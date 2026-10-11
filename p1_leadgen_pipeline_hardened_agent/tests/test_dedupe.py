"""Incoming leads are matched against the CRM on the CRM's own normalized keys."""

import re
from pathlib import Path

import psycopg
import pytest

from leadgen.category_config import CategoryConfig, load_category_config
from leadgen.dedupe import (
    EMAIL_NORMALIZED_SQL,
    PHONE_NORMALIZED_SQL,
    WEBSITE_DOMAIN_SQL,
    IncomingLead,
    NormalizedKeys,
    dedupe_against_crm,
    new_leads,
)
from tests.crm_builders import add_lead, add_market

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CRM_MIGRATION = PROJECT_ROOT / "src" / "leadgen" / "migrations" / "0001_crm_core.sql"


@pytest.fixture(scope="module")
def category() -> CategoryConfig:
    return load_category_config(PROJECT_ROOT / "config" / "category.yaml")


def decide(
    connection: psycopg.Connection, category: CategoryConfig, **lead_columns: str
) -> tuple[str, ...]:
    """Dedupe one incoming lead and return the keys it matched on."""
    lead = IncomingLead(business_name="Incoming Massage", **lead_columns)
    (decision,) = dedupe_against_crm(connection, [lead], category)
    return decision.matched_on


def collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text)


@pytest.mark.parametrize(
    "expression", [PHONE_NORMALIZED_SQL, EMAIL_NORMALIZED_SQL, WEBSITE_DOMAIN_SQL]
)
def test_normalization_expressions_are_the_crm_migrations(expression: str) -> None:
    # The dedupe keys are computed by the exact text that generates the CRM's key columns.
    migration_text = collapse_whitespace(CRM_MIGRATION.read_text(encoding="utf-8"))

    assert collapse_whitespace(expression) in migration_text


@pytest.mark.parametrize(
    ("raw_columns", "expected_keys"),
    [
        (
            {"phone": "+1 (310) 555-0142", "email": " Hello@Cedar.Example ", "website": None},
            NormalizedKeys(None, "3105550142", "hello@cedar.example", None),
        ),
        (
            {
                "phone": "310.555.0142 ext",
                "email": "a@one.example",
                "website": "HTTPS://WWW.Cedar.Example/services?ref=maps#top",
            },
            NormalizedKeys(None, "3105550142", "a@one.example", "cedar.example"),
        ),
        (
            {"phone": None, "email": "a@one.example", "website": "cedar.example:8080/about"},
            NormalizedKeys(None, None, "a@one.example", "cedar.example"),
        ),
        (
            {
                "phone": None,
                "email": "a@one.example",
                "website": "   ",
                "google_place_id": "place_7",
            },
            NormalizedKeys("place_7", None, "a@one.example", None),
        ),
    ],
)
def test_incoming_keys_equal_the_crm_generated_columns(
    migrated_connection: psycopg.Connection,
    category: CategoryConfig,
    raw_columns: dict[str, str | None],
    expected_keys: NormalizedKeys,
) -> None:
    market_id = add_market(migrated_connection)
    lead_id = add_lead(migrated_connection, market_id, **raw_columns)
    crm_keys = migrated_connection.execute(
        "SELECT google_place_id, phone_normalized, email_normalized, website_domain "
        "FROM crm_leads WHERE lead_id = %s",
        (lead_id,),
    ).fetchone()

    (decision,) = dedupe_against_crm(
        migrated_connection, [IncomingLead("Same Business", **raw_columns)], category
    )

    assert decision.keys == expected_keys
    assert crm_keys == (
        expected_keys.google_place_id,
        expected_keys.phone,
        expected_keys.email,
        expected_keys.website_domain,
    )
    assert decision.is_duplicate


def test_the_same_phone_in_another_format_is_a_duplicate(
    migrated_connection: psycopg.Connection, category: CategoryConfig
) -> None:
    market_id = add_market(migrated_connection)
    add_lead(migrated_connection, market_id, phone="(310) 555-0142")

    assert decide(migrated_connection, category, phone="+1 310.555.0142") == ("phone",)
    assert decide(migrated_connection, category, phone="3105550142") == ("phone",)
    assert decide(migrated_connection, category, phone="(310) 555-0143") == ()


def test_a_short_phone_never_matches(
    migrated_connection: psycopg.Connection, category: CategoryConfig
) -> None:
    market_id = add_market(migrated_connection)
    add_lead(migrated_connection, market_id, phone="(310) 555-0142")

    # Seven digits cannot equal any CRM key, which is always ten digits.
    assert decide(migrated_connection, category, phone="555-0142") == ()


def test_the_same_email_in_another_case_is_a_duplicate(
    migrated_connection: psycopg.Connection, category: CategoryConfig
) -> None:
    market_id = add_market(migrated_connection)
    add_lead(migrated_connection, market_id, email="Hello@CedarMassage.example")

    assert decide(migrated_connection, category, email="  hello@cedarmassage.example ") == (
        "email",
    )
    assert decide(migrated_connection, category, email="other@cedarmassage.example") == ()


def test_the_same_google_place_is_a_duplicate(
    migrated_connection: psycopg.Connection, category: CategoryConfig
) -> None:
    market_id = add_market(migrated_connection)
    add_lead(migrated_connection, market_id, email="a@one.example", google_place_id="place_1")

    assert decide(migrated_connection, category, google_place_id="place_1") == ("google_place_id",)
    assert decide(migrated_connection, category, google_place_id="place_2") == ()
    assert decide(migrated_connection, category, google_place_id="  ") == ()


@pytest.mark.parametrize(
    "incoming_website",
    [
        "https://www.cedarmassage.example/book",
        "http://cedarmassage.example:8080",
        "CedarMassage.example",
        "cedarmassage.example/about?utm=x",
    ],
)
def test_the_same_website_domain_is_a_duplicate(
    migrated_connection: psycopg.Connection, category: CategoryConfig, incoming_website: str
) -> None:
    market_id = add_market(migrated_connection)
    add_lead(
        migrated_connection,
        market_id,
        email="a@one.example",
        website="https://cedarmassage.example",
    )

    assert decide(migrated_connection, category, website=incoming_website) == ("website_domain",)


def test_a_different_subdomain_is_not_the_same_domain(
    migrated_connection: psycopg.Connection, category: CategoryConfig
) -> None:
    # Site builders give each business its own subdomain, so only the exact domain matches.
    market_id = add_market(migrated_connection)
    add_lead(
        migrated_connection,
        market_id,
        email="a@one.example",
        website="https://cedar.sitebuilder.example",
    )

    assert decide(migrated_connection, category, website="https://willow.sitebuilder.example") == ()
    assert decide(migrated_connection, category, website="cedar.sitebuilder.example") == (
        "website_domain",
    )


@pytest.mark.parametrize(
    ("crm_website", "incoming_website"),
    [
        ("https://www.instagram.com/cedarmassage", "https://instagram.com/willowbodywork/"),
        ("https://linktr.ee/cedarmassage", "https://linktr.ee/willowbodywork"),
        ("https://m.facebook.com/cedarmassage", "https://facebook.com/willowbodywork"),
        ("https://www.yelp.com/biz/cedar-massage", "https://yelp.com/biz/willow-bodywork"),
        ("https://cedarmassage.vagaro.com", "https://willowbodywork.vagaro.com"),
    ],
)
def test_a_shared_or_aggregator_host_is_never_a_domain_match(
    migrated_connection: psycopg.Connection,
    category: CategoryConfig,
    crm_website: str,
    incoming_website: str,
) -> None:
    market_id = add_market(migrated_connection)
    add_lead(migrated_connection, market_id, email="a@one.example", website=crm_website)

    assert decide(migrated_connection, category, website=incoming_website) == ()
    # The same page, though, is still the same business.
    assert decide(migrated_connection, category, website=crm_website) == ()


def test_a_shared_host_lead_still_matches_on_its_other_keys(
    migrated_connection: psycopg.Connection, category: CategoryConfig
) -> None:
    market_id = add_market(migrated_connection)
    add_lead(
        migrated_connection,
        market_id,
        phone="(310) 555-0142",
        website="https://www.instagram.com/cedarmassage",
    )

    assert decide(
        migrated_connection,
        category,
        phone="310-555-0142",
        website="https://instagram.com/cedarmassage",
    ) == ("phone",)


def test_every_matching_key_is_reported(
    migrated_connection: psycopg.Connection, category: CategoryConfig
) -> None:
    market_id = add_market(migrated_connection)
    add_lead(
        migrated_connection,
        market_id,
        phone="(310) 555-0142",
        email="hello@cedarmassage.example",
        website="https://cedarmassage.example",
        google_place_id="place_1",
    )

    matched_on = decide(
        migrated_connection,
        category,
        phone="+13105550142",
        email="HELLO@cedarmassage.example",
        website="www.cedarmassage.example/book",
        google_place_id="place_1",
    )

    assert matched_on == ("google_place_id", "phone", "email", "website_domain")


def test_a_lead_can_match_two_different_crm_leads(
    migrated_connection: psycopg.Connection, category: CategoryConfig
) -> None:
    market_id = add_market(migrated_connection)
    add_lead(migrated_connection, market_id, phone="(310) 555-0142")
    add_lead(migrated_connection, market_id, email="hello@willow.example")

    assert decide(
        migrated_connection, category, phone="310-555-0142", email="hello@willow.example"
    ) == ("phone", "email")


def test_a_lead_without_keys_is_new(
    migrated_connection: psycopg.Connection, category: CategoryConfig
) -> None:
    market_id = add_market(migrated_connection)
    add_lead(migrated_connection, market_id, phone="(310) 555-0142")

    (decision,) = dedupe_against_crm(
        migrated_connection, [IncomingLead(business_name="Nameless Massage")], category
    )

    assert decision.keys == NormalizedKeys(None, None, None, None)
    assert not decision.is_duplicate


def test_a_batch_keeps_its_order_and_splits_new_from_duplicate(
    migrated_connection: psycopg.Connection, category: CategoryConfig
) -> None:
    market_id = add_market(migrated_connection)
    add_lead(migrated_connection, market_id, phone="(310) 555-0142")
    add_lead(migrated_connection, market_id, email="hello@cedar.example")
    incoming = [
        IncomingLead("Known by phone", phone="310 555 0142"),
        IncomingLead("Brand new", phone="310 555 0199", website="https://new.example"),
        IncomingLead("Known by email", email="Hello@Cedar.Example"),
        IncomingLead("Also new", email="hi@fresh.example"),
    ]

    decisions = dedupe_against_crm(migrated_connection, incoming, category)

    assert [decision.lead for decision in decisions] == incoming
    assert [decision.is_duplicate for decision in decisions] == [True, False, True, False]
    assert [lead.business_name for lead in new_leads(decisions)] == ["Brand new", "Also new"]


def test_an_empty_batch_needs_no_database(category: CategoryConfig) -> None:
    class ClosedConnection:
        def execute(self, *arguments: object, **keyword_arguments: object) -> None:
            raise AssertionError("no query expected")

    assert dedupe_against_crm(ClosedConnection(), [], category) == []  # type: ignore[arg-type]
