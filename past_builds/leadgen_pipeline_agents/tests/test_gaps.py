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
        "SELECT lead_goal, available_leads, leads_needed, leads_available_pct, goal_met "
        "FROM gap_view WHERE market_id = %s",
        (market_id,),
    ).fetchone()
    assert row is not None
    return row


def test_available_leads_over_goal_and_assigned_leads_do_not_count(
    migrated_connection: psycopg.Connection,
) -> None:
    market_id = add_market(migrated_connection, goal=20)
    add_leads(migrated_connection, market_id, "assigned", 6, "assigned")
    add_leads(migrated_connection, market_id, "converted", 2, "converted")
    add_leads(migrated_connection, market_id, "available", 5, "available")
    add_leads(migrated_connection, market_id, "closed", 4, "closed")

    # 5 available ÷ 20 goal = 25%; 20 − 5 = 15 leads needed. Assigned and converted are ignored.
    assert gap_row(migrated_connection, market_id) == (20, 5, 15, 25, False)


def test_a_market_with_no_leads_is_at_zero(migrated_connection: psycopg.Connection) -> None:
    market_id = add_market(migrated_connection, goal=5)

    assert gap_row(migrated_connection, market_id) == (5, 0, 5, 0, False)


def test_a_market_over_its_goal_needs_nothing(migrated_connection: psycopg.Connection) -> None:
    market_id = add_market(migrated_connection, goal=2)
    add_leads(migrated_connection, market_id, "available", 5, "available")

    assert gap_row(migrated_connection, market_id) == (2, 5, 0, 250, True)


def test_read_gaps_lists_markets_below_goal_furthest_first(
    migrated_connection: psycopg.Connection,
) -> None:
    met = add_market(migrated_connection, "Met", goal=2)
    add_leads(migrated_connection, met, "available", 2, "met")
    add_market(migrated_connection, "Empty", goal=4)
    half = add_market(migrated_connection, "Half", goal=4)
    add_leads(migrated_connection, half, "available", 2, "half")

    gaps = read_gaps(migrated_connection)

    assert [(gap.market_name, gap.leads_available_pct) for gap in gaps] == [
        ("Empty", 0),
        ("Half", 50),
    ]


def test_market_demand_lists_below_goal_then_met(migrated_connection: psycopg.Connection) -> None:
    met = add_market(migrated_connection, "Met", goal=1)
    add_leads(migrated_connection, met, "available", 1, "met")
    add_market(migrated_connection, "Empty", goal=3)

    demand = read_market_demand(migrated_connection)

    assert [(market.market_name, market.status) for market in demand] == [
        ("Empty", "Below goal"),
        ("Met", "Goal met"),
    ]
