"""Search and page fetching: both backends faked with respx, the cache against real Postgres."""

import uuid
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

import httpx
import psycopg
import pytest
import respx
from pydantic import SecretStr

from leadgen.settings import Settings
from leadgen.web_research import (
    DUCKDUCKGO_HTML_URL,
    FIRECRAWL_BASE_URL,
    FirecrawlBackend,
    FreeWebBackend,
    PageFetchFailed,
    SearchResult,
    WebBackendMisconfigured,
    WebBackendOutOfCredit,
    WebBackendUnavailable,
    WebResearcher,
    build_web_researcher,
    firecrawl_search_credits,
    normalize_page_url,
    parse_duckduckgo_results,
)

USD_PER_CREDIT = Decimal("0.0032")
PAGE_URL = "https://serenity-mobile-massage.example/services"

# Long enough for trafilatura's main-content extractor; navigation and footer must be dropped.
SERVICES_HTML = """
<html><head><title>Serenity Mobile Massage | Services</title></head>
<body>
<nav><a href="/">Home</a> <a href="/book">Book now</a> <a href="/faq">FAQ</a></nav>
<main>
<h1>Mobile massage in Pasadena</h1>
<p>We bring a licensed massage therapist to your home, hotel or office anywhere in Pasadena,
Glendale and Burbank. Sessions run 60, 90 or 120 minutes and include a portable table, fresh
linens and oils. Book online or call us; same-day appointments are often available in the
afternoon.</p>
<p>Our therapists are certified in Swedish, deep tissue and prenatal massage, and every visit
is covered by liability insurance.</p>
</main>
<footer>Copyright 2026 Serenity Mobile Massage</footer>
</body></html>
"""

FIRECRAWL_SCRAPE_RESPONSE = {
    "success": True,
    "data": {
        "markdown": "# Mobile massage in Pasadena\n\nWe bring a licensed massage therapist to "
        "your home, hotel or office anywhere in Pasadena, Glendale and Burbank.",
        "metadata": {
            "title": "Serenity Mobile Massage | Services",
            "sourceURL": PAGE_URL,
            "url": PAGE_URL,
            "statusCode": 200,
        },
    },
}

FIRECRAWL_SEARCH_RESPONSE = {
    "success": True,
    "data": {
        "web": [
            {
                "url": "https://serenity-mobile-massage.example/",
                "title": "Serenity Mobile Massage",
                "description": "In-home massage across Pasadena and Glendale.",
            },
            {
                "url": "https://pasadena-wellness-directory.example/massage",
                "title": "Massage therapists in Pasadena",
                "description": "A directory of local therapists.",
            },
        ]
    },
    "id": "search-job-123",
    "creditsUsed": 2,
}


def firecrawl_backend(http_client: httpx.Client) -> FirecrawlBackend:
    return FirecrawlBackend("fc-test-key", usd_per_credit=USD_PER_CREDIT, http_client=http_client)


def ledger_rows(
    connection: psycopg.Connection,
) -> list[tuple[str, str | None, str, int, int, Decimal]]:
    return connection.execute(
        """
        SELECT service, model, endpoint, provider_units, call_number, cost_usd
        FROM cost_ledger ORDER BY call_number
        """
    ).fetchall()


def cache_rows(connection: psycopg.Connection) -> list[tuple[str, str]]:
    return connection.execute("SELECT url, backend FROM page_cache ORDER BY url").fetchall()


@pytest.fixture
def http_client() -> Iterator[httpx.Client]:
    with httpx.Client() as client:
        yield client


@pytest.fixture
def mocked_web() -> respx.MockRouter:
    """Every HTTP call must be declared here; anything else fails the test instead of leaving."""
    with respx.mock(assert_all_called=False) as router:
        yield router


# --- Firecrawl backend ---------------------------------------------------------------------


def test_firecrawl_search_sends_the_query_and_parses_web_results(
    migrated_connection: psycopg.Connection, mocked_web: respx.MockRouter, http_client: httpx.Client
) -> None:
    search_route = mocked_web.post(f"{FIRECRAWL_BASE_URL}/search").respond(
        json=FIRECRAWL_SEARCH_RESPONSE
    )
    researcher = WebResearcher(migrated_connection, firecrawl_backend(http_client))

    results = researcher.search("mobile massage Pasadena", limit=2)

    sent = search_route.calls.last.request
    assert sent.headers["Authorization"] == "Bearer fc-test-key"
    assert httpx.Request.read(sent) == (
        b'{"query":"mobile massage Pasadena","limit":2,"sources":["web"]}'
    )
    assert results == [
        SearchResult(
            "https://serenity-mobile-massage.example/",
            "Serenity Mobile Massage",
            "In-home massage across Pasadena and Glendale.",
        ),
        SearchResult(
            "https://pasadena-wellness-directory.example/massage",
            "Massage therapists in Pasadena",
            "A directory of local therapists.",
        ),
    ]


def test_firecrawl_search_is_ledgered_at_credits_times_price(
    migrated_connection: psycopg.Connection, mocked_web: respx.MockRouter, http_client: httpx.Client
) -> None:
    mocked_web.post(f"{FIRECRAWL_BASE_URL}/search").respond(json=FIRECRAWL_SEARCH_RESPONSE)
    run_id = uuid.uuid4()
    researcher = WebResearcher(migrated_connection, firecrawl_backend(http_client), run_id=run_id)

    researcher.search("mobile massage Pasadena")
    researcher.search("in-home massage Glendale")

    assert ledger_rows(migrated_connection) == [
        ("firecrawl", None, "/v2/search", 2, 1, Decimal("0.006400")),
        ("firecrawl", None, "/v2/search", 2, 2, Decimal("0.006400")),
    ]
    request_ids = migrated_connection.execute(
        "SELECT DISTINCT request_id, run_id FROM cost_ledger"
    ).fetchall()
    assert request_ids == [("search-job-123", run_id)]


@pytest.mark.parametrize(
    ("result_count", "credits"), [(0, 2), (1, 2), (10, 2), (11, 4), (20, 4), (21, 6)]
)
def test_search_credits_are_two_per_ten_results_rounded_up(result_count: int, credits: int) -> None:
    assert firecrawl_search_credits(result_count) == credits


def test_firecrawl_fetch_returns_markdown_and_charges_one_credit(
    migrated_connection: psycopg.Connection, mocked_web: respx.MockRouter, http_client: httpx.Client
) -> None:
    scrape_route = mocked_web.post(f"{FIRECRAWL_BASE_URL}/scrape").respond(
        json=FIRECRAWL_SCRAPE_RESPONSE
    )
    researcher = WebResearcher(migrated_connection, firecrawl_backend(http_client))

    page = researcher.fetch_page(PAGE_URL)

    sent_body = httpx.Request.read(scrape_route.calls.last.request)
    assert sent_body == (
        b'{"url":"https://serenity-mobile-massage.example/services",'
        b'"formats":["markdown"],"onlyMainContent":true}'
    )
    assert page.title == "Serenity Mobile Massage | Services"
    assert page.text.startswith("# Mobile massage in Pasadena")
    assert (page.backend, page.status_code, page.from_cache) == ("firecrawl", 200, False)
    assert ledger_rows(migrated_connection) == [
        ("firecrawl", None, "/v2/scrape", 1, 1, Decimal("0.003200"))
    ]


def test_firecrawl_fetch_of_a_missing_page_fails_uncached_but_is_still_charged(
    migrated_connection: psycopg.Connection, mocked_web: respx.MockRouter, http_client: httpx.Client
) -> None:
    # Firecrawl returns an error page as a result and bills it one credit like any other page.
    mocked_web.post(f"{FIRECRAWL_BASE_URL}/scrape").respond(
        json={
            "success": True,
            "creditsUsed": 1,
            "data": {"markdown": "", "metadata": {"statusCode": 404, "error": "Not Found"}},
        }
    )
    researcher = WebResearcher(migrated_connection, firecrawl_backend(http_client))

    with pytest.raises(PageFetchFailed, match="HTTP 404") as failure:
        researcher.fetch_page(PAGE_URL)

    assert failure.value.charge is not None and failure.value.charge.credits == 1
    assert cache_rows(migrated_connection) == []
    assert ledger_rows(migrated_connection) == [
        ("firecrawl", None, "/v2/scrape", 1, 1, Decimal("0.003200"))
    ]


def test_firecrawl_fetch_with_no_text_is_charged_even_without_credits_used(
    migrated_connection: psycopg.Connection, mocked_web: respx.MockRouter, http_client: httpx.Client
) -> None:
    # A 200 with nothing extractable, and no creditsUsed in the body: the published price applies.
    mocked_web.post(f"{FIRECRAWL_BASE_URL}/scrape").respond(
        json={"success": True, "data": {"markdown": "", "metadata": {"statusCode": 200}}}
    )
    researcher = WebResearcher(migrated_connection, firecrawl_backend(http_client))

    with pytest.raises(PageFetchFailed, match="no text extracted"):
        researcher.fetch_page(PAGE_URL)

    assert ledger_rows(migrated_connection) == [
        ("firecrawl", None, "/v2/scrape", 1, 1, Decimal("0.003200"))
    ]


def test_free_fetch_failure_carries_no_charge(
    migrated_connection: psycopg.Connection, mocked_web: respx.MockRouter, http_client: httpx.Client
) -> None:
    mocked_web.get(PAGE_URL).respond(status_code=404)
    researcher = WebResearcher(migrated_connection, FreeWebBackend(http_client=http_client))

    with pytest.raises(PageFetchFailed, match="HTTP 404") as failure:
        researcher.fetch_page(PAGE_URL)

    assert failure.value.charge is None
    assert ledger_rows(migrated_connection) == []


@pytest.mark.parametrize(
    ("status_code", "body", "expected_error", "expected_message"),
    [
        (401, {"success": False, "error": "Unauthorized"}, WebBackendMisconfigured, "API key"),
        (
            402,
            {"success": False, "error": "Insufficient credits", "code": "insufficient_credits"},
            WebBackendOutOfCredit,
            "out of credits",
        ),
        (429, {"error": "Rate limit exceeded"}, WebBackendUnavailable, "HTTP 429"),
        (
            500,
            {"success": False, "code": "UNKNOWN_ERROR", "error": "Unexpected"},
            WebBackendUnavailable,
            "HTTP 500",
        ),
    ],
)
def test_firecrawl_errors_become_plain_messages(
    mocked_web: respx.MockRouter,
    http_client: httpx.Client,
    status_code: int,
    body: dict,
    expected_error: type[Exception],
    expected_message: str,
) -> None:
    mocked_web.post(f"{FIRECRAWL_BASE_URL}/search").respond(status_code=status_code, json=body)

    with pytest.raises(expected_error, match=expected_message):
        firecrawl_backend(http_client).search("anything", limit=5)


def test_firecrawl_outage_falls_back_to_the_free_backend_without_a_charge(
    migrated_connection: psycopg.Connection, mocked_web: respx.MockRouter, http_client: httpx.Client
) -> None:
    mocked_web.post(f"{FIRECRAWL_BASE_URL}/scrape").respond(
        status_code=503, json={"success": False, "error": "unavailable"}
    )
    free_route = mocked_web.get(PAGE_URL).respond(html=SERVICES_HTML)
    researcher = WebResearcher(
        migrated_connection,
        firecrawl_backend(http_client),
        fallback=FreeWebBackend(http_client=http_client),
    )

    page = researcher.fetch_page(PAGE_URL)

    assert free_route.called
    assert page.backend == "free"
    assert "licensed massage therapist" in page.text
    assert ledger_rows(migrated_connection) == []
    assert cache_rows(migrated_connection) == [(PAGE_URL, "free")]


def test_invalid_key_is_not_hidden_by_the_fallback(
    migrated_connection: psycopg.Connection, mocked_web: respx.MockRouter, http_client: httpx.Client
) -> None:
    mocked_web.post(f"{FIRECRAWL_BASE_URL}/scrape").respond(status_code=401, json={"error": "no"})
    free_route = mocked_web.get(PAGE_URL).respond(html=SERVICES_HTML)
    researcher = WebResearcher(
        migrated_connection,
        firecrawl_backend(http_client),
        fallback=FreeWebBackend(http_client=http_client),
    )

    with pytest.raises(WebBackendMisconfigured, match="FIRECRAWL_API_KEY"):
        researcher.fetch_page(PAGE_URL)

    assert not free_route.called


# --- Free backend ---------------------------------------------------------------------------


def test_free_fetch_extracts_main_text_and_drops_navigation(
    migrated_connection: psycopg.Connection, mocked_web: respx.MockRouter, http_client: httpx.Client
) -> None:
    mocked_web.get(PAGE_URL).respond(html=SERVICES_HTML)
    researcher = WebResearcher(migrated_connection, FreeWebBackend(http_client=http_client))

    page = researcher.fetch_page(PAGE_URL)

    assert page.title == "Mobile massage in Pasadena"
    assert "Sessions run 60, 90 or 120 minutes" in page.text
    assert "Book now" not in page.text
    assert "Copyright" not in page.text
    assert (page.backend, page.status_code, page.final_url) == ("free", 200, PAGE_URL)
    assert ledger_rows(migrated_connection) == []


def test_free_fetch_follows_redirects_and_keeps_the_final_url(
    migrated_connection: psycopg.Connection, mocked_web: respx.MockRouter, http_client: httpx.Client
) -> None:
    moved_url = "http://serenity-mobile-massage.example/old-services"
    mocked_web.get(moved_url).respond(status_code=301, headers={"Location": PAGE_URL})
    mocked_web.get(PAGE_URL).respond(html=SERVICES_HTML)
    researcher = WebResearcher(migrated_connection, FreeWebBackend(http_client=http_client))

    page = researcher.fetch_page(moved_url)

    assert (page.url, page.final_url) == (moved_url, PAGE_URL)


@pytest.mark.parametrize("status_code", [404, 500])
def test_free_fetch_of_an_unreadable_page_fails_and_is_not_cached(
    migrated_connection: psycopg.Connection,
    mocked_web: respx.MockRouter,
    http_client: httpx.Client,
    status_code: int,
) -> None:
    mocked_web.get(PAGE_URL).respond(status_code=status_code)
    researcher = WebResearcher(migrated_connection, FreeWebBackend(http_client=http_client))

    with pytest.raises(PageFetchFailed, match=f"HTTP {status_code}"):
        researcher.fetch_page(PAGE_URL)

    assert cache_rows(migrated_connection) == []


def test_free_fetch_reports_connection_errors_plainly(
    migrated_connection: psycopg.Connection, mocked_web: respx.MockRouter, http_client: httpx.Client
) -> None:
    mocked_web.get(PAGE_URL).mock(side_effect=httpx.ConnectError("name resolution failed"))
    researcher = WebResearcher(migrated_connection, FreeWebBackend(http_client=http_client))

    with pytest.raises(PageFetchFailed, match="Could not fetch"):
        researcher.fetch_page(PAGE_URL)


def duckduckgo_results_html(*entries: tuple[str, str, str]) -> str:
    """The parts of DuckDuckGo's HTML results page the parser relies on."""
    items = []
    for url, title, snippet in entries:
        redirect = f"//duckduckgo.com/l/?uddg={quote(url, safe='')}&amp;rut=abc123"
        items.append(
            f'<div class="result"><h2 class="result__title">'
            f'<a rel="nofollow" class="result__a" href="{redirect}">{title}</a></h2>'
            f'<a class="result__snippet" href="{redirect}">{snippet}</a></div>'
        )
    return f"<html><body><div id='links'>{''.join(items)}</div></body></html>"


def test_free_search_parses_duckduckgo_results_and_unwraps_redirects(
    migrated_connection: psycopg.Connection, mocked_web: respx.MockRouter, http_client: httpx.Client
) -> None:
    search_route = mocked_web.post(DUCKDUCKGO_HTML_URL).respond(
        html=duckduckgo_results_html(
            (
                "https://serenity-mobile-massage.example/?ref=ddg",
                "Serenity <b>Mobile</b> Massage",
                "In-home massage across\n  Pasadena &amp; Glendale.",
            ),
            ("https://pasadena-wellness-directory.example/massage", "Directory", "Listings."),
            ("https://third-result.example/", "Third", "Beyond the limit."),
        )
    )
    researcher = WebResearcher(migrated_connection, FreeWebBackend(http_client=http_client))

    results = researcher.search("mobile massage Pasadena", limit=2)

    assert b"q=mobile+massage+Pasadena" in httpx.Request.read(search_route.calls.last.request)
    assert results == [
        SearchResult(
            "https://serenity-mobile-massage.example/?ref=ddg",
            "Serenity Mobile Massage",
            "In-home massage across Pasadena & Glendale.",
        ),
        SearchResult(
            "https://pasadena-wellness-directory.example/massage", "Directory", "Listings."
        ),
    ]
    assert ledger_rows(migrated_connection) == []


def test_duckduckgo_parser_keeps_direct_links_and_ignores_other_anchors() -> None:
    html = (
        '<a class="header__link" href="/about">About</a>'
        '<a class="result__a" href="https://direct.example/page">Direct</a>'
        '<a class="result__a" href="//protocol-relative.example/">Relative</a>'
        '<a class="result__a" href="javascript:void(0)">Junk</a>'
    )

    assert parse_duckduckgo_results(html) == [
        SearchResult("https://direct.example/page", "Direct", ""),
        SearchResult("https://protocol-relative.example/", "Relative", ""),
    ]


def test_free_search_treats_a_bot_challenge_as_unavailable(
    mocked_web: respx.MockRouter, http_client: httpx.Client
) -> None:
    mocked_web.post(DUCKDUCKGO_HTML_URL).respond(status_code=202, html="<html>checking</html>")

    with pytest.raises(WebBackendUnavailable, match="HTTP 202"):
        FreeWebBackend(http_client=http_client).search("anything", limit=5)


# --- Page cache -----------------------------------------------------------------------------


def test_second_fetch_within_seven_days_comes_from_the_cache(
    migrated_connection: psycopg.Connection, mocked_web: respx.MockRouter, http_client: httpx.Client
) -> None:
    scrape_route = mocked_web.post(f"{FIRECRAWL_BASE_URL}/scrape").respond(
        json=FIRECRAWL_SCRAPE_RESPONSE
    )
    researcher = WebResearcher(migrated_connection, firecrawl_backend(http_client))

    first = researcher.fetch_page(PAGE_URL)
    second = researcher.fetch_page(PAGE_URL)

    assert scrape_route.call_count == 1
    assert not first.from_cache and second.from_cache
    assert (second.text, second.title, second.backend) == (first.text, first.title, "firecrawl")
    assert len(ledger_rows(migrated_connection)) == 1


def test_cache_is_shared_across_researchers_and_backends(
    migrated_connection: psycopg.Connection, mocked_web: respx.MockRouter, http_client: httpx.Client
) -> None:
    mocked_web.get(PAGE_URL).respond(html=SERVICES_HTML)
    scrape_route = mocked_web.post(f"{FIRECRAWL_BASE_URL}/scrape").respond(
        json=FIRECRAWL_SCRAPE_RESPONSE
    )
    WebResearcher(migrated_connection, FreeWebBackend(http_client=http_client)).fetch_page(PAGE_URL)

    page = WebResearcher(migrated_connection, firecrawl_backend(http_client)).fetch_page(PAGE_URL)

    assert page.from_cache and page.backend == "free"
    assert not scrape_route.called
    assert ledger_rows(migrated_connection) == []


def test_stale_cache_entry_is_refetched_and_replaced(
    migrated_connection: psycopg.Connection, mocked_web: respx.MockRouter, http_client: httpx.Client
) -> None:
    scrape_route = mocked_web.post(f"{FIRECRAWL_BASE_URL}/scrape").respond(
        json=FIRECRAWL_SCRAPE_RESPONSE
    )
    researcher = WebResearcher(migrated_connection, firecrawl_backend(http_client))
    researcher.fetch_page(PAGE_URL)
    migrated_connection.execute(
        "UPDATE page_cache SET fetched_at = now() - interval '8 days', text = 'old text'"
    )

    page = researcher.fetch_page(PAGE_URL)

    assert scrape_route.call_count == 2
    assert not page.from_cache
    rows = migrated_connection.execute(
        "SELECT text, fetched_at > now() - interval '1 minute' FROM page_cache"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0].startswith("# Mobile massage") and rows[0][1] is True


def test_cache_key_ignores_fragments_and_host_case(
    migrated_connection: psycopg.Connection, mocked_web: respx.MockRouter, http_client: httpx.Client
) -> None:
    scrape_route = mocked_web.post(f"{FIRECRAWL_BASE_URL}/scrape").respond(
        json=FIRECRAWL_SCRAPE_RESPONSE
    )
    researcher = WebResearcher(migrated_connection, firecrawl_backend(http_client))

    researcher.fetch_page(PAGE_URL)
    researcher.fetch_page(f"{PAGE_URL}#pricing")
    page = researcher.fetch_page("  HTTPS://Serenity-Mobile-Massage.example/services  ")

    assert scrape_route.call_count == 1
    assert page.from_cache
    assert cache_rows(migrated_connection) == [(PAGE_URL, "firecrawl")]


def test_cache_key_keeps_the_query_string() -> None:
    assert normalize_page_url("HTTP://Example.COM/a?b=1#c") == "http://example.com/a?b=1"
    assert normalize_page_url("https://example.com/a?b=2") != normalize_page_url(
        "https://example.com/a?b=1"
    )


@pytest.mark.parametrize("bad_url", ["ftp://example.com/x", "example.com/page", "", "mailto:a@b.c"])
def test_non_http_urls_are_refused_before_any_fetch(
    migrated_connection: psycopg.Connection, http_client: httpx.Client, bad_url: str
) -> None:
    researcher = WebResearcher(migrated_connection, FreeWebBackend(http_client=http_client))

    with pytest.raises(ValueError, match="not an http"):
        researcher.fetch_page(bad_url)


# --- Schema and wiring ----------------------------------------------------------------------


def test_ledger_accepts_every_planned_service_and_nothing_else(
    migrated_connection: psycopg.Connection,
) -> None:
    def insert(service: str, *, model: str | None = None, endpoint: str | None = None) -> None:
        migrated_connection.execute(
            """
            INSERT INTO cost_ledger (run_id, service, model, endpoint, call_number, cost_usd)
            VALUES (%s, %s, %s, %s, 1, 0)
            """,
            (uuid.uuid4(), service, model, endpoint),
        )

    insert("claude", model="claude-haiku-5-5")
    for service in ("blitz", "apify", "exa", "firecrawl"):
        insert(service, endpoint="/v2/test")
    with pytest.raises(psycopg.errors.CheckViolation):
        insert("unknown_service", endpoint="/v2/test")


def test_ledger_still_ties_every_non_claude_row_to_an_endpoint(
    migrated_connection: psycopg.Connection,
) -> None:
    """The page-cache migration widens the service list without loosening the earlier rule."""
    constraint_names = {
        name
        for (name,) in migrated_connection.execute(
            "SELECT conname FROM pg_constraint WHERE conrelid = 'cost_ledger'::regclass"
        )
    }
    assert {"cost_ledger_service_check", "cost_ledger_call_identity"} <= constraint_names

    with pytest.raises(psycopg.errors.CheckViolation):
        migrated_connection.execute(
            """
            INSERT INTO cost_ledger (run_id, service, model, call_number, cost_usd)
            VALUES (%s, 'firecrawl', '/v2/scrape', 1, 0)
            """,
            (uuid.uuid4(),),
        )


def test_page_cache_rejects_non_http_urls_and_unknown_backends(
    migrated_connection: psycopg.Connection,
) -> None:
    def insert(url: str, backend: str) -> None:
        migrated_connection.execute(
            """
            INSERT INTO page_cache (url, final_url, text, status_code, backend)
            VALUES (%s, %s, 'text', 200, %s)
            """,
            (url, url, backend),
        )

    with pytest.raises(psycopg.errors.CheckViolation):
        insert("ftp://example.com/", "free")
    with pytest.raises(psycopg.errors.CheckViolation):
        insert("https://example.com/", "crawler")


def settings_with(**overrides: object) -> Settings:
    return Settings(
        database_url="postgresql://leadgen:unused@127.0.0.1:5433/unused",
        _env_file=None,
        **overrides,  # type: ignore[arg-type]
    )


def test_builder_picks_firecrawl_with_a_key_and_the_free_backend_as_fallback(
    migrated_connection: psycopg.Connection, http_client: httpx.Client
) -> None:
    settings = settings_with(firecrawl_api_key=SecretStr("fc-test-key"))

    researcher = build_web_researcher(migrated_connection, settings, http_client=http_client)

    assert isinstance(researcher.backend, FirecrawlBackend)
    assert isinstance(researcher.fallback, FreeWebBackend)


@pytest.mark.parametrize("key", [None, SecretStr(""), SecretStr("   ")])
def test_builder_picks_the_free_backend_without_a_key(
    migrated_connection: psycopg.Connection, http_client: httpx.Client, key: SecretStr | None
) -> None:
    settings = settings_with(firecrawl_api_key=key)

    researcher = build_web_researcher(migrated_connection, settings, http_client=http_client)

    assert isinstance(researcher.backend, FreeWebBackend)
    assert researcher.fallback is None


def test_builder_honours_prefer_free_even_with_a_key(
    migrated_connection: psycopg.Connection, http_client: httpx.Client
) -> None:
    settings = settings_with(firecrawl_api_key=SecretStr("fc-test-key"))

    researcher = build_web_researcher(
        migrated_connection, settings, http_client=http_client, prefer_free=True
    )

    assert isinstance(researcher.backend, FreeWebBackend)


def test_builder_owns_and_closes_the_client_it_opened(
    migrated_connection: psycopg.Connection,
) -> None:
    settings = settings_with(firecrawl_api_key=None)

    with build_web_researcher(migrated_connection, settings) as researcher:
        owned = researcher.owned_http_client
        assert isinstance(owned, httpx.Client) and not owned.is_closed

    assert owned.is_closed
    researcher.close()  # closing twice is harmless


def test_builder_leaves_an_injected_client_open(
    migrated_connection: psycopg.Connection, http_client: httpx.Client
) -> None:
    settings = settings_with(firecrawl_api_key=None)

    with build_web_researcher(migrated_connection, settings, http_client=http_client) as researcher:
        assert researcher.owned_http_client is None

    assert not http_client.is_closed


def test_settings_read_the_firecrawl_price_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", "postgresql://leadgen:unused@127.0.0.1:5433/unused")
    monkeypatch.setenv("FIRECRAWL_USD_PER_CREDIT", "0.00083")
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)

    settings = Settings()

    assert settings.firecrawl_usd_per_credit == Decimal("0.00083")
    assert settings.firecrawl_api_key is None
