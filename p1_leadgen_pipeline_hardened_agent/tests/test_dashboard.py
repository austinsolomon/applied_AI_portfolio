"""The demand dashboard, rendered headless with Streamlit's AppTest against a seeded database."""

from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from streamlit.testing.v1 import AppTest

from leadgen import dashboard as dashboard_module
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
def test_columns_are_in_reading_order() -> None:
    table = render_dashboard().dataframe[0].value

    assert list(table.columns) == [
        "Market",
        "Leads Available %",
        "Status",
        "Lead goal",
        "Assigned",
        "Still needed",
        "Available",
        "Short by",
    ]


@pytest.mark.usefixtures("dashboard_database")
def test_markets_are_listed_biggest_gap_first_with_their_status() -> None:
    table = render_dashboard().dataframe[0].value

    rows = table[["Market", "Leads Available %", "Status", "Short by"]].values.tolist()
    assert rows[:2] == [
        ["Phoenix, AZ", "10%", "Below goal", 54],
        ["Los Angeles, CA", "18%", "Below goal", 66],
    ]
    assert rows[-2:] == [["Seattle, WA", "125%", "Covered", 0], ["Miami, FL", "—", "Goal met", 0]]


@pytest.mark.usefixtures("dashboard_database")
def test_summary_names_the_biggest_gap_and_the_leads_to_find() -> None:
    metrics = {metric.label: metric.value for metric in render_dashboard().metric}

    assert {key: metrics[key] for key in ("Markets below goal", "Biggest gap")} == {
        "Markets below goal": "6 of 8",
        "Biggest gap": "Phoenix, AZ · 10%",
    }
    assert metrics["New leads to find"] == "200"


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
def test_tabs_cover_gaps_run_history_sources_and_evals() -> None:
    labels = [tab.label for tab in render_dashboard().tabs]

    assert labels == [
        "Demand gaps",
        "Current run",
        "Run history",
        "Sources and spend",
        "Eval review",
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
        connection, [PlannedCell(market_id, "Phoenix, AZ", "mobile massage", 30, Decimal("1"))]
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

    markdown = [element.value for element in app.markdown]
    stage_rows = {
        markdown[index].strip("*"): markdown[index + 1]
        for index in range(len(markdown) - 1)
        if markdown[index].startswith("**") and "badge" in markdown[index + 1]
    }
    assert list(stage_rows) == [
        "Google Maps",
        "Exa search",
        "Dedupe",
        "Enrich contacts",
        "Qualify",
        "Research",
        "Write to CRM",
    ]
    assert "done" in stage_rows["Exa search"]
    assert "needs a human" in stage_rows["Dedupe"]
    assert "waiting" in stage_rows["Write to CRM"]
    assert f"--plan {plan.plan_id} --resume" in app.warning[0].value
    metrics = {metric.label: metric.value for metric in app.metric}
    assert (metrics["Status"], metrics["Spent"], metrics["Budget"]) == ("failed", "$0.021", "$3")
