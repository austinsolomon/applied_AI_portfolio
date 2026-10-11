"""Offline end-to-end contracts and real-Postgres hardening acceptance tests."""

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import psycopg
import pytest
from claude_agent_sdk import ResultMessage
from pydantic import SecretStr

from leadgen.budget import BudgetBlocked, paid_operation, plan_spend, reserve
from leadgen.category_config import load_category_config
from leadgen.orchestrator import STAGES, TOOL_NAME, Pipeline, budget_hook, run_plan
from leadgen.plans import PlannedCell, approve_plan, create_plan, start_run
from leadgen.qualification import Judgment, judge_lead, validate_judgment
from leadgen.raw_leads import RawLead, persist_leads, read_raw_leads
from leadgen.settings import Settings
from leadgen.sourcing_exa import select_seeds, source_exa
from leadgen.sourcing_maps import grade_sample, source_maps
from tests.crm_builders import add_lead, add_market


@pytest.fixture
def approved(migrated_connection):
    connection = migrated_connection
    market_id = add_market(connection)
    plan = create_plan(
        connection, [PlannedCell(market_id, "Los Angeles, CA", "mobile massage", 50, Decimal("1"))]
    )
    approve_plan(connection, plan.plan_id, Decimal("5"), "test operator")
    return connection, plan


@pytest.fixture
def running(approved):
    connection, plan = approved
    return connection, plan, start_run(connection, plan.plan_id)


def test_reservation_rejects_foreign_cell_over_budget_and_duplicate(running):
    connection, plan, run = running
    with pytest.raises(BudgetBlocked, match="outside"):
        reserve(connection, run.run_id, "maps:bad", 1, cell_id=9999)
    with pytest.raises(BudgetBlocked, match="exceed"):
        reserve(connection, run.run_id, "maps:cost", 6, cell_id=plan.cells[0].cell_id)
    reserve(connection, run.run_id, "maps:one", 3, cell_id=plan.cells[0].cell_id)
    with pytest.raises(BudgetBlocked, match="exceed"):
        reserve(connection, run.run_id, "maps:two", 3)
    with pytest.raises(BudgetBlocked, match="already"):
        reserve(connection, run.run_id, "maps:one", 1)


def test_concurrent_reservations_cannot_both_spend_last_dollars(running, empty_database_url):
    from concurrent.futures import ThreadPoolExecutor

    connection, _plan, run = running

    def attempt(name):
        with psycopg.connect(empty_database_url, autocommit=True) as worker:
            try:
                reserve(worker, run.run_id, name, 3)
                return True
            except BudgetBlocked:
                return False

    with ThreadPoolExecutor(max_workers=2) as workers:
        assert sorted(workers.map(attempt, ["a", "b"])) == [False, True]


def test_ambiguous_call_keeps_reservation_and_prevents_paid_retry(running):
    connection, _plan, run = running
    with pytest.raises(TimeoutError):
        with paid_operation(connection, run.run_id, "uncertain", 4):
            raise TimeoutError()
    with pytest.raises(BudgetBlocked, match="already uncertain"):
        reserve(connection, run.run_id, "uncertain", 4)
    with pytest.raises(BudgetBlocked, match="exceed"):
        reserve(connection, run.run_id, "different", 2)


def test_model_allowance_independent_of_larger_plan_budget(running):
    connection, _plan, run = running
    reserve(connection, run.run_id, "sdk:orchestrator", Decimal(".08"))
    with pytest.raises(BudgetBlocked, match="Anthropic"):
        reserve(connection, run.run_id, "judge:lead", Decimal(".70"))


def test_unsupported_site_records_conservative_cost_without_blocking_resume(running):
    import httpx
    import respx

    from leadgen.orchestrator import GuardedWeb
    from leadgen.web_research import FirecrawlBackend, PageFetchFailed, WebResearcher

    connection, _plan, run = running
    settings = Settings()
    with respx.mock() as mock, httpx.Client() as http:
        mock.post("https://api.firecrawl.dev/v2/scrape").respond(403)
        backend = FirecrawlBackend(
            api_key="test-only",
            usd_per_credit=settings.firecrawl_usd_per_credit,
            http_client=http,
        )
        web = GuardedWeb(
            connection, run.run_id, WebResearcher(connection, backend, run.run_id), settings, "test"
        )
        with pytest.raises(PageFetchFailed, match="does not support"):
            web.fetch_page("https://example.com")
    assert connection.execute("SELECT status FROM budget_reservations").fetchone() == ("succeeded",)
    assert connection.execute("SELECT cost_usd,cost_basis FROM cost_ledger").fetchone() == (
        settings.firecrawl_usd_per_credit,
        "estimated",
    )


def test_hook_denies_unknown_tool_and_out_of_order_stage(running):
    connection, plan, run = running
    hook = budget_hook(connection, run.run_id, plan.plan_id)
    for name, stage, expected in [
        ("Bash", "maps", "deny"),
        (TOOL_NAME, "crm", "deny"),
        (TOOL_NAME, "maps", "allow"),
    ]:
        verdict = asyncio.run(hook({"tool_name": name, "tool_input": {"stage": stage}}))
        assert verdict["hookSpecificOutput"]["permissionDecision"] == expected


def fake_maps():
    item = {
        "placeId": "place-cedar",
        "title": "Cedar Mobile Massage",
        "phone": "3105550142",
        "website": "https://cedar.example",
        "categoryName": "Massage therapist",
        "location": {"lat": 34.05, "lng": -118.24},
    }
    actor_run = {
        "id": "apify-offline",
        "status": "RUNNING",
        "defaultDatasetId": "dataset-offline",
        "usageTotalUsd": 0.12,
    }
    client = Mock()
    client.actor.return_value.start.return_value = actor_run
    client.run.return_value.get.side_effect = [actor_run, {**actor_run, "status": "ABORTED"}]
    client.run.return_value.abort.return_value = {**actor_run, "status": "ABORTED"}
    client.dataset.return_value.list_items.return_value = SimpleNamespace(items=[item])
    client.dataset.return_value.iterate_items.return_value = iter([item])
    return client


def test_aborted_scraper_persists_and_repersist_adds_nothing(running):
    connection, plan, run = running
    client = fake_maps()
    leads = source_maps(
        connection, run.run_id, plan.cells[0], client, limit=1, sleep=lambda _: None
    )
    client.run.return_value.abort.assert_called_once()
    assert len(read_raw_leads(connection, run.run_id)) == 1
    assert persist_leads(connection, run.run_id, plan.cells[0].market_id, "google_maps", leads) == 0
    assert plan_spend(connection, plan.plan_id) == Decimal(".12")
    assert client.actor.return_value.start.call_args.kwargs["max_total_charge_usd"] == 1


def test_grader_does_not_treat_unknown_coordinates_as_in_market(running):
    connection, plan, run = running
    lead = RawLead("cedar", "Cedar", categories=["Massage therapist"])
    assert grade_sample(connection, run.run_id, plan.cells[0], [lead]) == "RECONFIGURE"


def test_exa_drops_shared_domains_and_counts_consensus(running):
    connection, plan, run = running
    client = Mock()
    client.find_similar.return_value = SimpleNamespace(
        cost_dollars=SimpleNamespace(total=0.005),
        results=[
            SimpleNamespace(url="https://cedar-massage.example", title="Cedar Massage"),
            SimpleNamespace(url="https://yelp.com/massage", title="Massage directory"),
        ],
    )
    leads = source_exa(
        connection,
        run.run_id,
        plan.cells[0].market_id,
        load_category_config(),
        client,
        seeds=["https://one.example", "https://two.example"],
    )
    assert len(leads) == 1
    assert leads[0].seed_consensus == 2
    assert read_raw_leads(connection, run.run_id)[0]["seed_consensus"] == 2
    assert plan_spend(connection, plan.plan_id) == Decimal(".01")


def test_seed_selection_excludes_directories_and_demo_domains(running):
    connection, plan, _run = running
    market_id = plan.cells[0].market_id
    add_lead(
        connection,
        market_id,
        business_name="Cedar Massage",
        website="https://cedar.example",
        phone="3105550142",
        qualification_reason="mobile massage",
    )
    assert select_seeds(connection, market_id, load_category_config()) == []


@pytest.mark.parametrize(
    "quote,url,market,expected",
    [
        ("We provide mobile massage in Los Angeles.", "https://cedar.example", "yes", "qualified"),
        ("Invented quote", "https://cedar.example", "yes", "review"),
        ("We provide mobile massage in Los Angeles.", "https://wrong.example", "yes", "review"),
        ("We provide mobile massage in Los Angeles.", "https://cedar.example", "no", "rejected"),
        ("We provide mobile massage in Los Angeles.", "https://cedar.example", "unclear", "review"),
    ],
)
def test_judge_requires_real_quote_url_and_market(quote, url, market, expected):
    judgment = Judgment(
        verdict="qualified",
        reason="Offers mobile massage",
        evidence_quote=quote,
        source_url=url,
        eligible_services=["mobile massage"],
        serves_market=market,
    )
    result = validate_judgment(
        judgment,
        {"https://cedar.example": "We provide mobile massage in Los Angeles."},
        load_category_config(),
    )
    assert result.verdict == expected


def test_qualification_records_model_usage_and_evidence(running):
    connection, plan, run = running
    persist_leads(
        connection,
        run.run_id,
        plan.cells[0].market_id,
        "google_maps",
        [RawLead("cedar", "Cedar Massage", phone="3105550142")],
    )
    lead = read_raw_leads(connection, run.run_id)[0]
    client = Mock()
    client.messages.parse.return_value = SimpleNamespace(
        id="offline-request",
        usage=SimpleNamespace(input_tokens=100, output_tokens=30),
        parsed_output=Judgment(
            verdict="qualified",
            reason="Mobile service",
            evidence_quote="Mobile",
            source_url="https://cedar.example",
            eligible_services=["mobile massage"],
            serves_market="yes",
        ),
    )
    settings = Settings()
    result = judge_lead(
        connection,
        run.run_id,
        lead,
        "Los Angeles",
        {"https://cedar.example": "Mobile"},
        load_category_config(),
        settings,
        client=client,
    )
    assert result.verdict == "qualified"
    assert connection.execute("SELECT count(*) FROM eval_records").fetchone()[0] == 1
    assert plan_spend(connection, plan.plan_id) > 0


def test_pipeline_dedupe_and_crm_share_real_schema(running):
    connection, plan, run = running
    market_id = plan.cells[0].market_id
    add_lead(connection, market_id, business_name="Already known", phone="(310) 555-0142")
    persist_leads(
        connection,
        run.run_id,
        market_id,
        "google_maps",
        [
            RawLead("dup", "Duplicate", phone="+1 310.555.0142"),
            RawLead("new", "Cedar", phone="3105550143"),
        ],
    )
    pipeline = Pipeline(connection, run, Settings())
    assert asyncio.run(pipeline.run_dedupe()) == {"duplicates": 1, "new": 1}
    connection.execute(
        "UPDATE raw_leads SET qualification=%s::jsonb",
        (
            '{"verdict":"qualified","reason":"Mobile massage","evidence_quote":"Mobile",'
            '"source_url":"https://cedar.example"}',
        ),
    )
    assert asyncio.run(pipeline.run_crm())["inserted"] == 1
    assert asyncio.run(pipeline.run_crm())["conflicts"] == 1
    assert (
        connection.execute("SELECT run_id FROM crm_leads WHERE phone='3105550143'").fetchone()[0]
        == run.run_id
    )


def test_sdk_runner_executes_stages_and_rerun_spends_nothing(approved, monkeypatch):
    import leadgen.orchestrator as orchestrator

    connection, plan = approved
    calls = []

    class ReplayPipeline(Pipeline):
        async def execute(self, stage):
            from psycopg.types.json import Jsonb

            calls.append(stage)
            result = {"replayed": True}
            connection.execute(
                "INSERT INTO pipeline_stages(run_id,stage,result) VALUES(%s,%s,%s)",
                (self.run.run_id, stage, Jsonb(result)),
            )
            return result

    monkeypatch.setattr(orchestrator, "create_sdk_mcp_server", lambda **kwargs: kwargs)

    async def replay(*, prompt, options):
        handler = options.mcp_servers["leadgen"]["tools"][0].handler
        assert options.tools == []
        assert options.max_budget_usd <= 0.08
        for stage in STAGES:
            await handler({"stage": stage})
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=7,
            session_id="offline",
            total_cost_usd=0.001,
            usage={},
        )

    settings = Settings(anthropic_api_key=SecretStr("offline-only"))
    first = asyncio.run(
        run_plan(
            connection, plan.plan_id, settings, query_fn=replay, pipeline_factory=ReplayPipeline
        )
    )
    second = asyncio.run(
        run_plan(
            connection, plan.plan_id, settings, query_fn=replay, pipeline_factory=ReplayPipeline
        )
    )
    assert calls == list(STAGES)
    assert first["run_id"] == second["run_id"]
    assert second["already_finished"]
    assert plan_spend(connection, plan.plan_id) == Decimal(".001")


def test_real_pipeline_end_to_end_with_fake_providers(approved, empty_database_url, monkeypatch):
    from datetime import UTC, datetime

    import leadgen.orchestrator as orchestrator
    from leadgen.web_research import FetchedPage, WebResearcher

    connection, plan = approved
    connection.execute("UPDATE scrape_plan_cells SET expected_raw_leads=1")
    maps = fake_maps()
    judge = Mock()
    judge.messages.parse.return_value = SimpleNamespace(
        id="offline",
        usage=SimpleNamespace(input_tokens=100, output_tokens=30),
        parsed_output=Judgment(
            verdict="qualified",
            reason="Travels to homes in the market",
            evidence_quote="Mobile massage in Los Angeles",
            source_url="https://cedar.example",
            eligible_services=["mobile massage"],
            serves_market="yes",
        ),
    )

    def page(_self, url):
        return FetchedPage(
            url,
            "https://cedar.example",
            "Cedar",
            "Mobile massage in Los Angeles",
            200,
            "free",
            datetime.now(UTC),
        )

    monkeypatch.setattr(WebResearcher, "fetch_page", page)
    monkeypatch.setattr(WebResearcher, "search", lambda *args, **kwargs: [])
    monkeypatch.setattr(orchestrator, "maps_client", lambda settings: maps)
    monkeypatch.setattr(orchestrator, "exa_client", lambda settings: Mock())
    monkeypatch.setattr(orchestrator, "create_sdk_mcp_server", lambda **kwargs: kwargs)

    async def replay(*, prompt, options):
        for stage in STAGES:
            await options.mcp_servers["leadgen"]["tools"][0].handler({"stage": stage})
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=7,
            session_id="offline",
            total_cost_usd=0.001,
            usage={},
        )

    def pipeline(connection, run, settings):
        return Pipeline(connection, run, settings, judge=judge)

    settings = Settings(
        _env_file=None, database_url=empty_database_url, anthropic_api_key=SecretStr("offline")
    )
    result = asyncio.run(
        run_plan(connection, plan.plan_id, settings, query_fn=replay, pipeline_factory=pipeline)
    )
    assert result["stages"]["maps"]["sourced"] == 1
    assert result["stages"]["enrich"]["processed"] == 1
    assert result["stages"]["qualify"]["judged"] == 1
    assert result["stages"]["crm"]["inserted"] == 1
    assert connection.execute("SELECT status FROM cell_jobs").fetchone()[0] == "succeeded"
    assert connection.execute("SELECT status FROM runs").fetchone()[0] == "finished"


def test_apify_cost_includes_events_before_usage_total_settles():
    from leadgen.sourcing_maps import apify_cost

    actor_run = {
        "usageTotalUsd": 0.0002,
        "chargedEventCounts": {"place-scraped": 50, "start": 4},
        "pricingInfo": {
            "pricingModel": "PAY_PER_EVENT",
            "pricingPerEvent": {
                "actorChargeEvents": {
                    "place-scraped": {"eventPriceUsd": 0.004},
                    "start": {"eventPriceUsd": 0.00005},
                }
            },
        },
    }
    assert apify_cost(actor_run) == Decimal(".2002")


def test_resume_refuses_unreconciled_paid_work(running):
    from leadgen.plans import finish_run

    connection, plan, run = running
    reserve(connection, run.run_id, "maps:pending", 1)
    finish_run(connection, run.run_id, "failed")
    settings = Settings(anthropic_api_key=SecretStr("offline"))
    with pytest.raises(BudgetBlocked, match="reconciled"):
        asyncio.run(run_plan(connection, plan.plan_id, settings, resume=True))


def test_reconciliation_retains_audit_and_releases_only_known_outcomes(running):
    from leadgen.budget import reconcile_operation

    connection, plan, run = running
    with pytest.raises(TimeoutError):
        with paid_operation(connection, run.run_id, "maps:rejected", 1):
            raise TimeoutError()
    reservation = connection.execute("SELECT reservation_id FROM budget_reservations").fetchone()[0]
    with pytest.raises(ValueError):
        reconcile_operation(connection, reservation, "rejected", "")
    reconcile_operation(
        connection, reservation, "rejected", "Provider confirms request was rejected"
    )
    reserve(connection, run.run_id, "maps:rejected", 1)
    assert connection.execute("SELECT count(*) FROM traces").fetchone()[0] == 2


def test_truncated_judge_is_retained_for_review_without_retry(running):
    connection, plan, run = running
    persist_leads(
        connection,
        run.run_id,
        plan.cells[0].market_id,
        "google_maps",
        [RawLead("truncated", "Incomplete decision")],
    )
    lead = read_raw_leads(connection, run.run_id)[0]
    client = Mock()
    client.messages.parse.return_value = SimpleNamespace(
        id="truncated",
        usage=SimpleNamespace(input_tokens=100, output_tokens=1400),
        parsed_output=None,
    )
    judgment = judge_lead(
        connection,
        run.run_id,
        lead,
        "Phoenix",
        {"https://cedar.example": "Mobile massage"},
        load_category_config(),
        Settings(),
        client=client,
    )
    assert judgment.verdict == "review"
    client.messages.parse.assert_called_once()
    assert connection.execute("SELECT status FROM budget_reservations").fetchone()[0] == "succeeded"


def test_research_agent_only_runs_for_review(running):
    from leadgen.research_agent import research_lead

    connection, plan, run = running
    lead = {"qualification": {"verdict": "qualified"}}
    query = Mock(side_effect=AssertionError("Must not start an agent"))
    result = asyncio.run(
        research_lead(
            connection,
            run.run_id,
            lead,
            "Phoenix",
            {},
            load_category_config(),
            Settings(),
            Mock(),
            query_fn=query,
        )
    )
    assert result == {"verdict": "qualified"}
    query.assert_not_called()


@pytest.mark.parametrize("subtype", ["success", "error_max_turns"])
def test_research_loop_rejudges_bounded_evidence(running, monkeypatch, subtype):
    import leadgen.research_agent as research

    connection, plan, run = running
    persist_leads(
        connection,
        run.run_id,
        plan.cells[0].market_id,
        "google_maps",
        [RawLead("research", "Cedar Massage", phone="3105550142")],
    )
    lead = read_raw_leads(connection, run.run_id)[0]
    lead["qualification"] = {"verdict": "review"}
    web = Mock()
    web.search.return_value = [
        SimpleNamespace(url="https://cedar.example", description="Mobile massage")
    ]
    monkeypatch.setattr(research, "create_sdk_mcp_server", lambda **kwargs: kwargs)

    async def replay(*, prompt, options):
        search, fetch = options.mcp_servers["research"]["tools"]
        await search.handler({"query": "Cedar mobile massage"})
        denied = await fetch.handler({"url": "http://localhost/"})
        assert denied["isError"]
        yield ResultMessage(
            subtype=subtype,
            duration_ms=1,
            duration_api_ms=1,
            is_error=subtype != "success",
            num_turns=2,
            session_id="offline",
            total_cost_usd=0.001,
            usage={},
        )

        raise AssertionError("The final result must close the SDK stream")

    judge = Mock()
    judge.messages.parse.return_value = SimpleNamespace(
        id="research",
        usage=SimpleNamespace(input_tokens=100, output_tokens=30),
        parsed_output=Judgment(
            verdict="qualified",
            reason="Offers mobile massage",
            evidence_quote="Mobile massage",
            source_url="https://cedar.example",
            eligible_services=["mobile massage"],
            serves_market="yes",
        ),
    )
    result = asyncio.run(
        research.research_lead(
            connection,
            run.run_id,
            lead,
            "Phoenix",
            {},
            load_category_config(),
            Settings(anthropic_api_key=SecretStr("offline")),
            web,
            query_fn=replay,
            judge_client=judge,
        )
    )
    assert result.verdict == "qualified"
    web.fetch_page.assert_not_called()
    assert read_raw_leads(connection, run.run_id)[0]["researched"]
