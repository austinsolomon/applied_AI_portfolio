"""Offline end-to-end contracts and real-Postgres hardening acceptance tests."""

import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, call

import psycopg
import pytest
from claude_agent_sdk import ResultMessage
from pydantic import SecretStr

from leadgen.audit import ACTORS
from leadgen.budget import BudgetBlocked, paid_operation, plan_spend, record_cost, reserve
from leadgen.category_config import load_category_config
from leadgen.orchestrator import (
    STAGES,
    Pipeline,
    build_plan,
    exa_calls,
    run_plan,
    stage_gate,
)
from leadgen.plans import PlannedCell, approve_plan, create_plan, start_run
from leadgen.qualification import Judgment, judge_lead, validate_judgment
from leadgen.raw_leads import RawLead, persist_leads, read_raw_leads
from leadgen.settings import Settings
from leadgen.sourcing_exa import source_exa
from leadgen.sourcing_maps import grade_sample, source_maps
from tests.crm_builders import add_lead, add_market


@pytest.fixture
def approved(migrated_connection):
    connection = migrated_connection
    market_id = add_market(connection)
    plan = create_plan(
        connection,
        [PlannedCell(market_id, "Los Angeles, CA", "financial planner", 50, Decimal("1"))],
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


@pytest.mark.parametrize("failure", ["503", "timeout"])
def test_unavailable_page_settles_its_reservation_at_the_ceiling(running, failure):
    """A Firecrawl 5xx or timeout is skipped by evidence gathering, so it must not leave an
    uncertain reservation behind to block the next run (2026-11-05, reservation 185)."""
    import httpx
    import respx

    from leadgen.orchestrator import GuardedWeb
    from leadgen.web_research import FirecrawlBackend, WebBackendUnavailable, WebResearcher

    connection, _plan, run = running
    settings = Settings()
    with respx.mock() as mock, httpx.Client() as http:
        route = mock.post("https://api.firecrawl.dev/v2/scrape")
        if failure == "503":
            route.respond(503, json={"error": "busy"})
        else:
            route.side_effect = httpx.ReadTimeout("timed out")
        backend = FirecrawlBackend(
            api_key="test-only",
            usd_per_credit=settings.firecrawl_usd_per_credit,
            http_client=http,
        )
        web = GuardedWeb(
            connection, run.run_id, WebResearcher(connection, backend, run.run_id), settings, "test"
        )
        with pytest.raises(WebBackendUnavailable):
            web.fetch_page("https://example.com")
    assert connection.execute("SELECT status FROM budget_reservations").fetchone() == ("succeeded",)
    assert connection.execute("SELECT service,cost_usd,cost_basis FROM cost_ledger").fetchall() == [
        ("firecrawl", settings.firecrawl_usd_per_credit, "estimated")
    ]


def test_exa_page_reserves_exa_ceiling_plus_one_firecrawl_render(running):
    from leadgen.orchestrator import GuardedWeb
    from leadgen.web_research import (
        EXA_WEB_CEILING_PER_CALL,
        ExaWebBackend,
        FirecrawlBackend,
        PageNeedsRendering,
        WebResearcher,
    )

    connection, _plan, run = running
    settings = Settings()
    exa = Mock()
    exa.get_contents.return_value = SimpleNamespace(
        results=[SimpleNamespace(url="https://cedar.example", title="Cedar", text="Mobile")],
        cost_dollars=SimpleNamespace(total=0.001),
    )
    exa.search.return_value = SimpleNamespace(results=[], cost_dollars={"total": 0.005})
    researcher = WebResearcher(
        connection,
        ExaWebBackend(exa),
        run_id=run.run_id,
        fallback=FirecrawlBackend(
            "unused", usd_per_credit=settings.firecrawl_usd_per_credit, http_client=Mock()
        ),
        fallback_on=PageNeedsRendering,
        fallback_searches=False,
    )
    web = GuardedWeb(connection, run.run_id, researcher, settings, "test")
    web.fetch_page("https://cedar.example")
    web.search("cedar planning")
    assert connection.execute(
        "SELECT ceiling_usd FROM budget_reservations ORDER BY reservation_id"
    ).fetchall() == [
        (EXA_WEB_CEILING_PER_CALL + settings.firecrawl_usd_per_credit,),
        (EXA_WEB_CEILING_PER_CALL,),
    ]
    assert plan_spend(connection, _plan.plan_id) == Decimal("0.006")


def test_stage_gate_allows_only_the_next_stage_within_budget(running):
    connection, plan, run = running
    assert stage_gate(connection, run.run_id, plan.plan_id, "crm") == (
        "Stage is outside the approved sequence"
    )
    assert stage_gate(connection, run.run_id, plan.plan_id, "maps") is None
    record_cost(connection, run.run_id, "apify", Decimal("5"), endpoint="test", units=1)
    assert stage_gate(connection, run.run_id, plan.plan_id, "maps") == (
        "Approved budget is exhausted or missing"
    )


def fake_maps():
    item = {
        "placeId": "place-cedar",
        "title": "Cedar Financial Planning",
        "phone": "3105550142",
        "website": "https://cedar.example",
        "categoryName": "Financial planner",
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
    lead = RawLead("cedar", "Cedar", categories=["Financial planner"])
    assert grade_sample(connection, run.run_id, plan.cells[0], [lead]) == "RECONFIGURE"


def persist_consensus_mix(connection, run, market_id):
    persist_leads(
        connection,
        run.run_id,
        market_id,
        "google_maps",
        [RawLead("maps-1", "Maps Planner", phone="3105550101")],
    )
    persist_leads(
        connection,
        run.run_id,
        market_id,
        "exa",
        [
            RawLead(f"c{consensus}.example", f"Exa lead {consensus}", phone=f"310555011{n}")
            for n, consensus in enumerate([1, 3, 2])
        ],
    )
    for consensus in (1, 3, 2):
        connection.execute(
            "UPDATE raw_leads SET query_consensus=%s WHERE source_key=%s",
            (consensus, f"c{consensus}.example"),
        )


def test_paid_stages_work_highest_query_consensus_first(running, empty_database_url):
    connection, plan, run = running
    persist_consensus_mix(connection, run, plan.cells[0].market_id)
    sourced = [lead["source_key"] for lead in read_raw_leads(connection, run.run_id)]
    assert sourced == ["maps-1", "c1.example", "c3.example", "c2.example"]
    by_consensus = read_raw_leads(connection, run.run_id, by_consensus=True)
    expected = ["c3.example", "c2.example", "maps-1", "c1.example"]
    assert [lead["source_key"] for lead in by_consensus] == expected
    # Jobs are claimed in job_id order, so enqueue order is processing order.
    settings = Settings(_env_file=None, database_url=empty_database_url)
    asyncio.run(Pipeline(connection, run, settings).run_enrich())
    keys = dict(connection.execute("SELECT raw_lead_id,source_key FROM raw_leads").fetchall())
    jobs = connection.execute(
        "SELECT (payload->>'lead')::bigint FROM cell_jobs WHERE kind='enrich' ORDER BY job_id"
    ).fetchall()
    assert [keys[lead_id] for (lead_id,) in jobs] == expected


def exa_response(*urls_and_titles, cost=0.005, text=None):
    return SimpleNamespace(
        cost_dollars=None if cost is None else SimpleNamespace(total=cost),
        results=[
            SimpleNamespace(url=url, title=title, text=text) for url, title in urls_and_titles
        ],
    )


def test_exa_stage_runs_one_company_search_per_phrasing(running):
    connection, plan, run = running
    category = load_category_config()
    client = Mock()
    client.search.return_value = exa_response(("https://desertplanning.com/", "Desert Planning"))
    assert exa_calls(plan, category) == len(category.exa_queries)
    result = asyncio.run(Pipeline(connection, run, Settings(), exa=client).run_exa())
    assert result == {"sourced": 1, "queries": len(category.exa_queries)}
    first_query = category.exa_queries[0].format(city="Los Angeles, CA")
    assert client.search.call_args_list[0] == call(
        first_query,
        type="auto",
        category="company",
        num_results=25,
        exclude_domains=sorted(category.hosts_never_identifying_a_business),
        contents={"text": {"maxCharacters": 6000}},
    )
    assert "Los Angeles, CA" in first_query and "{city}" not in first_query
    assert connection.execute("SELECT DISTINCT endpoint FROM cost_ledger").fetchall() == [
        ("search",)
    ]


def test_exa_counts_how_many_phrasings_found_a_business(running):
    connection, plan, run = running
    category = load_category_config()
    client = Mock()
    client.search.return_value = exa_response(("https://desertplanning.com", "Desert Planning"))
    leads = source_exa(
        connection, run.run_id, plan.cells[0].market_id, category, client, cities=["Phoenix, AZ"]
    )
    phrasings = len(category.exa_queries)
    assert [lead.query_consensus for lead in leads] == [phrasings]
    assert read_raw_leads(connection, run.run_id)[0]["query_consensus"] == phrasings
    assert plan_spend(connection, plan.plan_id) == Decimal("0.005") * phrasings


def test_exa_drops_shared_and_aggregator_hosts(running):
    connection, plan, run = running
    client = Mock()
    client.search.return_value = exa_response(
        (
            "https://www.yelp.com/search?find_desc=financial+planner",
            "Best Financial Planners in Phoenix",
        ),
        ("https://instagram.com/cfpplanner", "Financial Planner"),
        ("https://cedar.smartasset.com", "Cedar Planning on SmartAsset"),
        ("https://www.edwardjones.com/us-en/advisor/jane-doe", "Jane Doe, CFP® financial advisor"),
        ("https://desertplanning.com", "Desert Planning"),
        ("https://desertplanning.com/about", "Desert Planning about"),
        ("https://phoenixplumbing.com", "Phoenix Plumbing"),
    )
    leads = source_exa(
        connection,
        run.run_id,
        plan.cells[0].market_id,
        load_category_config(),
        client,
        cities=["Phoenix, AZ"],
    )
    assert [lead.source_key for lead in leads] == ["desertplanning.com"]


def test_exa_page_text_is_cached_for_qualification(running):
    from leadgen.web_research import WebResearcher

    connection, plan, run = running
    client = Mock()
    client.search.return_value = exa_response(
        ("https://desertplanning.com", "Desert Planning"),
        text="Our CFP® professionals offer financial planning across Phoenix.",
    )
    source_exa(
        connection,
        run.run_id,
        plan.cells[0].market_id,
        load_category_config(),
        client,
        cities=["Phoenix, AZ"],
    )
    paid_backend = Mock()
    page = WebResearcher(connection, paid_backend, run_id=run.run_id).fetch_page(
        "https://desertplanning.com/"
    )
    assert (page.from_cache, page.backend) == (True, "exa")
    assert "financial planning" in page.text
    paid_backend.fetch_page.assert_not_called()


def test_exa_records_ceiling_when_cost_is_missing(running):
    connection, plan, run = running
    client = Mock()
    client.search.return_value = exa_response(cost=None)
    source_exa(
        connection,
        run.run_id,
        plan.cells[0].market_id,
        load_category_config(),
        client,
        cities=["Phoenix, AZ"],
        ceiling_per_query=Decimal("0.03"),
    )
    assert connection.execute(
        "SELECT service,endpoint,cost_usd,cost_basis FROM cost_ledger LIMIT 1"
    ).fetchone() == ("exa", "search", Decimal("0.03"), "estimated")


def test_exa_blocked_when_budget_is_exhausted(running):
    connection, plan, run = running
    reserve(connection, run.run_id, "held:elsewhere", Decimal("4.99"))
    client = Mock()
    with pytest.raises(BudgetBlocked, match="exceed"):
        source_exa(
            connection,
            run.run_id,
            plan.cells[0].market_id,
            load_category_config(),
            client,
            cities=["Phoenix, AZ"],
        )
    client.search.assert_not_called()


@pytest.mark.parametrize(
    "quote,url,market,expected",
    [
        (
            "Dana Reyes, CFP®, plans for families across Los Angeles.",
            "https://cedar.example",
            "yes",
            "qualified",
        ),
        ("Invented quote", "https://cedar.example", "yes", "review"),
        (
            "Dana Reyes, CFP®, plans for families across Los Angeles.",
            "https://wrong.example",
            "yes",
            "review",
        ),
        (
            "Dana Reyes, CFP®, plans for families across Los Angeles.",
            "https://cedar.example",
            "no",
            "rejected",
        ),
        (
            "Dana Reyes, CFP®, plans for families across Los Angeles.",
            "https://cedar.example",
            "unclear",
            "review",
        ),
    ],
)
def test_judge_requires_real_quote_url_and_market(quote, url, market, expected):
    judgment = Judgment(
        verdict="qualified",
        reason="Independent firm with a CFP® planner",
        evidence_quote=quote,
        source_url=url,
        eligible_services=["financial planning"],
        serves_market=market,
    )
    result = validate_judgment(
        judgment,
        {"https://cedar.example": "Dana Reyes, CFP®, plans for families across Los Angeles."},
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
        [RawLead("cedar", "Cedar Financial Planning", phone="3105550142")],
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
            eligible_services=["financial planning"],
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
    evidence = connection.execute("SELECT evidence FROM eval_records").fetchone()[0]
    assert (evidence["source"], evidence["query_consensus"]) == ("google_maps", 1)
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
    assert asyncio.run(pipeline.run_dedupe()) == {"duplicates": 1, "new": 1, "already_judged": 0}
    connection.execute(
        "UPDATE raw_leads SET qualification=%s::jsonb",
        (
            '{"verdict":"qualified","reason":"Independent CFP® planner","evidence_quote":"Mobile",'
            '"source_url":"https://cedar.example"}',
        ),
    )
    assert asyncio.run(pipeline.run_crm())["inserted"] == 1
    assert asyncio.run(pipeline.run_crm())["conflicts"] == 1
    assert (
        connection.execute("SELECT run_id FROM crm_leads WHERE phone='3105550143'").fetchone()[0]
        == run.run_id
    )


def test_runner_executes_stages_in_order_and_rerun_spends_nothing(approved):
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

    settings = Settings(anthropic_api_key=SecretStr("offline-only"))
    first = asyncio.run(
        run_plan(connection, plan.plan_id, settings, pipeline_factory=ReplayPipeline)
    )
    second = asyncio.run(
        run_plan(connection, plan.plan_id, settings, pipeline_factory=ReplayPipeline)
    )
    assert calls == list(STAGES)
    assert first["run_id"] == second["run_id"]
    assert second["already_finished"]
    assert plan_spend(connection, plan.plan_id) == 0


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

    monkeypatch.setattr(WebResearcher, "fetch_page", page)
    monkeypatch.setattr(WebResearcher, "search", lambda *args, **kwargs: [])
    monkeypatch.setattr(orchestrator, "maps_client", lambda settings: maps)
    exa = Mock()
    exa.search.return_value = SimpleNamespace(cost_dollars={"total": 0.005}, results=[])
    monkeypatch.setattr(orchestrator, "exa_client", lambda settings: exa)

    def pipeline(connection, run, settings):
        return Pipeline(connection, run, settings, judge=judge)

    settings = Settings(
        _env_file=None, database_url=empty_database_url, anthropic_api_key=SecretStr("offline")
    )
    result = asyncio.run(run_plan(connection, plan.plan_id, settings, pipeline_factory=pipeline))
    assert result["stages"]["maps"]["sourced"] == 1
    assert result["stages"]["exa"]["sourced"] == 0
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
        {"https://cedar.example": "Jane Doe, CFP®"},
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


def test_research_hook_denies_unfound_pages_and_calls_over_the_limit(running, monkeypatch):
    import leadgen.research_agent as research

    connection, plan, run = running
    persist_leads(
        connection,
        run.run_id,
        plan.cells[0].market_id,
        "google_maps",
        [RawLead("hooked", "Birch Planning", phone="3105550143")],
    )
    lead = read_raw_leads(connection, run.run_id)[0]
    lead["qualification"] = {"verdict": "review", "reason": "No CFP® credential named"}
    web = Mock()
    web.search.return_value = [SimpleNamespace(url="https://birch.example", description="Birch")]
    monkeypatch.setattr(research, "create_sdk_mcp_server", lambda **kwargs: kwargs)
    decisions = []

    async def replay(*, prompt, options):
        hook = options.hooks["PreToolUse"][0].hooks[0]
        assert "No CFP® credential named" in prompt
        calls = [(research.FETCH_TOOL, {"url": "https://unfound.example"})] + [
            (research.SEARCH_TOOL, {"query": f"Birch planning {n}"}) for n in range(5)
        ]
        tools = dict(
            zip(
                (research.SEARCH_TOOL, research.FETCH_TOOL),
                options.mcp_servers["research"]["tools"],
                strict=True,
            )
        )
        for name, arguments in calls:
            verdict = await hook({"tool_name": name, "tool_input": arguments}, None, None)
            decisions.append(verdict["hookSpecificOutput"]["permissionDecision"])
            if decisions[-1] == "allow":
                await tools[name].handler(arguments)
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=6,
            session_id="offline",
            total_cost_usd=0.001,
            usage={},
        )

    judge = Mock()
    judge.messages.parse.return_value = SimpleNamespace(
        id="research",
        usage=SimpleNamespace(input_tokens=100, output_tokens=30),
        parsed_output=None,
    )
    asyncio.run(
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
    assert decisions == ["deny", "allow", "allow", "allow", "allow", "deny"]
    assert web.search.call_count == research.MAX_TOOL_CALLS


@pytest.mark.parametrize("subtype", ["success", "error_max_turns"])
def test_research_loop_rejudges_bounded_evidence(running, monkeypatch, subtype):
    import leadgen.research_agent as research

    connection, plan, run = running
    persist_leads(
        connection,
        run.run_id,
        plan.cells[0].market_id,
        "google_maps",
        [RawLead("research", "Cedar Financial Planning", phone="3105550142")],
    )
    lead = read_raw_leads(connection, run.run_id)[0]
    lead["qualification"] = {"verdict": "review"}
    web = Mock()
    web.search.return_value = [
        SimpleNamespace(url="https://cedar.example", description="Jane Doe, CFP®")
    ]
    monkeypatch.setattr(research, "create_sdk_mcp_server", lambda **kwargs: kwargs)

    async def replay(*, prompt, options):
        search, fetch = options.mcp_servers["research"]["tools"]
        await search.handler({"query": "Cedar financial planning"})
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
            reason="Independent firm with a CFP® planner",
            evidence_quote="Jane Doe, CFP®",
            source_url="https://cedar.example",
            eligible_services=["financial planning"],
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


def test_plan_can_target_a_named_market_below_its_goal(migrated_connection):
    from leadgen.demo_seed import seed_demo_data
    from leadgen.places import load_places

    connection = migrated_connection
    seed_demo_data(connection)
    load_places(connection)
    category = load_category_config()
    assert build_plan(connection, category).cells[0].city == "Phoenix, AZ"
    plan = build_plan(connection, category, market="los angeles, ca", target=5)
    assert plan.cells[0].city == "Los Angeles, CA"
    with pytest.raises(ValueError, match="below its lead goal"):
        build_plan(connection, category, market="Nowhere, ZZ")


def test_audit_trail_records_one_execution_as_who_does_what_to_whom(
    approved, empty_database_url, monkeypatch
):
    monkeypatch.setenv("LEADGEN_AUDIT", "1")
    test_real_pipeline_end_to_end_with_fake_providers(approved, empty_database_url, monkeypatch)
    connection, plan = approved
    rows = connection.execute(
        "SELECT actor, action, target, plan_id, detail FROM audit_events ORDER BY audit_event_id"
    ).fetchall()
    assert all(plan_id == plan.plan_id for *_, plan_id, _detail in rows)
    trail = [f"{actor} | {action} | {target}" for actor, action, target, *_ in rows]

    def first(fragment):
        return next(index for index, line in enumerate(trail) if fragment in line)

    # The execution, in the order it happened.
    milestones = [
        "Postgres (State machine) | run ",
        "Postgres (State machine) | stage maps started",
        "Apify API (Google Maps) | starts the Google Maps scraper",
        "Python (Sample grader) | grades",
        "Postgres (State machine) | stage maps finished",
        "Exa API (Search) | company search returns",
        "Python (Contact extraction) | reads",
        "Postgres (Dedupe) | keeps",
        "Claude API (LLM call) | returns verdict",
        "Postgres (CRM upload) | inserts 1",
        "Postgres (State machine) | run finished",
    ]
    positions = [first(milestone) for milestone in milestones]
    assert positions == sorted(positions)
    assert not any("@" in line for line in trail), "no email addresses in the audit trail"
    # Every row comes from one of the 20 actors, and the trail stays readable: the same fake run
    # wrote 94 rows from 25 actors before the trail was cut down to major steps.
    assert {actor for actor, *_ in rows} <= set(ACTORS)
    assert len(rows) <= 50
    # Plain code runs the stages: no agent session and no hook rows unless an agent runs.
    assert not any(actor == "Claude Agent SDK (PreToolUse hook)" for actor, *_ in rows)
    # Granted reservations and accepted citations are not rows of their own.
    assert not any(actor == "Postgres (Budget reservation)" for actor, *_ in rows)
    assert not any(actor == "Python (Citation check)" for actor, *_ in rows)
    assert any(
        actor == "Claude API (LLM call)" and detail.get("citation") == "accepted"
        for actor, *_, detail in rows
    )
    # Spend is a detail on the API rows, not a row of its own.
    assert any(actor == "Exa API (Search)" and "cost_usd" in detail for actor, *_, detail in rows)


def test_audit_trail_is_off_unless_asked_for(approved, empty_database_url, monkeypatch):
    monkeypatch.delenv("LEADGEN_AUDIT", raising=False)
    test_real_pipeline_end_to_end_with_fake_providers(approved, empty_database_url, monkeypatch)
    connection, _plan = approved
    assert connection.execute("SELECT count(*) FROM audit_events").fetchone()[0] == 0


def test_malformed_judge_json_becomes_review_without_stopping_the_run(running):
    from pydantic import ValidationError

    connection, plan, run = running
    persist_leads(
        connection,
        run.run_id,
        plan.cells[0].market_id,
        "google_maps",
        [RawLead("cedar", "Cedar Financial Planning", phone="3105550142")],
    )
    lead = read_raw_leads(connection, run.run_id)[0]
    try:
        Judgment.model_validate_json('{"verdict":"review","eligible_services":["financial pla')
    except ValidationError as cut_off:
        malformed = cut_off
    client = Mock()
    client.messages.parse.side_effect = malformed
    result = judge_lead(
        connection,
        run.run_id,
        lead,
        "Los Angeles",
        {"https://cedar.example": "Mobile"},
        load_category_config(),
        Settings(),
        client=client,
        tier=2,
    )
    assert result.verdict == "review" and "incomplete JSON" in result.reason
    assert connection.execute("SELECT status FROM budget_reservations").fetchone() == ("succeeded",)
    assert connection.execute("SELECT cost_basis FROM cost_ledger").fetchone() == ("estimated",)


def test_review_stage_researches_unconfident_verdicts_after_qualification(
    running, empty_database_url, monkeypatch
):
    from datetime import UTC, datetime

    from leadgen.web_research import FetchedPage, WebResearcher

    connection, plan, run = running
    market_id = plan.cells[0].market_id
    persist_leads(
        connection,
        run.run_id,
        market_id,
        "exa",
        [
            RawLead(
                "unclear.com", "Unclear Planner", phone="3105550111", website="https://unclear.com"
            ),
            RawLead("clear.com", "Clear Planner", phone="3105550122", website="https://clear.com"),
        ],
    )

    def page(_self, url):
        return FetchedPage(
            url,
            url,
            "Planner",
            "Dana Reyes, CFP®, plans for families in Los Angeles",
            200,
            "free",
            datetime.now(UTC),
        )

    monkeypatch.setattr(WebResearcher, "fetch_page", page)
    monkeypatch.setattr(WebResearcher, "search", lambda *args, **kwargs: [])

    def decision(verdict, url):
        return SimpleNamespace(
            id="offline",
            usage=SimpleNamespace(input_tokens=100, output_tokens=30),
            parsed_output=Judgment(
                verdict=verdict,
                reason=f"{verdict} decision",
                evidence_quote="Dana Reyes, CFP®, plans for families in Los Angeles"
                if verdict == "qualified"
                else "",
                source_url=url if verdict == "qualified" else "",
                eligible_services=["financial planning"] if verdict == "qualified" else [],
                serves_market="yes" if verdict == "qualified" else "unclear",
            ),
        )

    def judge_answer(**request):
        evidence = json.loads(request["messages"][0]["content"])
        business = evidence["business"]
        url = next(iter(evidence["evidence"]))
        tier_two = "research" in request["system"] or judge_answer.calls.get(business)
        judge_answer.calls[business] = True
        if business == "Unclear Planner" and not tier_two:
            return decision("review", url)
        return decision("qualified", url)

    judge_answer.calls = {}
    judge = Mock()
    judge.messages.parse.side_effect = judge_answer
    sessions = []

    async def research_session(*, prompt, options):
        sessions.append(prompt)
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="research",
            total_cost_usd=0.002,
            usage={},
        )

    monkeypatch.setattr("leadgen.research_agent.create_sdk_mcp_server", lambda **kwargs: kwargs)
    settings = Settings(
        _env_file=None, database_url=empty_database_url, anthropic_api_key=SecretStr("offline")
    )
    pipeline = Pipeline(connection, run, settings, judge=judge, agent_query=research_session)
    assert asyncio.run(pipeline.run_qualify()) == {
        "judged": 2,
        "qualified": 1,
        "rejected": 0,
        "to_review": 1,
    }
    assert sessions == [], "qualification writes first verdicts only; Review researches"
    assert asyncio.run(pipeline.run_review()) == {
        "reviewed": 1,
        "qualified": 2,
        "rejected": 0,
        "human_escalated": 0,
    }
    assert len(sessions) == 1
    assert sessions[0].startswith("Find missing evidence for Unclear Planner in Los Angeles, CA.")
    assert "The judge could not decide:" in sessions[0]
    leads = {lead["business_name"]: lead for lead in read_raw_leads(connection, run.run_id)}
    assert leads["Unclear Planner"]["researched"] and not leads["Clear Planner"]["researched"]
    assert {name: lead["qualification"]["verdict"] for name, lead in leads.items()} == {
        "Unclear Planner": "qualified",
        "Clear Planner": "qualified",
    }
    tiers = connection.execute(
        "SELECT evidence->>'tier', subject FROM eval_records ORDER BY eval_record_id"
    ).fetchall()
    assert sorted(tiers) == [
        ("1", "Clear Planner"),
        ("1", "Unclear Planner"),
        ("2", "Unclear Planner"),
    ]


@pytest.mark.parametrize(
    "domain,public",
    [
        ("anthonyserrano.", False),
        ("anthonyserrano", False),
        ("", False),
        ("anna-planning.com", True),
        ("anna.square.site", True),
    ],
)
def test_only_public_domains_count_as_businesses(domain, public):
    from leadgen.contacts import is_public_domain

    assert is_public_domain(domain) is public


def test_exa_skips_a_result_whose_host_has_no_top_level_domain(running):
    connection, plan, run = running
    client = Mock()
    client.search.return_value = SimpleNamespace(
        cost_dollars=SimpleNamespace(total=0.007),
        results=[
            SimpleNamespace(
                url="https://anthonyserrano./", title="Anthony Serrano Planning", text=""
            ),
            SimpleNamespace(url="https://anna-planning.com/", title="Anna Planning", text=""),
        ],
    )
    leads = source_exa(
        connection,
        run.run_id,
        plan.cells[0].market_id,
        load_category_config(),
        client,
        cities=["Phoenix, AZ"],
    )
    assert [lead.source_key for lead in leads] == ["anna-planning.com"]


def test_blitz_rejecting_one_lead_does_not_fail_enrichment(
    running, empty_database_url, monkeypatch
):
    import leadgen.orchestrator as orchestrator
    from leadgen.contacts import BlitzRequestRejected

    connection, plan, run = running
    persist_leads(
        connection,
        run.run_id,
        plan.cells[0].market_id,
        "exa",
        [RawLead("bad.example", "Bad Domain Planner", website="https://bad.example/")],
    )

    def rejected(*args, **kwargs):
        raise BlitzRequestRejected("BlitzAPI rejected the request: domain: Invalid domain.")

    monkeypatch.setattr(orchestrator, "enrich_contact", rejected)
    settings = Settings(
        _env_file=None, database_url=empty_database_url, blitz_api_key=SecretStr("offline")
    )
    result = asyncio.run(Pipeline(connection, run, settings).run_enrich())
    assert result == {"processed": 1}
    lead = read_raw_leads(connection, run.run_id)[0]
    assert lead["enriched"] and not (lead["phone"] or lead["email"])
    assert connection.execute(
        "SELECT status FROM budget_reservations WHERE operation LIKE 'enrich:%%'"
    ).fetchone() == ("succeeded",)


def test_dedupe_skips_businesses_judged_in_an_earlier_run(approved):
    connection, plan = approved
    market_id = plan.cells[0].market_id
    earlier = start_run(connection, plan.plan_id)
    persist_leads(
        connection,
        earlier.run_id,
        market_id,
        "exa",
        [
            RawLead("judged.example", "Judged Planner", website="https://www.judged.example/"),
            RawLead("unjudged.example", "Unjudged Planner", website="https://unjudged.example/"),
        ],
    )
    connection.execute(
        'UPDATE raw_leads SET qualification=\'{"verdict": "review"}\' '
        "WHERE source_key='judged.example'"
    )
    connection.execute(
        "UPDATE runs SET status='finished',finished_at=now() WHERE run_id=%s", (earlier.run_id,)
    )
    later = start_run(connection, plan.plan_id)
    persist_leads(
        connection,
        later.run_id,
        market_id,
        "exa",
        [
            RawLead("judged.example", "Judged Planner", website="https://judged.example/menu"),
            RawLead("unjudged.example", "Unjudged Planner", website="https://unjudged.example/"),
            RawLead("fresh.example", "Fresh Planner", website="https://fresh.example/"),
        ],
    )
    result = asyncio.run(Pipeline(connection, later, Settings()).run_dedupe())
    assert result == {"duplicates": 1, "new": 2, "already_judged": 1}
    kept = {
        lead["source_key"]
        for lead in read_raw_leads(connection, later.run_id)
        if not lead["duplicate"]
    }
    assert kept == {"unjudged.example", "fresh.example"}


@pytest.mark.parametrize("failure", ["session_error", "retry_refused"])
def test_failed_research_keeps_review_and_the_run_goes_on(
    running, empty_database_url, monkeypatch, failure
):
    from datetime import UTC, datetime

    from leadgen.web_research import FetchedPage, WebResearcher

    connection, plan, run = running
    persist_leads(
        connection,
        run.run_id,
        plan.cells[0].market_id,
        "exa",
        [
            RawLead(
                "unclear.com", "Unclear Planner", phone="3105550111", website="https://unclear.com"
            )
        ],
    )
    lead_id = read_raw_leads(connection, run.run_id)[0]["raw_lead_id"]
    monkeypatch.setattr(
        WebResearcher,
        "fetch_page",
        lambda _self, url: FetchedPage(
            url, url, "Planner", "A planning firm", 200, "free", datetime.now(UTC)
        ),
    )
    monkeypatch.setattr(WebResearcher, "search", lambda *args, **kwargs: [])
    judge = Mock()
    judge.messages.parse.return_value = SimpleNamespace(
        id="offline",
        usage=SimpleNamespace(input_tokens=100, output_tokens=30),
        parsed_output=Judgment(
            verdict="review",
            reason="No CFP® credential on the firm's own pages",
            evidence_quote="",
            source_url="",
            eligible_services=[],
            serves_market="unclear",
        ),
    )
    if failure == "retry_refused":
        # As on a resume: this lead's research session already ran, so it is never paid for twice.
        reserve(connection, run.run_id, f"sdk:research:{lead_id}", Decimal("0.03"))
        connection.execute("UPDATE budget_reservations SET status='succeeded'")

    async def failing_session(*, prompt, options):
        yield ResultMessage(
            subtype="error_during_execution",
            duration_ms=1,
            duration_api_ms=1,
            is_error=True,
            num_turns=3,
            session_id="research",
            total_cost_usd=0.003,
            usage={},
            result="tool crashed",
        )

    monkeypatch.setattr("leadgen.research_agent.create_sdk_mcp_server", lambda **kwargs: kwargs)
    settings = Settings(
        _env_file=None, database_url=empty_database_url, anthropic_api_key=SecretStr("offline")
    )
    pipeline = Pipeline(connection, run, settings, judge=judge, agent_query=failing_session)
    asyncio.run(pipeline.run_qualify())
    assert asyncio.run(pipeline.run_review()) == {
        "reviewed": 1,
        "qualified": 0,
        "rejected": 0,
        "human_escalated": 1,
    }
    assert read_raw_leads(connection, run.run_id)[0]["qualification"]["verdict"] == "review"
    error_type, message = connection.execute(
        "SELECT details->>'error_type', details->>'message' FROM traces "
        "WHERE operation=%s AND outcome='failed'",
        (f"research:{lead_id}",),
    ).fetchone()
    if failure == "session_error":
        assert error_type == "QualificationError"
        assert message == "Research session ended with error_during_execution: tool crashed"
    else:
        assert error_type == "BudgetBlocked" and "will not repeat" in message
