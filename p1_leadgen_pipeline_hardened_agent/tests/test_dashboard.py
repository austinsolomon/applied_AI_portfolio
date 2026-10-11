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

    assert metrics == {
        "Markets below goal": "6 of 8",
        "Biggest gap": "Phoenix, AZ · 10%",
        "New leads to find": "200",
    }


def test_missing_database_url_shows_how_to_fix_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_settings.cache_clear()

    app = render_dashboard()

    assert "DATABASE_URL is missing or malformed" in app.error[0].value
    get_settings.cache_clear()
