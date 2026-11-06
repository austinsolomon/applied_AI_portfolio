"""The adaptive sourcing agent, driven by a replayed SDK session against fake providers."""

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from claude_agent_sdk import ResultMessage
from pydantic import SecretStr

import leadgen.sourcing_agent as sourcing_agent
from leadgen.budget import plan_spend, reserve
from leadgen.orchestrator import Pipeline
from leadgen.plans import PlannedCell, approve_plan, create_plan, read_plan, start_run
from leadgen.raw_leads import RawLead, persist_leads
from leadgen.settings import Settings
from leadgen.sourcing_agent import (
    EXA_TOOL,
    MAPS_TOOL,
    MAX_NEW_MAPS_CELLS,
    STATUS_TOOL,
    SourcingAgent,
    adapt_sourcing,
    adaptive_ceiling,
)
from leadgen.sourcing_maps import SourcingError
from tests.crm_builders import add_market

CITY = "Los Angeles, CA"


@pytest.fixture
def running(migrated_connection):
    connection = migrated_connection
    market_id = add_market(connection)
    plan = create_plan(
        connection, [PlannedCell(market_id, CITY, "financial planner", 5, Decimal("1"))]
    )
    approve_plan(connection, plan.plan_id, Decimal("5"), "test operator")
    return connection, plan, start_run(connection, plan.plan_id)


def finished_maps():
    """An Apify client whose every run has already finished with one planner's office."""
    item = {
        "placeId": "place-oak",
        "title": "Oak Planning",
        "phone": "3105550177",
        "website": "https://oakplanning.com",
        "categoryName": "Financial planner",
        "location": {"lat": 34.05, "lng": -118.24},
    }
    actor_run = {
        "id": "apify-offline",
        "status": "SUCCEEDED",
        "defaultDatasetId": "dataset-offline",
        "usageTotalUsd": 0.02,
    }
    client = Mock()
    client.actor.return_value.start.return_value = actor_run
    client.run.return_value.get.return_value = actor_run
    client.run.return_value.resurrect.return_value = actor_run
    client.dataset.return_value.list_items.return_value = SimpleNamespace(items=[item])
    client.dataset.return_value.iterate_items.side_effect = lambda: iter([item])
    return client


def exa_with(*urls_and_titles):
    client = Mock()
    client.search.return_value = SimpleNamespace(
        cost_dollars={"total": 0.007},
        results=[SimpleNamespace(url=url, title=title, text="") for url, title in urls_and_titles],
    )
    return client


def pipeline_for(connection, run, *, exa=None, maps=None):
    settings = Settings(anthropic_api_key=SecretStr("offline-only"))
    return Pipeline(connection, run, settings, maps=maps or finished_maps(), exa=exa or exa_with())


def replay(calls, *, cost=0.004):
    """An SDK session that makes `calls` (tool name, arguments): hook first, then the handler,
    as the real SDK does. Records each reply."""
    replies = []

    async def session(*, prompt, options):
        hook = options.hooks["PreToolUse"][0].hooks[0]
        tools = {
            f"mcp__sourcing__{item.name}": item for item in options.mcp_servers["sourcing"]["tools"]
        }
        assert options.tools == [] and set(options.allowed_tools) == set(tools)
        for name, arguments in calls:
            verdict = await hook({"tool_name": name, "tool_input": arguments}, None, None)
            if verdict["hookSpecificOutput"]["permissionDecision"] == "deny":
                replies.append(
                    ("denied", verdict["hookSpecificOutput"]["permissionDecisionReason"])
                )
                continue
            replies.append(("ran", await tools[name].handler(arguments)))
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=len(calls),
            session_id="offline",
            total_cost_usd=cost,
            usage={},
            result="Tried another keyword and a plainer Exa phrasing.",
        )

    return session, replies


@pytest.fixture(autouse=True)
def inspectable_tool_server(monkeypatch):
    monkeypatch.setattr(sourcing_agent, "create_sdk_mcp_server", lambda **kwargs: kwargs)


def test_agent_is_not_started_when_planned_sourcing_met_its_target(running):
    connection, plan, run = running
    persist_leads(
        connection,
        run.run_id,
        plan.cells[0].market_id,
        "exa",
        [RawLead(f"planner{n}.com", f"Planner {n}") for n in range(5)],
    )
    connection.execute("UPDATE scrape_plan_cells SET status='scraped'")
    session, _ = replay([])
    pipeline = pipeline_for(connection, run)
    result = asyncio.run(adapt_sourcing(pipeline, query_fn=session))
    assert result == {"agent": "not needed", "sourced": 5}
    assert plan_spend(connection, plan.plan_id) == 0


def test_agent_adds_a_maps_cell_and_an_exa_search_within_the_approval(running):
    connection, plan, run = running
    exa = exa_with(("https://birchwealth.com", "Birch Financial Planning"))
    session, replies = replay(
        [
            (STATUS_TOOL, {}),
            (MAPS_TOOL, {"city": CITY, "keyword": "certified financial planner"}),
            (EXA_TOOL, {"query": f"independent fee-only financial planners in {CITY}"}),
        ]
    )
    result = asyncio.run(adapt_sourcing(pipeline_for(connection, run, exa=exa), query_fn=session))

    assert [kind for kind, _ in replies] == ["ran", "ran", "ran"]
    assert result["maps_cells_added"] == 1 and result["exa_searches_added"] == 1
    assert result["sourced_added"] == 2 and result["denied"] == 0
    added = [cell for cell in read_plan(connection, plan.plan_id).cells if cell.origin == "agent"]
    assert [(cell.city, cell.keyword) for cell in added] == [(CITY, "certified financial planner")]
    assert exa.search.call_args.args[0] == f"independent fee-only financial planners in {CITY}"
    endpoints = {
        row[0] for row in connection.execute("SELECT endpoint FROM cost_ledger").fetchall()
    }
    assert {"sourcing-agent", "search", "compass/crawler-google-places"} <= endpoints


@pytest.mark.parametrize(
    "call,reason",
    [
        ((MAPS_TOOL, {"city": "Reno, NV", "keyword": "financial planner"}), "not in the approved"),
        ((MAPS_TOOL, {"city": CITY, "keyword": "tax preparer"}), "not one of the category"),
        ((MAPS_TOOL, {"city": CITY, "keyword": "financial planner"}), "already tried"),
        ((EXA_TOOL, {"query": "financial planners near me"}), "must name one of"),
        (("Bash", {"command": "ls"}), "outside the sourcing agent"),
    ],
)
def test_hook_denies_calls_outside_the_approval(running, call, reason):
    connection, _plan, run = running
    session, replies = replay([call])
    asyncio.run(adapt_sourcing(pipeline_for(connection, run), query_fn=session))
    assert replies[0][0] == "denied" and reason in replies[0][1]


def test_allowances_and_budget_cap_what_the_agent_can_add(running):
    connection, plan, run = running
    agent = SourcingAgent(pipeline_for(connection, run))
    agent.maps_added = MAX_NEW_MAPS_CELLS
    assert "allowance used" in agent.refusal(
        MAPS_TOOL, {"city": CITY, "keyword": "financial advisor"}
    )
    reserve(connection, run.run_id, "held:elsewhere", Decimal("4.99"))
    assert "budget cannot cover" in agent.refusal(EXA_TOOL, {"query": f"planners in {CITY}"})
    assert adaptive_ceiling() == Decimal("1.17")


def test_a_provider_failure_inside_a_tool_fails_the_stage(running):
    connection, _plan, run = running
    exa = Mock()
    exa.search.side_effect = RuntimeError("Exa is down")
    session, replies = replay([(EXA_TOOL, {"query": f"financial planners in {CITY}"})])
    with pytest.raises(SourcingError, match="Exa search failed"):
        asyncio.run(adapt_sourcing(pipeline_for(connection, run, exa=exa), query_fn=session))
    assert replies[0][0] == "ran"
