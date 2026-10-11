"""Small builders for inserting markets and CRM leads in tests."""

import psycopg


def add_market(connection: psycopg.Connection, name: str = "Los Angeles", goal: int = 10) -> int:
    row = connection.execute(
        """
        INSERT INTO markets (name, state, center_latitude, center_longitude, lead_goal)
        VALUES (%s, 'CA', 34.05, -118.24, %s) RETURNING market_id
        """,
        (name, goal),
    ).fetchone()
    assert row is not None
    return row[0]


def add_lead(connection: psycopg.Connection, market_id: int, **columns: object) -> int:
    values = {"business_name": "Cedar Mobile Massage", "source": "demo_seed", **columns}
    names = ", ".join(["market_id", *values])
    placeholders = ", ".join(["%s"] * (len(values) + 1))
    row = connection.execute(
        f"INSERT INTO crm_leads ({names}) VALUES ({placeholders}) RETURNING lead_id",
        (market_id, *values.values()),
    ).fetchone()
    assert row is not None
    return row[0]
