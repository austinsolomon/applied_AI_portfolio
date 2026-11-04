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
from leadgen.category_config import load_category_config
from leadgen.plans import PlannedCell, approve_plan, create_plan, start_run
from leadgen.raw_leads import read_raw_leads
from leadgen.sourcing_exa import source_exa, source_exa_search
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
        connection, [PlannedCell(market_id, "Phoenix, AZ", "mobile massage", 30, Decimal("1"))]
    )
    approve_plan(connection, plan.plan_id, Decimal("3"), "test operator")
    return connection, plan, start_run(connection, plan.plan_id)


def test_recorded_find_similar_replays_end_to_end(running):
    fixture, response = load("find_similar")
    connection, plan, run = running
    client = Mock()
    client.find_similar.return_value = response
    leads = source_exa(
        connection,
        run.run_id,
        plan.cells[0].market_id,
        load_category_config(),
        client,
        seeds=[fixture["request"]["seed"]],
    )
    stored = read_raw_leads(connection, run.run_id)
    assert {lead.source_key for lead in leads} == {row["source_key"] for row in stored}
    assert len(stored) <= len(fixture["results"])
    assert all(row["source"] == "exa" and row["seed_consensus"] == 1 for row in stored)
    assert plan_spend(connection, plan.plan_id) == Decimal(str(fixture["cost_dollars"]))


def test_recorded_search_replays_end_to_end(running):
    fixture, response = load("search")
    connection, plan, run = running
    client = Mock()
    client.search.return_value = response
    source_exa_search(
        connection,
        run.run_id,
        plan.cells[0].market_id,
        load_category_config(),
        client,
        cities=["Phoenix, AZ"],
    )
    assert client.search.call_count == 1
    assert len(read_raw_leads(connection, run.run_id)) <= len(fixture["results"])
    assert plan_spend(connection, plan.plan_id) == Decimal(str(fixture["cost_dollars"]))
