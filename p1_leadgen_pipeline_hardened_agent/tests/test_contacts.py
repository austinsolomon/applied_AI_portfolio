"""Contact enrichment through a faked BlitzAPI: the lookup chain, the cache and the ledger."""

import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

import httpx
import psycopg
import pytest
import respx

from leadgen.contacts import (
    DECISION_MAKER_ENDPOINT,
    DOMAIN_TO_LINKEDIN_ENDPOINT,
    EMAIL_ENDPOINT,
    KEY_INFO_ENDPOINT,
    PHONE_ENDPOINT,
    BlitzAuthenticationError,
    BlitzClient,
    BlitzOutOfRecords,
    BlitzRequestRejected,
    BlitzUnavailable,
    enrich_contact,
    normalize_domain,
)
from tests.crm_builders import add_lead, add_market

BASE_URL = "https://blitz.test"
COMPANY_URL = "https://www.linkedin.com/company/cedar-mobile-massage"
PERSON_URL = "https://www.linkedin.com/in/jordan-cedar"


def fair_usage(records_used: int, request_id: str = "req-1") -> dict[str, Any]:
    return {
        "records_used": records_used,
        "records_remaining": 950 - records_used,
        "next_reset_at": "2026-11-01T00:00:00.000Z",
        "rate_limit": {"requests_per_second": 10, "remaining_this_second": 9},
        "request_id": request_id,
    }


KEY_INFO_ALL_ENDPOINTS = {
    "valid": True,
    "id": "key_test",
    "records_remaining": 950,
    "next_reset_at": "2026-11-01T00:00:00.000Z",
    "max_requests_per_seconds": 10,
    "allowed_apis": [
        "/enrichment/domain-to-linkedin",
        "/search/waterfall-icp-keyword",
        "/enrichment/email",
        "/enrichment/phone",
    ],
    "active_plans": [{"name": "Unlimited Phone Numbers", "status": "active"}],
}

COMPANY_FOUND = {
    "found": True,
    "company_linkedin_url": COMPANY_URL,
    "company_name": "Cedar Mobile Massage",
    "other": [],
    "fair_usage": fair_usage(1, "req-company"),
}
COMPANY_NOT_FOUND = {"found": False, "fair_usage": fair_usage(1, "req-company")}

PERSON_FOUND = {
    "company_linkedin_url": COMPANY_URL,
    "max_results": 1,
    "results_length": 1,
    "results": [
        {
            "icp": 1,
            "ranking": 1,
            "person": {
                "first_name": "Jordan",
                "last_name": "Cedar",
                "full_name": "Jordan Cedar",
                "headline": "Owner | @Cedar Mobile Massage",
                "linkedin_url": PERSON_URL,
                "experiences": [
                    {
                        "company_name": "Cedar Mobile Massage",
                        "job_title": "Owner",
                        "company_linkedin_url": COMPANY_URL,
                        "job_is_current": True,
                    }
                ],
            },
        }
    ],
    "fair_usage": fair_usage(1, "req-person"),
}
PERSON_NOT_FOUND = {
    "company_linkedin_url": COMPANY_URL,
    "max_results": 1,
    "results_length": 0,
    "results": [],
    "fair_usage": fair_usage(0, "req-person"),
}

EMAIL_FOUND = {
    "found": True,
    "email": "jordan@cedar-massage.example",
    "all_emails": [{"email": "jordan@cedar-massage.example", "job_order_in_profile": 1}],
    "fair_usage": fair_usage(1, "req-email"),
}
EMAIL_NOT_FOUND = {"found": False, "email": None, "fair_usage": fair_usage(0, "req-email")}

PHONE_FOUND = {"found": True, "phone": "+13105550142", "fair_usage": fair_usage(1, "req-phone")}
PHONE_NOT_FOUND = {"found": False, "phone": None, "fair_usage": fair_usage(0, "req-phone")}


class FakeBlitz:
    """Routes for the endpoints the chain calls, each answering with a fixed payload."""

    def __init__(self, mock_router: respx.MockRouter) -> None:
        self.router = mock_router
        self.key_info = mock_router.get(f"{BASE_URL}{KEY_INFO_ENDPOINT}").mock(
            return_value=httpx.Response(200, json=KEY_INFO_ALL_ENDPOINTS)
        )
        self.company = mock_router.post(f"{BASE_URL}{DOMAIN_TO_LINKEDIN_ENDPOINT}")
        self.person = mock_router.post(f"{BASE_URL}{DECISION_MAKER_ENDPOINT}")
        self.email = mock_router.post(f"{BASE_URL}{EMAIL_ENDPOINT}")
        self.phone = mock_router.post(f"{BASE_URL}{PHONE_ENDPOINT}")

    def everything_found(self) -> None:
        self.company.mock(return_value=httpx.Response(200, json=COMPANY_FOUND))
        self.person.mock(return_value=httpx.Response(200, json=PERSON_FOUND))
        self.email.mock(return_value=httpx.Response(200, json=EMAIL_FOUND))
        self.phone.mock(return_value=httpx.Response(200, json=PHONE_FOUND))

    @property
    def metered_calls(self) -> int:
        return sum(
            route.call_count for route in (self.company, self.person, self.email, self.phone)
        )


@pytest.fixture
def blitz(respx_mock: respx.MockRouter) -> FakeBlitz:
    return FakeBlitz(respx_mock)


@pytest.fixture
def client() -> BlitzClient:
    sleeps: list[float] = []
    blitz_client = BlitzClient("test-key", base_url=BASE_URL, sleep=sleeps.append)
    blitz_client.sleeps = sleeps  # type: ignore[attr-defined]
    return blitz_client


def ledger_rows(connection: psycopg.Connection, run_id: uuid.UUID) -> list[tuple[Any, ...]]:
    return connection.execute(
        """
        SELECT call_number, service, model, endpoint, provider_units, cost_usd, request_id
        FROM cost_ledger WHERE run_id = %s ORDER BY call_number
        """,
        (run_id,),
    ).fetchall()


def test_full_chain_finds_email_and_phone_and_ledgers_every_call(
    migrated_connection: psycopg.Connection, blitz: FakeBlitz, client: BlitzClient
) -> None:
    blitz.everything_found()
    run_id = uuid.uuid4()

    lookup = enrich_contact(
        "https://www.Cedar-Massage.example/book",
        connection=migrated_connection,
        client=client,
        run_id=run_id,
        usd_per_record=Decimal("0.05"),
    )

    assert lookup.domain == "cedar-massage.example"
    assert lookup.found is True
    assert lookup.from_cache is False
    assert (lookup.email, lookup.phone) == ("jordan@cedar-massage.example", "+13105550142")
    assert (lookup.person_name, lookup.person_title) == ("Jordan Cedar", "Owner")
    assert lookup.records_used == 4
    assert blitz.company.calls.last.request.content == b'{"domain":"cedar-massage.example"}'
    assert blitz.company.calls.last.request.headers["x-api-key"] == "test-key"

    assert ledger_rows(migrated_connection, run_id) == [
        (1, "blitz", None, DOMAIN_TO_LINKEDIN_ENDPOINT, 1, Decimal("0.05"), "req-company"),
        (2, "blitz", None, DECISION_MAKER_ENDPOINT, 1, Decimal("0.05"), "req-person"),
        (3, "blitz", None, EMAIL_ENDPOINT, 1, Decimal("0.05"), "req-email"),
        (4, "blitz", None, PHONE_ENDPOINT, 1, Decimal("0.05"), "req-phone"),
    ]


def test_second_lookup_of_the_same_domain_is_served_from_the_cache(
    migrated_connection: psycopg.Connection, blitz: FakeBlitz, client: BlitzClient
) -> None:
    blitz.everything_found()
    first_run, second_run = uuid.uuid4(), uuid.uuid4()
    enrich_contact(
        "cedar-massage.example", connection=migrated_connection, client=client, run_id=first_run
    )
    calls_after_first = blitz.metered_calls

    cached = enrich_contact(
        "http://www.cedar-massage.example:443/?utm=x",
        connection=migrated_connection,
        client=client,
        run_id=second_run,
    )

    assert cached.from_cache is True
    assert (cached.email, cached.phone) == ("jordan@cedar-massage.example", "+13105550142")
    assert blitz.metered_calls == calls_after_first == 4
    assert ledger_rows(migrated_connection, second_run) == []


def test_a_stale_cache_row_is_looked_up_again_and_replaced(
    migrated_connection: psycopg.Connection, blitz: FakeBlitz, client: BlitzClient
) -> None:
    blitz.everything_found()
    enrich_contact(
        "cedar-massage.example",
        connection=migrated_connection,
        client=client,
        run_id=uuid.uuid4(),
    )
    migrated_connection.execute(
        """
        UPDATE contact_cache
        SET looked_up_at = now() - interval '91 days', email = 'old@old.example'
        """
    )

    refreshed = enrich_contact(
        "cedar-massage.example",
        connection=migrated_connection,
        client=client,
        run_id=uuid.uuid4(),
        cache_max_age=timedelta(days=90),
    )

    assert refreshed.from_cache is False
    assert refreshed.email == "jordan@cedar-massage.example"
    assert blitz.metered_calls == 8
    assert migrated_connection.execute("SELECT count(*) FROM contact_cache").fetchone() == (1,)


def test_unknown_domain_stops_after_one_call_and_caches_the_miss(
    migrated_connection: psycopg.Connection, blitz: FakeBlitz, client: BlitzClient
) -> None:
    blitz.company.mock(return_value=httpx.Response(200, json=COMPANY_NOT_FOUND))
    run_id = uuid.uuid4()

    lookup = enrich_contact(
        "unknown.example", connection=migrated_connection, client=client, run_id=run_id
    )
    again = enrich_contact(
        "unknown.example", connection=migrated_connection, client=client, run_id=uuid.uuid4()
    )

    assert lookup.found is False
    assert lookup.company_linkedin_url is None
    assert [row[3] for row in ledger_rows(migrated_connection, run_id)] == [
        DOMAIN_TO_LINKEDIN_ENDPOINT
    ]
    assert again.from_cache is True
    assert blitz.metered_calls == 1


def test_company_without_a_decision_maker_stops_after_two_calls(
    migrated_connection: psycopg.Connection, blitz: FakeBlitz, client: BlitzClient
) -> None:
    blitz.company.mock(return_value=httpx.Response(200, json=COMPANY_FOUND))
    blitz.person.mock(return_value=httpx.Response(200, json=PERSON_NOT_FOUND))

    lookup = enrich_contact(
        "cedar-massage.example",
        connection=migrated_connection,
        client=client,
        run_id=uuid.uuid4(),
    )

    assert lookup.found is False
    assert lookup.company_name == "Cedar Mobile Massage"
    assert lookup.person_linkedin_url is None
    assert blitz.metered_calls == 2
    assert blitz.person.calls.last.request.content == (
        b'{"company_linkedin_url":"' + COMPANY_URL.encode() + b'",'
        b'"cascade":[{"include_title":["Owner","Founder","Co-Founder","CEO","President",'
        b'"Principal"]},{"include_title":["Director","Manager","Partner"]}],"max_results":1}'
    )


def test_email_found_but_no_phone_is_still_a_find(
    migrated_connection: psycopg.Connection, blitz: FakeBlitz, client: BlitzClient
) -> None:
    blitz.everything_found()
    blitz.phone.mock(return_value=httpx.Response(200, json=PHONE_NOT_FOUND))

    lookup = enrich_contact(
        "cedar-massage.example",
        connection=migrated_connection,
        client=client,
        run_id=uuid.uuid4(),
    )

    assert lookup.found is True
    assert (lookup.email, lookup.phone) == ("jordan@cedar-massage.example", None)
    assert lookup.records_used == 3


def test_phone_is_skipped_when_the_plan_has_no_phone_endpoint(
    migrated_connection: psycopg.Connection, blitz: FakeBlitz, client: BlitzClient
) -> None:
    blitz.everything_found()
    trial_key = {**KEY_INFO_ALL_ENDPOINTS, "allowed_apis": ["/enrichment/email"]}
    blitz.key_info.mock(return_value=httpx.Response(200, json=trial_key))

    lookup = enrich_contact(
        "cedar-massage.example",
        connection=migrated_connection,
        client=client,
        run_id=uuid.uuid4(),
    )

    assert (lookup.email, lookup.phone) == ("jordan@cedar-massage.example", None)
    assert blitz.phone.call_count == 0
    assert blitz.key_info.call_count == 1


def test_rate_limit_is_retried_with_backoff_and_ledgered_once(
    migrated_connection: psycopg.Connection, blitz: FakeBlitz, client: BlitzClient
) -> None:
    blitz.company.side_effect = [
        httpx.Response(429, json={"success": False, "message": "Rate limit exceeded"}),
        httpx.Response(503, json={"success": False, "message": "Service unavailable"}),
        httpx.Response(200, json=COMPANY_NOT_FOUND),
    ]
    run_id = uuid.uuid4()

    enrich_contact("retry.example", connection=migrated_connection, client=client, run_id=run_id)

    assert blitz.company.call_count == 3
    assert len(client.sleeps) == 2  # type: ignore[attr-defined]
    assert all(0 <= pause <= 1.0 for pause in client.sleeps)  # type: ignore[attr-defined]
    assert len(ledger_rows(migrated_connection, run_id)) == 1


def test_rate_limit_that_never_clears_is_reported_after_the_retry_budget(
    migrated_connection: psycopg.Connection, blitz: FakeBlitz
) -> None:
    blitz.company.mock(
        return_value=httpx.Response(429, json={"success": False, "message": "Rate limit exceeded"})
    )
    client = BlitzClient("test-key", base_url=BASE_URL, max_retries=2, sleep=lambda _: None)

    with pytest.raises(BlitzUnavailable, match="after 2 retries"):
        enrich_contact(
            "retry.example", connection=migrated_connection, client=client, run_id=uuid.uuid4()
        )

    assert blitz.company.call_count == 3


def test_timeout_is_not_retried(
    migrated_connection: psycopg.Connection, blitz: FakeBlitz, client: BlitzClient
) -> None:
    blitz.company.side_effect = httpx.ReadTimeout("read timed out")

    with pytest.raises(BlitzUnavailable, match="timed out"):
        enrich_contact(
            "slow.example", connection=migrated_connection, client=client, run_id=uuid.uuid4()
        )

    assert blitz.company.call_count == 1


@pytest.mark.parametrize(
    ("status_code", "payload", "error_type", "message_part"),
    [
        (
            401,
            {"success": False, "message": "Invalid API key"},
            BlitzAuthenticationError,
            "BLITZ_API_KEY",
        ),
        (
            402,
            {"success": False, "message": "Fair Use limit reached"},
            BlitzOutOfRecords,
            "no records",
        ),
        (
            422,
            {
                "success": False,
                "message": "Invalid request body, 1 error.",
                "errors": [{"field": "body", "message": 'Unknown field "website".'}],
            },
            BlitzRequestRejected,
            'body: Unknown field "website".',
        ),
    ],
)
def test_client_errors_become_plain_messages(
    migrated_connection: psycopg.Connection,
    blitz: FakeBlitz,
    client: BlitzClient,
    status_code: int,
    payload: dict[str, Any],
    error_type: type[Exception],
    message_part: str,
) -> None:
    blitz.company.mock(return_value=httpx.Response(status_code, json=payload))

    with pytest.raises(error_type, match=message_part):
        enrich_contact(
            "cedar-massage.example",
            connection=migrated_connection,
            client=client,
            run_id=uuid.uuid4(),
        )

    assert migrated_connection.execute("SELECT count(*) FROM contact_cache").fetchone() == (0,)


def test_a_failed_chain_keeps_the_spend_it_already_made(
    migrated_connection: psycopg.Connection, blitz: FakeBlitz, client: BlitzClient
) -> None:
    blitz.company.mock(return_value=httpx.Response(200, json=COMPANY_FOUND))
    blitz.person.mock(return_value=httpx.Response(500, json={"success": False, "message": "boom"}))
    run_id = uuid.uuid4()

    with pytest.raises(BlitzUnavailable):
        enrich_contact(
            "cedar-massage.example", connection=migrated_connection, client=client, run_id=run_id
        )

    assert [row[3] for row in ledger_rows(migrated_connection, run_id)] == [
        DOMAIN_TO_LINKEDIN_ENDPOINT
    ]


def test_call_numbers_continue_after_claude_calls_in_the_same_run(
    migrated_connection: psycopg.Connection, blitz: FakeBlitz, client: BlitzClient
) -> None:
    blitz.company.mock(return_value=httpx.Response(200, json=COMPANY_NOT_FOUND))
    run_id = uuid.uuid4()
    migrated_connection.execute(
        """
        INSERT INTO cost_ledger (run_id, service, model, call_number, input_tokens,
            cache_creation_input_tokens, cache_read_input_tokens, output_tokens, cost_usd)
        VALUES (%s, 'claude', 'claude-haiku-5-5', 1, 10, 0, 0, 5, 0.000002)
        """,
        (run_id,),
    )

    enrich_contact("cedar.example", connection=migrated_connection, client=client, run_id=run_id)

    assert [(row[0], row[1]) for row in ledger_rows(migrated_connection, run_id)] == [
        (1, "claude"),
        (2, "blitz"),
    ]


def test_website_without_a_domain_is_refused_before_any_call(
    migrated_connection: psycopg.Connection, blitz: FakeBlitz, client: BlitzClient
) -> None:
    with pytest.raises(ValueError, match="no domain"):
        enrich_contact(
            "https://", connection=migrated_connection, client=client, run_id=uuid.uuid4()
        )

    assert blitz.metered_calls == 0


@pytest.mark.parametrize(
    "website",
    [
        "https://www.Cedar-Massage.example/book-now?ref=maps#top",
        "http://cedar-massage.example:8080/",
        "  WWW.cedar-massage.example  ",
        "cedar-massage.example",
        "https://booking.cedar-massage.example/",
        "https://www.instagram.com/cedarmassage/",
    ],
)
def test_normalize_domain_matches_the_crm_generated_column(
    migrated_connection: psycopg.Connection, website: str
) -> None:
    market_id = add_market(migrated_connection)
    lead_id = add_lead(migrated_connection, market_id, phone="310-555-0142", website=website)
    crm_domain = migrated_connection.execute(
        "SELECT website_domain FROM crm_leads WHERE lead_id = %s", (lead_id,)
    ).fetchone()

    assert crm_domain is not None
    assert normalize_domain(website) == crm_domain[0]


def test_normalize_domain_of_nothing_is_none() -> None:
    assert normalize_domain(None) is None
    assert normalize_domain("   ") is None
    assert normalize_domain("https://") is None


def test_ledger_accepts_blitz_rows_and_still_requires_a_model_for_claude(
    migrated_connection: psycopg.Connection,
) -> None:
    run_id = uuid.uuid4()
    migrated_connection.execute(
        """
        INSERT INTO cost_ledger (run_id, service, endpoint, call_number, provider_units, cost_usd)
        VALUES (%s, 'blitz', '/v2/enrichment/email', 1, 1, 0)
        """,
        (run_id,),
    )

    with pytest.raises(psycopg.errors.CheckViolation):
        migrated_connection.execute(
            """
            INSERT INTO cost_ledger (run_id, service, call_number, cost_usd)
            VALUES (%s, 'claude', 2, 0)
            """,
            (run_id,),
        )
    with pytest.raises(psycopg.errors.CheckViolation):
        migrated_connection.execute(
            """
            INSERT INTO cost_ledger (run_id, service, call_number, cost_usd)
            VALUES (%s, 'blitz', 3, 0)
            """,
            (run_id,),
        )
    with pytest.raises(psycopg.errors.CheckViolation):
        migrated_connection.execute(
            """
            INSERT INTO cost_ledger (run_id, service, endpoint, call_number, cost_usd)
            VALUES (%s, 'exa', '/search', 4, 0)
            """,
            (run_id,),
        )
