"""The CRM tables enforce their own rules: normalized dedupe keys, contactability, valid values."""

import psycopg
import pytest

from tests.crm_builders import add_lead, add_market


def normalized_keys(connection: psycopg.Connection, lead_id: int) -> tuple[str, str, str]:
    row = connection.execute(
        "SELECT phone_normalized, email_normalized, website_domain FROM crm_leads "
        "WHERE lead_id = %s",
        (lead_id,),
    ).fetchone()
    assert row is not None
    return row


def test_the_same_phone_in_another_format_is_a_duplicate(
    migrated_connection: psycopg.Connection,
) -> None:
    market_id = add_market(migrated_connection)
    add_lead(migrated_connection, market_id, phone="(310) 555-0142")

    with pytest.raises(psycopg.errors.UniqueViolation):
        add_lead(migrated_connection, market_id, phone="+1 310.555.0142")


def test_the_same_email_in_another_case_is_a_duplicate(
    migrated_connection: psycopg.Connection,
) -> None:
    market_id = add_market(migrated_connection)
    add_lead(migrated_connection, market_id, email="Hello@CedarMassage.example")

    with pytest.raises(psycopg.errors.UniqueViolation):
        add_lead(migrated_connection, market_id, email="  hello@cedarmassage.example ")


def test_the_same_google_place_is_a_duplicate(migrated_connection: psycopg.Connection) -> None:
    market_id = add_market(migrated_connection)
    add_lead(migrated_connection, market_id, email="a@one.example", google_place_id="place_1")

    with pytest.raises(psycopg.errors.UniqueViolation):
        add_lead(migrated_connection, market_id, email="b@two.example", google_place_id="place_1")


@pytest.mark.parametrize(
    ("website", "expected_domain"),
    [
        ("https://www.CedarMassage.example/services?ref=maps#top", "cedarmassage.example"),
        ("http://cedarmassage.example:8080", "cedarmassage.example"),
        ("cedarmassage.example/about", "cedarmassage.example"),
        ("   ", None),
    ],
)
def test_website_is_reduced_to_its_bare_domain(
    migrated_connection: psycopg.Connection, website: str, expected_domain: str | None
) -> None:
    market_id = add_market(migrated_connection)
    lead_id = add_lead(migrated_connection, market_id, email="a@one.example", website=website)

    assert normalized_keys(migrated_connection, lead_id)[2] == expected_domain


def test_a_shared_domain_is_allowed(migrated_connection: psycopg.Connection) -> None:
    # Chain locations and link-in-bio hosts share a domain, so the domain is a match key only.
    market_id = add_market(migrated_connection)
    add_lead(
        migrated_connection, market_id, email="a@one.example", website="https://chain.example/a"
    )
    add_lead(
        migrated_connection, market_id, email="b@two.example", website="https://chain.example/b"
    )

    shared_domain_rows = migrated_connection.execute(
        "SELECT count(*) FROM crm_leads WHERE website_domain = 'chain.example'"
    ).fetchone()
    assert shared_domain_rows == (2,)


def test_a_lead_needs_a_phone_or_an_email(migrated_connection: psycopg.Connection) -> None:
    market_id = add_market(migrated_connection)

    with pytest.raises(psycopg.errors.CheckViolation):
        add_lead(migrated_connection, market_id, website="https://cedar.example")


@pytest.mark.parametrize(
    "columns",
    [
        {"email": "a@one.example", "status": "pending"},
        {"phone": "555-0142"},
        {"email": "not-an-email"},
        {"email": "a@one.example", "source": "spreadsheet"},
        {"email": "a@one.example", "business_name": "  "},
    ],
)
def test_invalid_lead_values_are_rejected(
    migrated_connection: psycopg.Connection, columns: dict[str, str]
) -> None:
    market_id = add_market(migrated_connection)

    with pytest.raises(psycopg.errors.CheckViolation):
        add_lead(migrated_connection, market_id, **columns)


@pytest.mark.parametrize(
    "market_values",
    [
        "('Austin', 'tx', 30.27, -97.74, 50, 50)",
        "('Austin', 'TX', 95.0, -97.74, 50, 50)",
        "('Austin', 'TX', 30.27, -97.74, 0, 50)",
        "('Austin', 'TX', 30.27, -97.74, 50, -1)",
    ],
)
def test_invalid_market_values_are_rejected(
    migrated_connection: psycopg.Connection, market_values: str
) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        migrated_connection.execute(
            "INSERT INTO markets (name, state, center_latitude, center_longitude, "
            f"service_radius_km, lead_goal) VALUES {market_values}"
        )
