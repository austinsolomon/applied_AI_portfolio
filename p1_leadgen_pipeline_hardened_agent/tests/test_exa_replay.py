"""Replay recorded, redacted Exa responses through the real sourcing code.

Fixtures come from `scripts/record_exa_fixture.py`; each test skips until its file exists.
"""

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from leadgen.budget import plan_spend
from leadgen.category_config import is_on_host, load_category_config
from leadgen.plans import PlannedCell, approve_plan, create_plan, start_run
from leadgen.raw_leads import read_raw_leads
from leadgen.sourcing_exa import source_exa
from tests.crm_builders import add_market

FIXTURES = Path(__file__).parent / "fixtures" / "exa"


def load(name):
    path = FIXTURES / f"{name}.json"
    if not path.exists():
        pytest.skip(f"no recorded Exa fixture yet: run scripts/record_exa_fixture.py ({path.name})")
    fixture = json.loads(path.read_text(encoding="utf-8"))
    for result in fixture["results"]:
        assert set(result) == {"url", "title", "domain"}, "fixture must stay redacted"
    response = SimpleNamespace(
        cost_dollars=SimpleNamespace(total=fixture["cost_dollars"]),
        results=[SimpleNamespace(url=r["url"], title=r["title"]) for r in fixture["results"]],
    )
    return fixture, response


@pytest.fixture
def running(migrated_connection):
    connection = migrated_connection
    market_id = add_market(connection)
    plan = create_plan(
        connection, [PlannedCell(market_id, "Phoenix, AZ", "private chef", 30, Decimal("1"))]
    )
    approve_plan(connection, plan.plan_id, Decimal("3"), "test operator")
    return connection, plan, start_run(connection, plan.plan_id)


@pytest.mark.parametrize("name", ["company_search", "search"])
def test_recorded_exa_response_replays_through_sourcing(running, name):
    """company_search.json is the current request shape; search.json is a real 2026-11-04
    response from the earlier keyword search, kept because its results (directories, Exa's own
    pages, real businesses) exercise the same filters."""
    fixture, response = load(name)
    connection, plan, run = running
    category = load_category_config()
    client = Mock()
    client.search.return_value = response
    leads = source_exa(
        connection, run.run_id, plan.cells[0].market_id, category, client, cities=["Phoenix, AZ"]
    )
    stored = read_raw_leads(connection, run.run_id)
    assert {lead.source_key for lead in leads} == {row["source_key"] for row in stored}
    assert len(stored) <= len(fixture["results"])
    assert not any(
        is_on_host(row["source_key"], category.hosts_never_identifying_a_business) for row in stored
    )
    # The same response for every phrasing: each kept business is found by all of them.
    assert {row["query_consensus"] for row in stored} <= {len(category.exa_queries)}
    expected = Decimal(str(fixture["cost_dollars"])) * len(category.exa_queries)
    assert plan_spend(connection, plan.plan_id) == expected
