"""The demand dashboard, rendered headless with Streamlit's AppTest against a seeded database."""

import json
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
    assert f"--plan {plan.plan_id} --resume" in app.warning[0].value
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

    audit = next(frame.value for frame in app.dataframe if "Who (X)" in frame.value.columns)
    assert list(audit.columns) == ["#", "Time", "+s", "Who (X)", "Summary", "Details"]
    # Newest first by default; the summary is one plain sentence, the details keep the target.
    assert audit[["Who (X)", "Summary"]].values.tolist() == [
        ["Exa API (Search)", "Company search returns 5 results — financial planner in Phoenix"],
        ["Postgres (State machine)", f"Run started — plan {plan.plan_id}"],
    ]
    assert json.loads(audit["Details"].iloc[0])["target"] == "financial planner in Phoenix"
    # The actor filter offers the 19 actors in pipeline order.
    actor_filter = next(widget for widget in app.multiselect if widget.label == "Who (X)")
    assert list(actor_filter.options) == list(ACTORS)
