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
        "Site by studio@webagency.com · noreply@saffrontable.com · "
        "Questions? Chef@SaffronTable.com."
    )
    assert extract_email(text, "https://saffrontable.com") == "chef@saffrontable.com"
    assert extract_email("Write to hello@gmail.com.", "https://saffrontable.com") == (
        "hello@gmail.com"
    )
    assert extract_email("noreply@saffrontable.com only", "https://saffrontable.com") is None


@pytest.fixture
def running(migrated_connection):
    market_id = add_market(migrated_connection)
    plan = create_plan(
        migrated_connection,
        [PlannedCell(market_id, "Los Angeles, CA", "private chef", 5, Decimal("1"))],
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
            RawLead("saffrontable.com", "Saffron Table", website="https://saffrontable.com"),
            RawLead("uncached.com", "Uncached Chef", website="https://uncached.com"),
        ],
    )
    persist_leads(
        connection,
        run.run_id,
        market_id,
        "google_maps",
        [RawLead("place-1", "Maps Chef", phone="3105550101", website="https://mapschef.com")],
    )
    cache(connection, "https://saffrontable.com/", "Call (310) 555-0177 · chef@saffrontable.com")
    cache(connection, "https://mapschef.com/", "Call (310) 555-0999 · hi@mapschef.com")

    settings = Settings(_env_file=None, database_url=empty_database_url)
    result = asyncio.run(Pipeline(connection, run, settings).run_extract())

    assert result == {"pages_read": 2, "phones_found": 1, "emails_found": 2}
    contacts = {
        lead["business_name"]: (lead["phone"], lead["email"])
        for lead in read_raw_leads(connection, run.run_id)
    }
    assert contacts == {
        "Saffron Table": ("(310) 555-0177", "chef@saffrontable.com"),
        "Uncached Chef": (None, None),
        # A Maps phone is never replaced; only the missing email is filled in.
        "Maps Chef": ("3105550101", "hi@mapschef.com"),
    }
    assert connection.execute("SELECT count(*) FROM cost_ledger").fetchone() == (0,)
