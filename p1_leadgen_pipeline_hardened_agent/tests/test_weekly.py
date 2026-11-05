"""`leadgen weekly`: standing-policy approval and the unattended run, with fake providers."""

import asyncio
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
import respx
from claude_agent_sdk import ResultMessage
from pydantic import SecretStr

import leadgen.orchestrator as orchestrator
from leadgen.demo_seed import seed_demo_data
from leadgen.orchestrator import STAGES, Pipeline
from leadgen.places import load_places
from leadgen.qualification import Judgment
from leadgen.settings import Settings
from leadgen.weekly import (
    EXIT_ERROR,
    EXIT_NEEDS_HUMAN,
    EXIT_NOTHING_TO_DO,
    EXIT_OK,
    PolicyError,
    WeeklyPolicy,
    load_policy,
    policy_version,
    post_to_slack,
    run_weekly,
)
from tests.test_pipeline_acceptance import fake_maps

LOS_ANGELES = "Los Angeles, CA"


def policy(**overrides):
    values = {"budget_usd": Decimal("3"), "target_leads": 1, "market": LOS_ANGELES}
    return WeeklyPolicy(**{**values, **overrides})


@pytest.fixture
def demo(migrated_connection):
    seed_demo_data(migrated_connection)
    load_places(migrated_connection)
    return migrated_connection


def offline_settings(database_url, **overrides):
    return Settings(
        _env_file=None,
        database_url=database_url,
        anthropic_api_key=SecretStr("offline"),
        **overrides,
    )


@pytest.fixture
def fake_providers(monkeypatch):
    """Apify, Exa, the web and the judge answer offline; the SDK replays every stage in order."""
    from datetime import UTC, datetime

    from leadgen.web_research import FetchedPage, WebResearcher

    judge = Mock()
    judge.messages.parse.return_value = SimpleNamespace(
        id="offline",
        usage=SimpleNamespace(input_tokens=100, output_tokens=30),
        parsed_output=Judgment(
            verdict="qualified",
            reason="Independent firm with a CFP® planner serving the market",
            evidence_quote="Dana Reyes, CFP®, plans for families in Los Angeles",
            source_url="https://cedar.example",
            eligible_services=["financial planning"],
            serves_market="yes",
        ),
    )

    def page(_self, url):
        return FetchedPage(
            url,
            "https://cedar.example",
            "Cedar",
            "Dana Reyes, CFP®, plans for families in Los Angeles",
            200,
            "free",
            datetime.now(UTC),
        )

    exa = Mock()
    exa.search.return_value = SimpleNamespace(cost_dollars={"total": 0.007}, results=[])
    monkeypatch.setattr(WebResearcher, "fetch_page", page)
    monkeypatch.setattr(WebResearcher, "search", lambda *args, **kwargs: [])
    monkeypatch.setattr(orchestrator, "maps_client", lambda settings: fake_maps())
    monkeypatch.setattr(orchestrator, "exa_client", lambda settings: exa)
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

    return {"query_fn": replay, "pipeline_factory": pipeline}


def weekly(connection, settings, the_policy, **options):
    return asyncio.run(run_weekly(connection, settings, the_policy, "abc123def456", **options))


def test_policy_file_in_repo_is_valid_and_versioned():
    the_policy, version = load_policy(Path("config/weekly_policy.yaml"))
    assert the_policy.budget_usd > 0 and len(version) == 12


def test_policy_version_matches_git_blob_id():
    # `printf 'hello\n' | git hash-object --stdin` prints ce013625030ba8dba906f756967f9e9ca394464a
    assert policy_version(b"hello\n") == "ce013625030b"


@pytest.mark.parametrize(
    "text,problem",
    [
        ("budget_usd: 0\n", "budget_usd"),
        ("budget_usd: 3\nsurprise: 1\n", "surprise"),
        ("budget_usd: 3\nmax_cities: 0\n", "max_cities"),
        ("- not a mapping\n", "mapping"),
    ],
)
def test_invalid_policy_is_refused_naming_the_field(tmp_path, text, problem):
    path = tmp_path / "policy.yaml"
    path.write_text(text)
    with pytest.raises(PolicyError, match=problem):
        load_policy(path)


def test_unattended_run_is_approved_by_the_policy_and_completes(
    demo, empty_database_url, fake_providers
):
    settings = offline_settings(empty_database_url)
    exit_code, summary = weekly(demo, settings, policy(), **fake_providers)
    assert exit_code == EXIT_OK, summary
    assert summary["status"] == "finished"
    assert summary["approver"] == "policy:weekly@abc123def456"
    assert demo.execute("SELECT approved_by FROM approvals").fetchone() == (
        "policy:weekly@abc123def456",
    )
    assert summary["sourced"] == {"google_maps": 1, "exa": 0}
    assert (summary["qualified"], summary["crm_inserted"]) == (1, 1)
    assert Decimal(summary["spend_usd"]) <= Decimal(summary["budget_usd"])
    assert set(summary["spend_by_service"]) >= {"apify", "exa", "claude"}
    stored = demo.execute("SELECT details FROM traces WHERE operation='weekly'").fetchone()[0]
    assert stored["run_id"] == summary["run_id"]


def test_second_trigger_within_the_cadence_spends_nothing(demo, empty_database_url, fake_providers):
    settings = offline_settings(empty_database_url)
    assert weekly(demo, settings, policy(), **fake_providers)[0] == EXIT_OK
    before = demo.execute("SELECT count(*), sum(cost_usd) FROM cost_ledger").fetchone()
    exit_code, summary = weekly(demo, settings, policy(), **fake_providers)
    assert exit_code == EXIT_NOTHING_TO_DO and "within 6 days" in summary["reason"]
    assert demo.execute("SELECT count(*), sum(cost_usd) FROM cost_ledger").fetchone() == before
    assert demo.execute("SELECT count(*) FROM scrape_plans").fetchone() == (1,)


def test_plan_over_policy_budget_is_refused_and_never_approved(demo, empty_database_url):
    exit_code, summary = weekly(
        demo, offline_settings(empty_database_url), policy(budget_usd=Decimal("1"))
    )
    assert exit_code == EXIT_NEEDS_HUMAN and "policy budget" in summary["reason"]
    assert demo.execute("SELECT count(*) FROM approvals").fetchone() == (0,)


def test_unreconciled_paid_call_blocks_the_next_run(demo, empty_database_url):
    from leadgen.budget import reserve
    from leadgen.plans import PlannedCell, approve_plan, create_plan, finish_run, start_run

    plan = create_plan(demo, [PlannedCell(1, "Phoenix, AZ", "financial planner", 1, Decimal("1"))])
    approve_plan(demo, plan.plan_id, Decimal("3"), "operator")
    run = start_run(demo, plan.plan_id)
    reserve(demo, run.run_id, "exa:uncertain", Decimal("0.03"))
    demo.execute("UPDATE budget_reservations SET status='uncertain'")
    finish_run(demo, run.run_id, "failed")
    exit_code, summary = weekly(demo, offline_settings(empty_database_url), policy())
    assert exit_code == EXIT_NEEDS_HUMAN and "reconciled" in summary["reason"]


def test_run_still_marked_running_blocks_the_next_run(demo, empty_database_url):
    from leadgen.plans import PlannedCell, approve_plan, create_plan, start_run

    plan = create_plan(demo, [PlannedCell(1, "Phoenix, AZ", "financial planner", 1, Decimal("1"))])
    approve_plan(demo, plan.plan_id, Decimal("3"), "operator")
    start_run(demo, plan.plan_id)
    exit_code, summary = weekly(demo, offline_settings(empty_database_url), policy())
    assert exit_code == EXIT_NEEDS_HUMAN and "still marked running" in summary["reason"]


def test_pending_migration_blocks_and_is_never_applied(demo, empty_database_url):
    demo.execute(
        "DELETE FROM schema_migrations WHERE version = (SELECT max(version) FROM schema_migrations)"
    )
    exit_code, summary = weekly(demo, offline_settings(empty_database_url), policy())
    assert exit_code == EXIT_NEEDS_HUMAN and "leadgen db migrate" in summary["reason"]


def test_nothing_to_do_when_every_market_is_at_goal(migrated_connection, empty_database_url):
    exit_code, summary = weekly(
        migrated_connection, offline_settings(empty_database_url), policy(market=None)
    )
    assert exit_code == EXIT_NOTHING_TO_DO and "goal" in summary["reason"]


def test_dry_run_shows_the_plan_and_stores_nothing(demo, empty_database_url):
    exit_code, summary = weekly(demo, offline_settings(empty_database_url), policy(), dry_run=True)
    assert exit_code == EXIT_OK and summary["status"] == "dry run: would run"
    assert summary["market"] == LOS_ANGELES and summary["cities"] == [LOS_ANGELES]
    assert demo.execute("SELECT count(*) FROM scrape_plans").fetchone() == (0,)


def test_failed_pipeline_reports_error_with_spend(demo, empty_database_url, fake_providers):
    async def broken(*, prompt, options):
        raise RuntimeError("SDK session ended")
        yield  # pragma: no cover

    exit_code, summary = weekly(
        demo,
        offline_settings(empty_database_url),
        policy(),
        query_fn=broken,
        pipeline_factory=fake_providers["pipeline_factory"],
    )
    assert exit_code == EXIT_ERROR and summary["status"] == "failed"
    assert "SDK session ended" in summary["error"]


def test_slack_post_is_best_effort():
    summary = {"status": "finished", "market": LOS_ANGELES, "spend_usd": "1", "budget_usd": "3"}
    with respx.mock() as mock, httpx.Client() as http:
        route = mock.post("https://hooks.slack.test/abc").respond(200)
        assert post_to_slack("https://hooks.slack.test/abc", summary, http_client=http)
        assert "Los Angeles" in json.loads(route.calls[0].request.content)["text"]
        mock.post("https://hooks.slack.test/down").mock(side_effect=httpx.ConnectError("down"))
        assert not post_to_slack("https://hooks.slack.test/down", summary, http_client=http)
