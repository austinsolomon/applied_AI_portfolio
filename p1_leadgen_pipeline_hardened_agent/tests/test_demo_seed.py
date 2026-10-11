"""The demo data loads only into an empty database and produces the documented gaps."""

import psycopg
import pytest

from leadgen.demo_seed import DEMO_MARKETS, DemoSeedRefused, seed_demo_data
from leadgen.gaps import read_gaps
from leadgen.migrate import apply_migrations, discover_migrations


def test_demo_seed_produces_the_documented_gaps(migrated_connection: psycopg.Connection) -> None:
    lead_count = seed_demo_data(migrated_connection)

    expected_leads = sum(
        market.available
        + market.assigned
        + market.converted
        + market.closed
        + market.do_not_contact
        for market in DEMO_MARKETS
    )
    assert lead_count == expected_leads
    # Seattle (125%) and Miami (goal met) are not gaps.
    assert [
        (gap.market_name, gap.leads_available_pct) for gap in read_gaps(migrated_connection)
    ] == [
        ("Phoenix", 10),
        ("Los Angeles", 18),
        ("San Diego", 30),
        ("Austin", 50),
        ("Chicago", 55),
        ("Denver", 80),
    ]


def test_demo_seed_is_identical_on_every_run(empty_database_url: str) -> None:
    def seeded_snapshot() -> list[tuple]:
        with psycopg.connect(empty_database_url, autocommit=True) as connection:
            connection.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
            apply_migrations(connection, discover_migrations())
            seed_demo_data(connection)
            return connection.execute(
                "SELECT business_name, phone, email, website, status "
                "FROM crm_leads ORDER BY lead_id"
            ).fetchall()

    assert seeded_snapshot() == seeded_snapshot()


def test_demo_seed_refuses_a_database_that_has_data(
    migrated_connection: psycopg.Connection,
) -> None:
    seed_demo_data(migrated_connection)

    with pytest.raises(DemoSeedRefused):
        seed_demo_data(migrated_connection)
