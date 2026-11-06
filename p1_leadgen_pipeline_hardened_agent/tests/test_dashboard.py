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
def test_demand_columns_are_goal_available_needed_and_percent() -> None:
    table = render_dashboard().dataframe[0].value

    assert list(table.columns) == [
        "Market",
        "Lead Goal",
        "Leads Available",
        "Leads Needed",
        "Leads Available %",
    ]


@pytest.mark.usefixtures("dashboard_database")
def test_markets_are_listed_furthest_from_goal_first() -> None:
    table = render_dashboard().dataframe[0].value

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
        "Extract contacts",
        "Dedupe",
        "Enrich contacts",
        "Qualify",
        "Write to CRM",
    ]
    positions = [track.index(f"{number}. {label}") for number, label in enumerate(labels, 1)]
    assert positions == sorted(positions), "stages run left to right in pipeline order"
    stage_cells = track.split('<div style="flex:1')[1:]
    assert "#1840ED" in stage_cells[1] and "done" in stage_cells[1]  # Exa search: blue
    assert "needs a human" in stage_cells[2]
    assert "#E3E5EA" in stage_cells[6] and "waiting" in stage_cells[6]  # CRM: still gray
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
    # The actor filter offers the 19 actors in pipeline order.
    actor_filter = next(widget for widget in app.multiselect if widget.label == "Subcomponent")
    assert list(actor_filter.options) == list(ACTORS)


def test_audit_rows_are_labelled_with_the_stage_they_ran_in() -> None:
    from leadgen.audit import PLANNER, SDK_PRE_TOOL_USE_HOOK, SDK_TOOL_CALL, STATE_MACHINE
    from leadgen.dashboard import audit_stages

    def event(actor, action, target):
        return {"actor": actor, "action": action, "target": target}

    events = [
        event(PLANNER, "plans 1 cell", "Phoenix, AZ"),
        event(SDK_PRE_TOOL_USE_HOOK, "allows the tool call", "run_stage maps"),
        event(SDK_TOOL_CALL, "calls run_stage('maps')", "maps"),
        event(STATE_MACHINE, "stage maps started", "maps"),
        event("Apify (Google Maps)", "starts the scraper", "financial planner in Phoenix, AZ"),
        event(SDK_TOOL_CALL, "calls run_stage('crm')", "crm"),
        event(STATE_MACHINE, "stage crm finished: inserted 1", "crm"),
        event(STATE_MACHINE, "run finished", "plan 1"),
    ]
    assert audit_stages(events) == [
        "Plan & approve",
        "Google Maps",
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
