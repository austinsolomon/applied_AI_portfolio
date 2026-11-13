"""The demand dashboard, rendered headless with Streamlit's AppTest against a seeded database."""

from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from streamlit.testing.v1 import AppTest

from leadgen import dashboard as dashboard_module
from leadgen.audit import ACTORS
from leadgen.demo_seed import seed_demo_data
from leadgen.settings import get_settings

DASHBOARD_PATH = Path(dashboard_module.__file__)


@pytest.fixture
def dashboard_database(
    migrated_connection: psycopg.Connection,
    empty_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Point the dashboard at a fresh database with the demo data loaded."""
    seed_demo_data(migrated_connection)
    monkeypatch.setenv("DATABASE_URL", empty_database_url)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def render_dashboard() -> AppTest:
    app = AppTest.from_file(str(DASHBOARD_PATH), default_timeout=30)
    app.run()
    assert not app.exception, app.exception
    return app


@pytest.mark.usefixtures("dashboard_database")
def test_demand_tab_shows_the_progress_chart_and_no_duplicate_table() -> None:
    demand_tab = render_dashboard().tabs[0]
    assert demand_tab.label == "Demand gaps"
    # Summary metrics, the progress chart (AppTest has no chart element type) and a caption.
    assert len(demand_tab.children) == 3 and demand_tab.caption
    assert not demand_tab.dataframe, "the same numbers are not repeated as a table"


def test_markets_are_charted_furthest_from_goal_first(migrated_connection) -> None:
    from leadgen.dashboard import demand_table
    from leadgen.demo_seed import seed_demo_data
    from leadgen.gaps import read_market_demand

    seed_demo_data(migrated_connection)
    table = demand_table(read_market_demand(migrated_connection))
    assert list(table.columns) == [
        "Market",
        "Lead Goal",
        "Leads Available",
        "Leads Needed",
        "Leads Available %",
    ]
    rows = table.values.tolist()
    assert rows[:2] == [["Phoenix, AZ", 100, 10, 90, 10], ["Los Angeles, CA", 100, 18, 82, 18]]
    assert rows[-1] == ["Seattle, WA", 100, 100, 0, 100]


@pytest.mark.usefixtures("dashboard_database")
def test_summary_names_the_furthest_market_and_the_leads_needed() -> None:
    metrics = {metric.label: metric.value for metric in render_dashboard().metric}

    assert metrics["Markets below goal"] == "7 of 8"
    assert metrics["Furthest from goal"] == "Phoenix, AZ · 10%"
    assert metrics["Leads needed"] == "365"


def test_distance_bands_split_far_halfway_close_and_met() -> None:
    assert [dashboard_module.distance_band(pct) for pct in (0, 39, 40, 74, 75, 99, 100, 250)] == [
        "Far (under 40%)",
        "Far (under 40%)",
        "Halfway (40–74%)",
        "Halfway (40–74%)",
        "Close (75–99%)",
        "Close (75–99%)",
        "Goal met",
        "Goal met",
    ]


def test_missing_database_url_shows_how_to_fix_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_settings.cache_clear()

    app = render_dashboard()

    assert "DATABASE_URL is missing or malformed" in app.error[0].value
    get_settings.cache_clear()


@pytest.mark.usefixtures("dashboard_database")
def test_tabs_cover_gaps_run_audit_history_sources_and_evals() -> None:
    labels = [tab.label for tab in render_dashboard().tabs]

    assert labels == [
        "Demand gaps",
        "Current run",
        "Audit log",
        "Run history",
        "Sources and spend",
        "Eval review",
        "Data model",
    ]


@pytest.mark.usefixtures("dashboard_database")
def test_with_no_runs_the_run_tabs_say_how_to_start_one() -> None:
    infos = [info.value for info in render_dashboard().info]

    assert any("leadgen plan" in info for info in infos)


def seed_blocked_run(connection: psycopg.Connection):
    """A Phoenix run that finished Maps and Exa, then stopped with one paid call unresolved."""
    from decimal import Decimal

    from psycopg.types.json import Jsonb

    from leadgen.plans import PlannedCell, approve_plan, create_plan, finish_run, start_run

    market_id = connection.execute("SELECT market_id FROM markets WHERE name='Phoenix'").fetchone()[
        0
    ]
    plan = create_plan(
        connection, [PlannedCell(market_id, "Phoenix, AZ", "financial planner", 30, Decimal("1"))]
    )
    approve_plan(connection, plan.plan_id, Decimal("3"), "test operator")
    run = start_run(connection, plan.plan_id)
    for stage, result in [("maps", {"sourced": 18}), ("exa", {"sourced": 37, "queries": 3})]:
        connection.execute(
            "INSERT INTO pipeline_stages(run_id,stage,result) VALUES (%s,%s,%s)",
            (run.run_id, stage, Jsonb(result)),
        )
    connection.execute(
        "INSERT INTO budget_reservations(plan_id,run_id,operation,ceiling_usd,status) "
        "VALUES (%s,%s,'web:tier1:1:1:fetch_page',0.02,'uncertain')",
        (plan.plan_id, run.run_id),
    )
    connection.execute(
        "INSERT INTO cost_ledger(run_id,service,call_number,cost_usd,endpoint) "
        "VALUES (%s,'exa',1,0.021,'search')",
        (run.run_id,),
    )
    finish_run(connection, run.run_id, "failed")
    return plan, run


def test_current_run_shows_each_stage_and_flags_the_human_step(
    dashboard_database, migrated_connection: psycopg.Connection
) -> None:
    plan, _run = seed_blocked_run(migrated_connection)

    app = render_dashboard()

    track = next(element.value for element in app.markdown if "display:flex" in element.value)
    labels = [
        "Google Maps",
        "Exa search",
        "Adapt sourcing (agent)",
        "Extract contacts",
        "Dedupe",
        "Enrich contacts",
        "Qualify",
        "Review",
        "Write to CRM",
    ]
    positions = [track.index(f"{number}. {label}") for number, label in enumerate(labels, 1)]
    assert positions == sorted(positions), "stages run left to right in pipeline order"
    stage_cells = track.split('<div style="flex:1')[1:]
    assert "#1840ED" in stage_cells[1] and "done" in stage_cells[1]  # Exa search: blue
    assert "needs a human" in stage_cells[2]
    assert "#E3E5EA" in stage_cells[8] and "waiting" in stage_cells[8]  # CRM: still gray
    # The open-call note is a small caption under the run's numbers, never a banner on top.
    assert not app.warning
    assert any("web:tier1:1:1:fetch_page" in caption.value for caption in app.caption)
    summary = next(
        frame.value for frame in app.dataframe if "Per qualified" in frame.value.columns
    ).iloc[0]
    assert (summary["Status"], summary["Spend"], summary["Budget"]) == ("failed", "$0.021", "$3")
    assert "Recent events" not in [header.value for header in app.subheader]


def test_audit_log_reads_who_does_what_to_whom(
    dashboard_database, migrated_connection: psycopg.Connection
) -> None:
    plan, run = seed_blocked_run(migrated_connection)
    for actor, action, target in [
        ("Postgres (State machine)", "run started", f"plan {plan.plan_id}"),
        ("Exa API (Search)", "company search returns 5 results", "financial planner in Phoenix"),
    ]:
        migrated_connection.execute(
            "INSERT INTO audit_events (run_id, plan_id, actor, action, target) "
            "VALUES (%s, %s, %s, %s, %s)",
            (run.run_id, plan.plan_id, actor, action, target),
        )

    app = render_dashboard()

    audit = next(frame.value for frame in app.dataframe if "Subcomponent" in frame.value.columns)
    assert list(audit.columns) == ["#", "Time", "Stage", "Subcomponent", "Summary"]
    # Newest first by default; the summary is one plain sentence, the details keep the target.
    assert audit[["Subcomponent", "Summary"]].values.tolist() == [
        ["Exa API (Search)", "Company search returns 5 results — financial planner in Phoenix"],
        ["Postgres (State machine)", f"Run started — plan {plan.plan_id}"],
    ]
    assert audit["Stage"].tolist() == ["Plan & approve", "Plan & approve"]
    # The actor filter offers the 20 actors in pipeline order.
    actor_filter = next(widget for widget in app.multiselect if widget.label == "Subcomponent")
    assert list(actor_filter.options) == list(ACTORS)


def test_audit_rows_are_labelled_with_the_stage_they_ran_in() -> None:
    from leadgen.audit import APIFY_GOOGLE_MAPS, PLANNER, STATE_MACHINE
    from leadgen.dashboard import audit_stages

    def event(actor, action, target):
        return {"actor": actor, "action": action, "target": target}

    events = [
        event(PLANNER, "plans 1 cell", "Phoenix, AZ"),
        event(STATE_MACHINE, "stage maps started", "maps"),
        event(APIFY_GOOGLE_MAPS, "starts the scraper", "financial planner in Phoenix, AZ"),
        event(STATE_MACHINE, "stage maps finished: sourced 1", "maps"),
        event(STATE_MACHINE, "stage crm started", "crm"),
        event(STATE_MACHINE, "stage crm finished: inserted 1", "crm"),
        event(STATE_MACHINE, "run finished", "plan 1"),
    ]
    assert audit_stages(events) == [
        "Plan & approve",
        "Google Maps",
        "Google Maps",
        "Google Maps",
        "Write to CRM",
        "Write to CRM",
        "Close-out",
    ]


def login_as_role(connection, database_url, monkeypatch, grant, mode):
    """Create a throwaway login, give it a production role's grants and point the page at it."""
    from urllib.parse import urlsplit, urlunsplit
    from uuid import uuid4

    from psycopg import sql

    role = f"dashboard_test_{uuid4().hex[:8]}"
    connection.execute(
        sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'dashboard-test'").format(sql.Identifier(role))
    )
    connection.execute(
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
            sql.Identifier(connection.info.dbname), sql.Identifier(role)
        )
    )
    grant(connection, role)
    parts = urlsplit(database_url)
    host = parts.netloc.rsplit("@", 1)[1]
    role_url = urlunsplit(parts._replace(netloc=f"{role}:dashboard-test@{host}"))
    monkeypatch.setenv("DATABASE_URL", role_url)
    monkeypatch.setenv("LEADGEN_DASHBOARD_MODE", mode)
    get_settings.cache_clear()
    return role, role_url


def drop_role(connection, role):
    from psycopg import sql

    get_settings.cache_clear()
    connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
    connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


@pytest.fixture
def read_only_dashboard_role(
    migrated_connection: psycopg.Connection,
    empty_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Log the page in with the production dashboard role's grants: aggregate views only."""
    from leadgen.database_roles import grant_dashboard

    seed_demo_data(migrated_connection)
    role, _url = login_as_role(
        migrated_connection, empty_database_url, monkeypatch, grant_dashboard, "hosted"
    )
    yield
    drop_role(migrated_connection, role)


@pytest.mark.usefixtures("read_only_dashboard_role")
def test_hosted_dashboard_renders_from_views_with_a_read_only_role() -> None:
    app = render_dashboard()

    assert [tab.label for tab in app.tabs] == ["Demand gaps", "Run history", "Sources and spend"]
    assert not app.error, [element.value for element in app.error]


@pytest.fixture
def operator_role(
    migrated_connection: psycopg.Connection,
    empty_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[str]:
    """Log the page in with the production operator role's grants: read all, write eval labels."""
    from leadgen.database_roles import grant_operator

    seed_demo_data(migrated_connection)
    role, role_url = login_as_role(
        migrated_connection, empty_database_url, monkeypatch, grant_operator, "operator"
    )
    yield role_url
    drop_role(migrated_connection, role)


def test_operator_dashboard_shows_every_tab_and_may_write_only_labels(
    operator_role: str, migrated_connection: psycopg.Connection
) -> None:
    from leadgen.eval_review import EVAL_LABELS, label_eval_record, record_eval

    app = render_dashboard()
    assert [tab.label for tab in app.tabs] == [
        "Demand gaps",
        "Current run",
        "Audit log",
        "Run history",
        "Sources and spend",
        "Eval review",
        "Data model",
    ]
    assert not app.error, [element.value for element in app.error]

    record_id = record_eval(
        migrated_connection, kind="judge_decision", subject="Cedar", verdict="qualified"
    )
    with psycopg.connect(operator_role, autocommit=True) as operator:
        label_eval_record(operator, record_id, label=EVAL_LABELS[0], labeled_by="operator test")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            operator.execute("DELETE FROM crm_leads")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            operator.execute("CREATE TABLE operator_check (x int)")


def test_run_history_sorts_new_leads_into_qualified_rejected_and_human_escalated() -> None:
    from datetime import datetime
    from decimal import Decimal

    from leadgen.dashboard import history_table

    def row(**counts):
        base = {
            "started_at": datetime(2026, 11, 6, 8, 0),
            "market": "San Diego, CA",
            "status": "finished",
            "maps_sourced": 138,
            "exa_sourced": 37,
            "new_leads": 137,
            "qualified": 45,
            "rejected": 62,
            "human_escalated": 30,
            "not_judged": 0,
            "crm_inserted": 44,
            "spend_usd": Decimal("3.20"),
            "budget_usd": Decimal("5"),
            "cost_per_qualified_usd": Decimal("0.07"),
            "run_id": "6f8dd39e-e101",
        }
        return {**base, **counts}

    table = history_table([row()])
    assert "Review" not in table.columns and "Not judged" not in table.columns
    first = table.iloc[0]
    assert first["New"] == first["Qualified"] + first["Rejected"] + first["Human escalated"]
    # A run that left leads unjudged shows them, so the columns still add up to New.
    unfinished = history_table([row(), row(new_leads=140, not_judged=3)]).iloc[1]
    assert unfinished["New"] == (
        unfinished["Qualified"]
        + unfinished["Rejected"]
        + unfinished["Human escalated"]
        + unfinished["Not judged"]
    )


def test_demand_progress_axis_ticks_every_20_points() -> None:
    import pandas

    from leadgen.dashboard import progress_chart

    table = pandas.DataFrame(
        [
            {
                "Market": "Phoenix, AZ",
                "Lead Goal": 100,
                "Leads Available": 37,
                "Leads Needed": 63,
                "Leads Available %": 37,
            }
        ]
    )
    spec = progress_chart(table).to_dict()
    tick_lists = [
        layer["encoding"]["x"]["axis"]["values"]
        for layer in spec["layer"]
        if "axis" in layer["encoding"]["x"]
    ]
    assert tick_lists and all(ticks == [0, 20, 40, 60, 80, 100] for ticks in tick_lists)


def test_the_page_does_not_poll_the_database_with_no_run_in_progress(
    dashboard_database, migrated_connection: psycopg.Connection
) -> None:
    """Neon meters data sent out; an idle page left open must not keep re-reading it."""
    seed_blocked_run(migrated_connection)  # the run has ended (failed), so nothing is live
    app = render_dashboard()
    assert app.session_state["live_refresh"] is False
    assert any("does not refresh itself" in caption.value for caption in app.caption)


def test_the_page_refreshes_while_a_run_is_in_progress_and_stops_after(
    dashboard_database, migrated_connection: psycopg.Connection
) -> None:
    _, run = seed_blocked_run(migrated_connection)
    migrated_connection.execute(
        "UPDATE runs SET status='running', finished_at=NULL WHERE run_id=%s", (run.run_id,)
    )
    app = render_dashboard()
    assert app.session_state["live_refresh"] is True
    assert any("Refreshes every 10 seconds" in caption.value for caption in app.caption)

    migrated_connection.execute(
        "UPDATE runs SET status='finished', finished_at=now() WHERE run_id=%s", (run.run_id,)
    )
    app.run()
    assert app.session_state["live_refresh"] is False


def test_audit_reads_fetch_only_events_newer_than_the_ones_held(
    migrated_connection: psycopg.Connection,
) -> None:
    from leadgen.demo_seed import seed_demo_data
    from leadgen.run_status import read_audit_events

    seed_demo_data(migrated_connection)
    plan, run = seed_blocked_run(migrated_connection)
    for action in ["first", "second", "third"]:
        migrated_connection.execute(
            "INSERT INTO audit_events (run_id, plan_id, actor, action, target) "
            "VALUES (%s, %s, 'Postgres (State machine)', %s, 'x')",
            (run.run_id, plan.plan_id, action),
        )
    events = read_audit_events(migrated_connection, plan.plan_id)
    newer = read_audit_events(migrated_connection, plan.plan_id, events[0]["audit_event_id"])
    assert [event["action"] for event in newer] == [event["action"] for event in events[1:]]


def test_a_run_stuck_as_running_for_hours_does_not_keep_pages_polling(
    migrated_connection: psycopg.Connection,
) -> None:
    from leadgen.demo_seed import seed_demo_data
    from leadgen.run_status import RUN_LIVE_HOURS, run_in_progress

    seed_demo_data(migrated_connection)
    _, run = seed_blocked_run(migrated_connection)
    migrated_connection.execute(
        "UPDATE runs SET status='running', finished_at=NULL WHERE run_id=%s", (run.run_id,)
    )
    assert run_in_progress(migrated_connection)
    migrated_connection.execute(
        "UPDATE runs SET started_at = now() - make_interval(hours => %s) WHERE run_id=%s",
        (RUN_LIVE_HOURS + 1, run.run_id),
    )
    assert not run_in_progress(migrated_connection)
