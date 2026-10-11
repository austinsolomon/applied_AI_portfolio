"""Leads Available % per market, and the order the agent reads gaps in."""

import psycopg

from leadgen.gaps import read_gaps, read_market_demand
from tests.crm_builders import add_lead, add_market


def add_leads(
    connection: psycopg.Connection, market_id: int, status: str, count: int, prefix: str
) -> None:
    for lead_number in range(count):
        add_lead(connection, market_id, status=status, email=f"{prefix}{lead_number}@x.example")


def gap_row(connection: psycopg.Connection, market_id: int) -> tuple:
    row = connection.execute(
        "SELECT assigned_leads, available_leads, remaining_goal, leads_available_pct, goal_met, "
        "short_by_leads FROM gap_view WHERE market_id = %s",
        (market_id,),
    ).fetchone()
    assert row is not None
    return row


def test_available_leads_over_remaining_goal(migrated_connection: psycopg.Connection) -> None:
    market_id = add_market(migrated_connection, goal=20)
    add_leads(migrated_connection, market_id, "assigned", 6, "assigned")
    add_leads(migrated_connection, market_id, "converted", 2, "converted")
    add_leads(migrated_connection, market_id, "available", 3, "available")
    add_leads(migrated_connection, market_id, "closed", 4, "closed")

    # Still needed = 20 − (6 assigned + 2 converted) = 12; 3 available ÷ 12 = 25%; short by 9.
    assert gap_row(migrated_connection, market_id) == (8, 3, 12, 25, False, 9)


def test_a_market_with_no_leads_is_at_zero(migrated_connection: psycopg.Connection) -> None:
    market_id = add_market(migrated_connection, goal=5)

    assert gap_row(migrated_connection, market_id) == (0, 0, 5, 0, False, 5)


def test_an_over_assigned_market_has_met_its_goal(
    migrated_connection: psycopg.Connection,
) -> None:
    market_id = add_market(migrated_connection, goal=2)
    add_leads(migrated_connection, market_id, "assigned", 3, "assigned")

    assert gap_row(migrated_connection, market_id) == (3, 0, 0, None, True, 0)


def test_read_gaps_lists_markets_under_100_percent_biggest_gap_first(
    migrated_connection: psycopg.Connection,
) -> None:
    covered = add_market(migrated_connection, "Covered", goal=2)
    add_leads(migrated_connection, covered, "available", 3, "covered")
    add_market(migrated_connection, "Empty", goal=4)
    half = add_market(migrated_connection, "Half", goal=4)
    add_leads(migrated_connection, half, "available", 2, "half")
    met = add_market(migrated_connection, "Met", goal=1)
    add_leads(migrated_connection, met, "converted", 1, "met")

    gaps = read_gaps(migrated_connection)

    assert [(gap.market_name, gap.leads_available_pct) for gap in gaps] == [
        ("Empty", 0),
        ("Half", 50),
    ]


def test_a_covered_market_is_short_by_nothing(migrated_connection: psycopg.Connection) -> None:
    market_id = add_market(migrated_connection, goal=2)
    add_leads(migrated_connection, market_id, "available", 5, "available")

    assert gap_row(migrated_connection, market_id) == (0, 5, 2, 250, False, 0)


def test_market_demand_lists_below_goal_then_covered_then_met(
    migrated_connection: psycopg.Connection,
) -> None:
    met = add_market(migrated_connection, "Met", goal=1)
    add_leads(migrated_connection, met, "converted", 1, "met")
    covered = add_market(migrated_connection, "Covered", goal=1)
    add_leads(migrated_connection, covered, "available", 2, "covered")
    add_market(migrated_connection, "Empty", goal=3)

    demand = read_market_demand(migrated_connection)

    assert [(market.market_name, market.status) for market in demand] == [
        ("Empty", "Below goal"),
        ("Covered", "Covered"),
        ("Met", "Goal met"),
    ]
