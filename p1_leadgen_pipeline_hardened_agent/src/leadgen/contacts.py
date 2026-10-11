"""Phone and email for a lead, through BlitzAPI, cached per website domain and costed per call.

This is a plain function, not an agent: the input is a domain, the output is a contact, and no
reasoning happens in between. BlitzAPI keys everything on LinkedIn URLs, so one lookup is a short
fixed chain, each step one HTTP call, stopping at the first step that finds nothing:

1. website domain → company LinkedIn URL
2. company → the best-placed person (owner or founder first, then other decision-makers)
3. person → verified work email
4. person → direct phone (skipped when the key's plan has no phone endpoint)

Every metered call writes a `cost_ledger` row with the records BlitzAPI says it used. The result,
found or not, goes to `contact_cache`, so a fresh domain is never paid for twice.
"""

import random
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import psycopg
from psycopg.rows import dict_row

BLITZ_BASE_URL = "https://api.blitz-api.ai"
KEY_INFO_ENDPOINT = "/v2/account/key-info"
DOMAIN_TO_LINKEDIN_ENDPOINT = "/v2/enrichment/domain-to-linkedin"
DECISION_MAKER_ENDPOINT = "/v2/search/waterfall-icp-keyword"
EMAIL_ENDPOINT = "/v2/enrichment/email"
PHONE_ENDPOINT = "/v2/enrichment/phone"

# Ordered search tiers: who to contact at a small business, best first. Phrases in a tier are
# OR'd; all words of a phrase must appear in the job title.
DEFAULT_DECISION_MAKER_CASCADE: list[list[str]] = [
    ["Owner", "Founder", "Co-Founder", "CEO", "President", "Principal"],
    ["Director", "Manager", "Partner"],
]

DEFAULT_CACHE_MAX_AGE = timedelta(days=90)
RETRYABLE_STATUS_CODES = frozenset({429, 500, 503})


class BlitzApiError(Exception):
    """BlitzAPI did not return a usable answer."""


class BlitzAuthenticationError(BlitzApiError):
    """The API key is missing, invalid or unknown (HTTP 401 or 404)."""


class BlitzOutOfRecords(BlitzApiError):
    """The account has no records left on its plan (HTTP 402)."""


class BlitzRequestRejected(BlitzApiError):
    """The request body was wrong (HTTP 400 or 422); retrying the same request cannot help."""


class BlitzUnavailable(BlitzApiError):
    """Rate limited or failing server, still after retries, or a timeout."""


@dataclass(frozen=True)
class BlitzResponse:
    payload: dict[str, Any]
    records_used: int
    request_id: str | None


@dataclass(frozen=True)
class KeyInfo:
    valid: bool
    records_remaining: int | str
    allowed_apis: tuple[str, ...]
    max_requests_per_second: int | None


class BlitzClient:
    """Thin HTTP client: one method per BlitzAPI endpoint this project uses.

    Retries 429, 500 and 503 with exponential backoff and jitter; never retries a timeout, because
    the server may already have processed and billed the request.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = BLITZ_BASE_URL,
        timeout_seconds: float = 20.0,
        max_retries: int = 3,
        backoff_seconds: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = httpx.Client(
            base_url=base_url,
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            timeout=timeout_seconds,
        )
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds
        self._sleep = sleep
        self._key_info: KeyInfo | None = None
        self.last_records_remaining: int | str | None = None

    def key_info(self) -> KeyInfo:
        """The key's plan: valid, records left, endpoints allowed. Free; fetched once and kept."""
        if self._key_info is None:
            payload = self._request("GET", KEY_INFO_ENDPOINT).payload
            self._key_info = KeyInfo(
                valid=bool(payload.get("valid", False)),
                records_remaining=payload.get("records_remaining", 0),
                allowed_apis=tuple(payload.get("allowed_apis") or ()),
                max_requests_per_second=payload.get("max_requests_per_seconds"),
            )
        return self._key_info

    def allows(self, endpoint: str) -> bool:
        """Whether the plan includes `endpoint`. An empty allow-list is read as everything."""
        allowed_apis = self.key_info().allowed_apis
        return not allowed_apis or endpoint.removeprefix("/v2") in allowed_apis

    def domain_to_linkedin(self, domain: str) -> BlitzResponse:
        return self._request("POST", DOMAIN_TO_LINKEDIN_ENDPOINT, {"domain": domain})

    def find_decision_maker(
        self, company_linkedin_url: str, cascade: list[list[str]]
    ) -> BlitzResponse:
        body = {
            "company_linkedin_url": company_linkedin_url,
            "cascade": [{"include_title": titles} for titles in cascade],
            "max_results": 1,
        }
        return self._request("POST", DECISION_MAKER_ENDPOINT, body)

    def find_email(self, person_linkedin_url: str) -> BlitzResponse:
        return self._request("POST", EMAIL_ENDPOINT, {"person_linkedin_url": person_linkedin_url})

    def find_phone(self, person_linkedin_url: str) -> BlitzResponse:
        return self._request("POST", PHONE_ENDPOINT, {"person_linkedin_url": person_linkedin_url})

    def _request(
        self, method: str, endpoint: str, body: dict[str, Any] | None = None
    ) -> BlitzResponse:
        for attempt in range(self._max_retries + 1):
            try:
                response = self._http.request(method, endpoint, json=body)
            except httpx.TimeoutException as error:
                raise BlitzUnavailable(f"BlitzAPI {endpoint} timed out: {error}") from error
            except httpx.HTTPError as error:
                raise BlitzUnavailable(f"BlitzAPI {endpoint} unreachable: {error}") from error

            if response.status_code in RETRYABLE_STATUS_CODES and attempt < self._max_retries:
                # Full jitter: anywhere between zero and the doubling backoff, so retrying workers
                # do not hit the endpoint in lockstep.
                self._sleep(random.uniform(0, self._backoff_seconds * 2**attempt))
                continue
            return self._interpret(endpoint, response)
        raise AssertionError("unreachable: the loop returns or raises on its last attempt")

    def _interpret(self, endpoint: str, response: httpx.Response) -> BlitzResponse:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        message = payload.get("message") if isinstance(payload, dict) else None

        if response.status_code == 200:
            fair_usage = payload.get("fair_usage") or {}
            self.last_records_remaining = fair_usage.get(
                "records_remaining", self.last_records_remaining
            )
            return BlitzResponse(
                payload=payload,
                records_used=int(fair_usage.get("records_used") or 0),
                request_id=fair_usage.get("request_id"),
            )
        if response.status_code in (401, 404):
            raise BlitzAuthenticationError(
                "BlitzAPI rejected the API key. Check BLITZ_API_KEY in .env."
            )
        if response.status_code == 402:
            raise BlitzOutOfRecords(
                "The BlitzAPI account has no records left on its plan. "
                "Check https://app.blitz-api.ai before running enrichment again."
            )
        if response.status_code in (400, 422):
            problems = "; ".join(
                f"{problem.get('field')}: {problem.get('message')}"
                for problem in payload.get("errors") or []
            )
            raise BlitzRequestRejected(
                f"BlitzAPI rejected the request to {endpoint}: {message or response.text}"
                + (f" ({problems})" if problems else "")
            )
        if response.status_code in RETRYABLE_STATUS_CODES:
            raise BlitzUnavailable(
                f"BlitzAPI {endpoint} still answered {response.status_code} after "
                f"{self._max_retries} retries: {message or response.text}"
            )
        raise BlitzApiError(
            f"BlitzAPI {endpoint} answered {response.status_code}: {message or response.text}"
        )


@dataclass(frozen=True)
class ContactLookup:
    """What one domain lookup produced. `found` means a phone or an email was found."""

    domain: str
    found: bool
    company_linkedin_url: str | None
    company_name: str | None
    person_linkedin_url: str | None
    person_name: str | None
    person_title: str | None
    email: str | None
    phone: str | None
    records_used: int
    looked_up_at: datetime
    from_cache: bool


URL_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://")
WWW_PREFIX_OR_REST_OF_URL = re.compile(r"^www\.|[/:?#].*$")


def normalize_domain(website: str | None) -> str | None:
    """The bare domain of a website, exactly as the CRM's generated `website_domain` column.

    The two regular expressions are the ones in migration 0001, applied in the same order, so
    the cache key here and the dedupe key in `crm_leads` can never disagree. No scheme, no
    leading "www.", nothing from the first port, path, query or fragment on; lower case. None
    when nothing is left.
    """
    if website is None:
        return None
    text = URL_SCHEME.sub("", website.strip().lower(), count=1)
    text = WWW_PREFIX_OR_REST_OF_URL.sub("", text)
    return text or None


def enrich_contact(
    website: str,
    *,
    connection: psycopg.Connection,
    client: BlitzClient,
    run_id: uuid.UUID,
    usd_per_record: Decimal = Decimal(0),
    cache_max_age: timedelta = DEFAULT_CACHE_MAX_AGE,
    cascade: list[list[str]] | None = None,
) -> ContactLookup:
    """Phone and email for the business at `website`, from the cache or from BlitzAPI.

    A cached lookup younger than `cache_max_age` costs nothing. Otherwise the chain runs, every
    metered call is written to `cost_ledger` under `run_id` as it happens (an aborted chain still
    has its spend recorded), and the outcome replaces the cache row.
    """
    domain = normalize_domain(website)
    if domain is None:
        raise ValueError(f"no domain in website {website!r}")

    cached = _read_cache(connection, domain)
    if cached is not None and cached.looked_up_at >= _now(connection) - cache_max_age:
        return cached

    lookup = _run_lookup_chain(
        domain,
        connection=connection,
        client=client,
        run_id=run_id,
        usd_per_record=usd_per_record,
        cascade=cascade or DEFAULT_DECISION_MAKER_CASCADE,
    )
    return _write_cache(connection, lookup)


def _run_lookup_chain(
    domain: str,
    *,
    connection: psycopg.Connection,
    client: BlitzClient,
    run_id: uuid.UUID,
    usd_per_record: Decimal,
    cascade: list[list[str]],
) -> ContactLookup:
    outcome: dict[str, Any] = {
        "company_linkedin_url": None,
        "company_name": None,
        "person_linkedin_url": None,
        "person_name": None,
        "person_title": None,
        "email": None,
        "phone": None,
        "records_used": 0,
    }

    def metered(response: BlitzResponse, endpoint: str) -> dict[str, Any]:
        _record_blitz_call(connection, run_id, endpoint, response, usd_per_record)
        outcome["records_used"] += response.records_used
        return response.payload

    company = metered(client.domain_to_linkedin(domain), DOMAIN_TO_LINKEDIN_ENDPOINT)
    if company.get("found") and company.get("company_linkedin_url"):
        outcome["company_linkedin_url"] = company["company_linkedin_url"]
        outcome["company_name"] = company.get("company_name")

        search = metered(
            client.find_decision_maker(outcome["company_linkedin_url"], cascade),
            DECISION_MAKER_ENDPOINT,
        )
        person = _first_person(search)
        if person is not None:
            outcome["person_linkedin_url"] = person.get("linkedin_url")
            outcome["person_name"] = person.get("full_name")
            outcome["person_title"] = _current_title(person)

    if outcome["person_linkedin_url"]:
        email = metered(client.find_email(outcome["person_linkedin_url"]), EMAIL_ENDPOINT)
        if email.get("found"):
            outcome["email"] = email.get("email") or None
        if client.allows(PHONE_ENDPOINT):
            phone = metered(client.find_phone(outcome["person_linkedin_url"]), PHONE_ENDPOINT)
            if phone.get("found"):
                outcome["phone"] = phone.get("phone") or None

    return ContactLookup(
        domain=domain,
        found=bool(outcome["email"] or outcome["phone"]),
        looked_up_at=_now(connection),
        from_cache=False,
        **outcome,
    )


def _first_person(search_payload: dict[str, Any]) -> dict[str, Any] | None:
    results = search_payload.get("results") or []
    if not results:
        return None
    first = results[0]
    # The waterfall search wraps each hit as {"icp", "ranking", "person"}; a flat person is
    # tolerated so the parser also fits the employee-finder shape.
    person = first.get("person", first)
    return person if person.get("linkedin_url") else None


def _current_title(person: dict[str, Any]) -> str | None:
    for experience in person.get("experiences") or []:
        if experience.get("job_is_current") and experience.get("job_title"):
            return experience["job_title"]
    return person.get("headline")


def _record_blitz_call(
    connection: psycopg.Connection,
    run_id: uuid.UUID,
    endpoint: str,
    response: BlitzResponse,
    usd_per_record: Decimal,
) -> int:
    """Append one ledger row; returns its call number within the run.

    The call number is the next free one for the run, computed inside the insert. Two writers on
    the same run can still pick the same number, which the unique constraint rejects; then retry.
    """
    parameters = {
        "run_id": run_id,
        "endpoint": endpoint,
        "provider_units": response.records_used,
        "cost_usd": usd_per_record * response.records_used,
        "request_id": response.request_id,
    }
    for _attempt in range(5):
        try:
            row = connection.execute(
                """
                INSERT INTO cost_ledger
                    (run_id, service, endpoint, call_number, provider_units, cost_usd, request_id)
                SELECT %(run_id)s, 'blitz', %(endpoint)s, coalesce(max(call_number), 0) + 1,
                    %(provider_units)s, %(cost_usd)s, %(request_id)s
                FROM cost_ledger WHERE run_id = %(run_id)s
                RETURNING call_number
                """,
                parameters,
            ).fetchone()
        except psycopg.errors.UniqueViolation:
            continue
        assert row is not None
        return row[0]
    raise RuntimeError(f"could not number the ledger row for run {run_id} after 5 attempts")


CACHE_COLUMNS = (
    "domain, found, company_linkedin_url, company_name, person_linkedin_url, person_name, "
    "person_title, email, phone, records_used, looked_up_at"
)


def _read_cache(connection: psycopg.Connection, domain: str) -> ContactLookup | None:
    with connection.cursor(row_factory=dict_row) as cursor:
        row = cursor.execute(
            f"SELECT {CACHE_COLUMNS} FROM contact_cache WHERE domain = %s", (domain,)
        ).fetchone()
    return None if row is None else ContactLookup(from_cache=True, **row)


def _write_cache(connection: psycopg.Connection, lookup: ContactLookup) -> ContactLookup:
    connection.execute(
        f"""
        INSERT INTO contact_cache ({CACHE_COLUMNS})
        VALUES (%(domain)s, %(found)s, %(company_linkedin_url)s, %(company_name)s,
            %(person_linkedin_url)s, %(person_name)s, %(person_title)s, %(email)s, %(phone)s,
            %(records_used)s, %(looked_up_at)s)
        ON CONFLICT (domain) DO UPDATE SET
            found = excluded.found,
            company_linkedin_url = excluded.company_linkedin_url,
            company_name = excluded.company_name,
            person_linkedin_url = excluded.person_linkedin_url,
            person_name = excluded.person_name,
            person_title = excluded.person_title,
            email = excluded.email,
            phone = excluded.phone,
            records_used = excluded.records_used,
            looked_up_at = excluded.looked_up_at
        """,
        {
            "domain": lookup.domain,
            "found": lookup.found,
            "company_linkedin_url": lookup.company_linkedin_url,
            "company_name": lookup.company_name,
            "person_linkedin_url": lookup.person_linkedin_url,
            "person_name": lookup.person_name,
            "person_title": lookup.person_title,
            "email": lookup.email,
            "phone": lookup.phone,
            "records_used": lookup.records_used,
            "looked_up_at": lookup.looked_up_at,
        },
    )
    return lookup


def _now(connection: psycopg.Connection) -> datetime:
    """The database clock, so freshness is judged where `looked_up_at` was stamped."""
    row = connection.execute("SELECT now()").fetchone()
    assert row is not None
    return row[0]
