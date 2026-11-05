"""Free contact extraction from cached page text, and the extract stage that uses it."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from leadgen.contact_extraction import extract_email, extract_phone
from leadgen.orchestrator import Pipeline
from leadgen.plans import PlannedCell, approve_plan, create_plan, start_run
from leadgen.raw_leads import RawLead, persist_leads, read_raw_leads
from leadgen.settings import Settings
from leadgen.web_research import FetchedPage, write_cached_page
from tests.crm_builders import add_market


@pytest.mark.parametrize(
    ("text", "phone"),
    [
        ("Call us: (602) 555-0142 to book.", "(602) 555-0142"),
        ("Text +1 480.555.0199 anytime", "(480) 555-0199"),
        ("Bookings 623-555-0101 or email", "(623) 555-0101"),
        ("Order #12345678901 · est. 2015", None),
        ("Release v2.602.555.0142", None),
        ("Call 1 (602) 555-0142", "(602) 555-0142"),
        ("No numbers here", None),
    ],
)
def test_phone_is_read_from_page_text(text, phone):
    assert extract_phone(text) == phone


def test_email_prefers_the_business_domain_and_skips_junk():
    text = (
        "Site by studio@webagency.com · noreply@juniperplanning.com · "
        "Questions? Planner@JuniperPlanning.com."
    )
    assert extract_email(text, "https://juniperplanning.com") == "planner@juniperplanning.com"
    assert extract_email("Write to hello@gmail.com.", "https://juniperplanning.com") == (
        "hello@gmail.com"
    )
    assert extract_email("noreply@juniperplanning.com only", "https://juniperplanning.com") is None


@pytest.fixture
def running(migrated_connection):
    market_id = add_market(migrated_connection)
    plan = create_plan(
        migrated_connection,
        [PlannedCell(market_id, "Los Angeles, CA", "financial planner", 5, Decimal("1"))],
    )
    approve_plan(migrated_connection, plan.plan_id, Decimal("3"), "operator")
    return migrated_connection, market_id, start_run(migrated_connection, plan.plan_id)


def cache(connection, url, text):
    write_cached_page(
        connection, FetchedPage(url, url, "Home", text, 200, "exa", datetime.now(UTC))
    )


def test_extract_stage_fills_missing_contacts_from_cached_homepages(running, empty_database_url):
    connection, market_id, run = running
    persist_leads(
        connection,
        run.run_id,
        market_id,
        "exa",
        [
            RawLead(
                "juniperplanning.com", "Juniper Planning", website="https://juniperplanning.com"
            ),
            RawLead("uncached.com", "Uncached Planner", website="https://uncached.com"),
        ],
    )
    persist_leads(
        connection,
        run.run_id,
        market_id,
        "google_maps",
        [RawLead("place-1", "Maps Planner", phone="3105550101", website="https://mapsplanner.com")],
    )
    cache(
        connection,
        "https://juniperplanning.com/",
        "Call (310) 555-0177 · planner@juniperplanning.com",
    )
    cache(connection, "https://mapsplanner.com/", "Call (310) 555-0999 · hi@mapsplanner.com")

    settings = Settings(_env_file=None, database_url=empty_database_url)
    result = asyncio.run(Pipeline(connection, run, settings).run_extract())

    assert result == {"pages_read": 2, "phones_found": 1, "emails_found": 2}
    contacts = {
        lead["business_name"]: (lead["phone"], lead["email"])
        for lead in read_raw_leads(connection, run.run_id)
    }
    assert contacts == {
        "Juniper Planning": ("(310) 555-0177", "planner@juniperplanning.com"),
        "Uncached Planner": (None, None),
        # A Maps phone is never replaced; only the missing email is filled in.
        "Maps Planner": ("3105550101", "hi@mapsplanner.com"),
    }
    assert connection.execute("SELECT count(*) FROM cost_ledger").fetchone() == (0,)
