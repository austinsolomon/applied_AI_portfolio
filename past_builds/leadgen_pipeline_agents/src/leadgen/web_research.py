"""Web search and page fetching behind one small interface, with a Postgres page cache.

`WebResearcher.search(query)` and `WebResearcher.fetch_page(url)` are what the qualification stages
call. Three backends implement them:

- `ExaWebBackend` (paid, `EXA_API_KEY`, the default when set): Exa `search` with page text for
  search results, and Exa `get_contents` for pages. Every call records Exa's reported
  `cost_dollars` in `cost_ledger` as service `exa`. When Exa returns no text for a page (a
  JavaScript-rendered site it could not read), and only then, the page is fetched once with
  Firecrawl if a key is set, or with the free backend otherwise. Searches never fall back.
- `FirecrawlBackend` (paid, `FIRECRAWL_API_KEY`): Firecrawl's `/v2/search` and `/v2/scrape`, which
  handle JavaScript-rendered sites and anti-bot pages. Every call is charged in credits and written
  to `cost_ledger` as service `firecrawl`, including a scrape that comes back as an error page
  (Firecrawl bills a 403 or 404 like any other page), so the ledger matches the provider's usage.
- `FreeWebBackend` (no key): plain httpx GET with trafilatura main-text extraction, and DuckDuckGo's
  HTML results page for search. It costs nothing and is the backend tests and offline runs use.

Pages go through `page_cache`: a URL fetched within the last `PAGE_CACHE_MAX_AGE` is served from
Postgres without touching the network or the ledger. When Firecrawl is unavailable (rate limited,
timed out, server error) the researcher falls back to the free backend for that call; an invalid key
or an empty credit balance is reported instead, because silently continuing would hide a problem
the operator must fix.

`build_web_researcher` opens an `httpx.Client` when none is injected; the researcher then owns it,
so use the researcher as a context manager (or call `close()`) to release the connection pool.
"""

import math
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from html import unescape
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

import httpx
import psycopg
import trafilatura
from psycopg.rows import class_row

from leadgen.audit import EXA_CONTENTS, EXA_SEARCH, FIRECRAWL_RENDER, audit
from leadgen.public_urls import validate_public_url
from leadgen.settings import Settings

FIRECRAWL_BASE_URL = "https://api.firecrawl.dev/v2"
DUCKDUCKGO_HTML_URL = "https://html.duckduckgo.com/html/"
USER_AGENT = "leadgen-agent/0.1 (+https://github.com/austinsolomon/applied_AI_portfolio)"

PAGE_CACHE_MAX_AGE = timedelta(days=7)
BACKEND_LABELS = {"exa": "Exa API", "firecrawl": "Firecrawl API", "free": "Free web (httpx)"}
# The audit actor for each backend's searches and page reads. Exa searches and reads pages;
# Firecrawl renders the pages Exa cannot read. The free backend is not a paid call: no row.
SEARCH_AUDIT_ACTORS = {"exa": EXA_SEARCH}
FETCH_AUDIT_ACTORS = {"exa": EXA_CONTENTS, "firecrawl": FIRECRAWL_RENDER}
DEFAULT_SEARCH_LIMIT = 5
REQUEST_TIMEOUT_SECONDS = 30.0
# Stored page text is capped so one oversized page cannot bloat the cache or a later prompt.
MAX_PAGE_TEXT_CHARACTERS = 100_000
# Firecrawl's published prices: one credit per scraped page; a search costs two credits per ten
# results, rounded up. The response's own creditsUsed wins when present.
FIRECRAWL_CREDITS_PER_SCRAPE = 1
FIRECRAWL_SEARCH_CREDITS_PER_BLOCK = 2
FIRECRAWL_SEARCH_RESULTS_PER_BLOCK = 10
# Exa reports cost_dollars per request; this per-call ceiling is reserved before the call and
# recorded, marked estimated, if Exa omits the figure.
EXA_WEB_CEILING_PER_CALL = Decimal("0.02")
EXA_SEARCH_TEXT_CHARACTERS = 1500
EXA_PAGE_TEXT_CHARACTERS = 6000


@dataclass(frozen=True)
class Charge:
    """What one paid backend call cost, for the ledger."""

    service: str
    endpoint: str
    credits: int
    cost_usd: Decimal
    request_id: str | None = None
    cost_basis: str = "provider_reported"


class WebResearchError(Exception):
    """A backend could not answer, with a message fit for the operator."""


class WebBackendMisconfigured(WebResearchError):
    """The key is missing or rejected; nothing will work until it is fixed."""


class WebBackendOutOfCredit(WebResearchError):
    """The paid backend's balance is empty; the account needs topping up."""


class WebBackendUnavailable(WebResearchError):
    """A transient failure (rate limit, timeout, server error); another backend may still work."""


class PageNeedsRendering(WebBackendUnavailable):
    """Exa returned no text for a page, typically one built by JavaScript. A live renderer
    (Firecrawl) may still read it. Exa's charge for the attempt travels with the exception."""

    def __init__(self, message: str, *, charge: "Charge | None" = None) -> None:
        super().__init__(message)
        self.charge = charge


class PageFetchFailed(WebResearchError):
    """The page itself could not be read (unreachable host, HTTP error, no extractable text).

    A paid backend may still have billed the attempt (Firecrawl charges for an error page), so the
    charge rides on the exception for the researcher to ledger before it re-raises.
    """

    def __init__(self, message: str, *, charge: Charge | None = None) -> None:
        super().__init__(message)
        self.charge = charge


@dataclass(frozen=True)
class SearchResult:
    url: str
    title: str
    description: str


@dataclass(frozen=True)
class FetchedPage:
    url: str
    final_url: str
    title: str | None
    text: str
    status_code: int
    backend: str
    fetched_at: datetime
    from_cache: bool = False


@dataclass(frozen=True)
class SearchResponse:
    results: list[SearchResult]
    charge: Charge | None = None


@dataclass(frozen=True)
class FetchResponse:
    page: FetchedPage
    charge: Charge | None = None


class WebBackend(Protocol):
    name: str

    def search(self, query: str, *, limit: int, site: str | None = None) -> SearchResponse: ...

    def fetch_page(self, url: str) -> FetchResponse: ...


def normalize_page_url(url: str) -> str:
    """The cache key: whitespace trimmed, scheme and host lowercased, fragment dropped."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(f"not an http(s) URL: {url!r}")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ""))


def firecrawl_search_credits(result_count: int) -> int:
    blocks = max(1, math.ceil(result_count / FIRECRAWL_SEARCH_RESULTS_PER_BLOCK))
    return blocks * FIRECRAWL_SEARCH_CREDITS_PER_BLOCK


class FirecrawlBackend:
    """Firecrawl's hosted API. The HTTP client is injected so tests can fake the endpoints."""

    name = "firecrawl"

    def __init__(
        self,
        api_key: str,
        *,
        usd_per_credit: Decimal,
        http_client: httpx.Client,
        base_url: str = FIRECRAWL_BASE_URL,
    ) -> None:
        if not api_key.strip():
            raise WebBackendMisconfigured("FIRECRAWL_API_KEY is empty")
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._usd_per_credit = usd_per_credit
        self._http = http_client
        self._base_url = base_url.rstrip("/")

    def search(self, query: str, *, limit: int, site: str | None = None) -> SearchResponse:
        query = f"site:{site} {query}" if site else query
        body = self._post("/search", {"query": query, "limit": limit, "sources": [{"type": "web"}]})
        web_results = body.get("data", {}).get("web", [])
        results = [
            SearchResult(
                url=item["url"],
                title=item.get("title") or "",
                description=item.get("description") or "",
            )
            for item in web_results
            if item.get("url")
        ]
        credits = int(body.get("creditsUsed") or firecrawl_search_credits(len(results)))
        return SearchResponse(results, self._charge("/search", credits, body.get("id")))

    def fetch_page(self, url: str) -> FetchResponse:
        body = self._post("/scrape", {"url": url, "formats": ["markdown"], "onlyMainContent": True})
        data = body.get("data", {})
        metadata = data.get("metadata", {})
        status_code = int(metadata.get("statusCode") or 200)
        text = (data.get("markdown") or "").strip()
        # Firecrawl bills an error page (403, 404) the same as a readable one, so the charge is
        # fixed before the page is judged and travels with the failure when it is unusable.
        credits = int(body.get("creditsUsed") or FIRECRAWL_CREDITS_PER_SCRAPE)
        charge = self._charge("/scrape", credits, None)
        if status_code >= 400 or not text:
            raise PageFetchFailed(
                f"Firecrawl could not read {url}: HTTP {status_code}, "
                f"{metadata.get('error') or 'no text extracted'}",
                charge=charge,
            )
        page = FetchedPage(
            url=url,
            final_url=metadata.get("url") or metadata.get("sourceURL") or url,
            title=metadata.get("title") or None,
            text=text[:MAX_PAGE_TEXT_CHARACTERS],
            status_code=status_code,
            backend=self.name,
            fetched_at=datetime.now(UTC),
        )
        return FetchResponse(page, charge)

    def _charge(self, path: str, credits: int, request_id: str | None) -> Charge:
        # The ledger names the endpoint as the API path, e.g. "/v2/scrape", so it can be
        # reconciled against Firecrawl's own usage figures.
        return Charge(
            service=self.name,
            endpoint=f"{urlsplit(self._base_url).path}{path}",
            credits=credits,
            cost_usd=self._usd_per_credit * credits,
            request_id=request_id,
        )

    def _post(self, path: str, payload: dict) -> dict:
        try:
            response = self._http.post(
                f"{self._base_url}{path}",
                json=payload,
                headers=self._headers,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as error:
            raise WebBackendUnavailable(f"Firecrawl request failed: {error}") from error
        if response.status_code == 401:
            raise WebBackendMisconfigured(
                "Firecrawl rejected the API key. Check FIRECRAWL_API_KEY."
            )
        if response.status_code == 402:
            raise WebBackendOutOfCredit("The Firecrawl account is out of credits.")
        if path == "/scrape" and response.status_code == 403:
            # Unsupported sites are a completed page failure, with unknown billing.
            raise PageFetchFailed("Firecrawl does not support this evidence site")
        if response.status_code == 429 or response.status_code >= 500:
            raise WebBackendUnavailable(
                f"Firecrawl is unavailable right now (HTTP {response.status_code}): "
                f"{_error_message(response)}"
            )
        if response.status_code >= 400:
            raise WebResearchError(
                f"Firecrawl refused the request (HTTP {response.status_code}): "
                f"{_error_message(response)}"
            )
        body = response.json()
        if not body.get("success", True):
            if path == "/scrape":
                # A dead domain (DNS failure) or unreachable page: the page is unreadable, and
                # qualification goes on without it. Billing is unknown, so no charge is attached.
                raise PageFetchFailed(f"Firecrawl could not read the page: {body.get('error')}")
            raise WebResearchError(f"Firecrawl reported a failure: {body.get('error')}")
        return body


def _error_message(response: httpx.Response) -> str:
    try:
        return str(response.json().get("error") or response.text)
    except ValueError:
        return response.text


class ExaWebBackend:
    """Exa for evidence: `search` with result text, `get_contents` for pages. `client` is an
    `exa_py.Exa` (or a fake with the same two methods)."""

    name = "exa"

    def __init__(
        self, client, *, ceiling_usd: Decimal = EXA_WEB_CEILING_PER_CALL, sleep=time.sleep
    ) -> None:
        self._client = client
        self._ceiling = ceiling_usd
        self._sleep = sleep

    def search(self, query: str, *, limit: int, site: str | None = None) -> SearchResponse:
        # Exa restricts a search to one site with include_domains, never a site: operator.
        options = {"include_domains": [site]} if site else {}
        response = self._call(
            "search",
            lambda: self._client.search(
                query,
                type="auto",
                num_results=limit,
                contents={"text": {"maxCharacters": EXA_SEARCH_TEXT_CHARACTERS}},
                **options,
            ),
        )
        results = [
            SearchResult(
                url=result.url,
                title=result.title or "",
                description=_collapse_whitespace(result.text or ""),
            )
            for result in response.results
            if result.url
        ]
        return SearchResponse(results, self._charge("search", response))

    def fetch_page(self, url: str) -> FetchResponse:
        response = self._call(
            "contents",
            lambda: self._client.get_contents(
                [url],
                # Exa's stored copy is used when it has one: forcing a live recrawl made most
                # small sites come back empty in the 2026-11-04 run.
                text={"maxCharacters": EXA_PAGE_TEXT_CHARACTERS},
            ),
        )
        charge = self._charge("contents", response)
        result = response.results[0] if response.results else None
        text = (getattr(result, "text", None) or "").strip()
        if not text:
            statuses = getattr(response, "statuses", None) or []
            detail = ", ".join(str(getattr(item, "status", item)) for item in statuses)
            raise PageNeedsRendering(
                f"Exa returned no text for {url} ({detail or 'no result'})", charge=charge
            )
        page = FetchedPage(
            url=url,
            final_url=result.url or url,
            title=result.title or None,
            text=text[:MAX_PAGE_TEXT_CHARACTERS],
            status_code=200,
            backend=self.name,
            fetched_at=datetime.now(UTC),
        )
        return FetchResponse(page, charge)

    def _call(self, endpoint: str, request):
        # A rate-limited request is refused before any work, so it is not billed and is safe to
        # repeat: two retries with a short backoff.
        for delay in (1, 3, None):
            try:
                return request()
            except Exception as error:
                if "status code 429" in str(error) and delay is not None:
                    self._sleep(delay)
                    continue
                self._raise_for(endpoint, error)

    def _raise_for(self, endpoint: str, error: Exception):
        message = str(error)
        if "status code 401" in message or "status code 403" in message:
            raise WebBackendMisconfigured("Exa rejected the API key. Check EXA_API_KEY.") from error
        if "status code 402" in message:
            raise WebBackendOutOfCredit("The Exa account is out of credits.") from error
        raise WebBackendUnavailable(f"Exa {endpoint} failed: {message[:200]}") from error

    def _charge(self, endpoint: str, response) -> Charge:
        reported = getattr(response, "cost_dollars", None)
        total = reported.get("total") if isinstance(reported, dict) else None
        if reported is not None and not isinstance(reported, dict):
            total = getattr(reported, "total", None)
        if total is None:
            return Charge(self.name, endpoint, 1, self._ceiling, cost_basis="estimated")
        return Charge(self.name, endpoint, 1, Decimal(str(total)))


class FreeWebBackend:
    """No key, no cost: httpx + trafilatura for pages, DuckDuckGo's HTML results for search."""

    name = "free"

    def __init__(self, *, http_client: httpx.Client) -> None:
        self._http = http_client

    def search(self, query: str, *, limit: int, site: str | None = None) -> SearchResponse:
        query = f"site:{site} {query}" if site else query
        try:
            response = self._http.post(
                DUCKDUCKGO_HTML_URL,
                data={"q": query},
                headers={"User-Agent": USER_AGENT},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as error:
            raise WebBackendUnavailable(f"DuckDuckGo request failed: {error}") from error
        if response.status_code != 200:
            raise WebBackendUnavailable(
                f"DuckDuckGo answered HTTP {response.status_code} instead of results"
            )
        return SearchResponse(parse_duckduckgo_results(response.text)[:limit])

    def fetch_page(self, url: str) -> FetchResponse:
        current_url = url
        try:
            for _ in range(6):
                validate_public_url(current_url)
                with self._http.stream(
                    "GET",
                    current_url,
                    headers={"User-Agent": USER_AGENT},
                    follow_redirects=False,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                ) as response:
                    if response.is_redirect:
                        current_url = urljoin(current_url, response.headers.get("location", ""))
                        continue
                    if response.status_code >= 400:
                        raise PageFetchFailed(f"Could not fetch {url}: HTTP {response.status_code}")
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > 2_000_000:
                            raise PageFetchFailed("Evidence page exceeds the two-megabyte limit")
                        chunks.append(chunk)
                    html = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
                    break
            else:
                raise PageFetchFailed("Evidence page redirected too many times")
        except (httpx.HTTPError, ValueError) as error:
            raise PageFetchFailed(f"Could not fetch {url}: {error}") from error
        title, text = extract_main_text(html, url=url)
        if not text:
            raise PageFetchFailed(f"No readable text found at {url}")
        page = FetchedPage(
            url=url,
            final_url=str(response.url),
            title=title,
            text=text[:MAX_PAGE_TEXT_CHARACTERS],
            status_code=response.status_code,
            backend=self.name,
            fetched_at=datetime.now(UTC),
        )
        return FetchResponse(page)


def extract_main_text(html: str, *, url: str) -> tuple[str | None, str]:
    """Main content of an HTML page as plain text, plus its title, with navigation stripped."""
    document = trafilatura.bare_extraction(
        html, url=url, with_metadata=True, favor_recall=True, include_comments=False
    )
    if document is None:
        # Pages too short or too unusual for the main extractor still get their visible text.
        return None, (trafilatura.html2txt(html) or "").strip()
    return document.title or None, (document.text or "").strip()


class _DuckDuckGoResultParser(HTMLParser):
    """Collects result links and snippets from DuckDuckGo's HTML results page."""

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._capturing: str | None = None

    def handle_starttag(self, tag: str, attributes: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attribute_map = dict(attributes)
        css_classes = (attribute_map.get("class") or "").split()
        href = attribute_map.get("href") or ""
        if "result__a" in css_classes:
            self.results.append({"url": _unwrap_duckduckgo_redirect(href), "title": ""})
            self._capturing = "title"
        elif "result__snippet" in css_classes and self.results:
            self.results[-1].setdefault("description", "")
            self._capturing = "description"

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._capturing = None

    def handle_data(self, data: str) -> None:
        if self._capturing and self.results:
            self.results[-1][self._capturing] = self.results[-1].get(self._capturing, "") + data


def _unwrap_duckduckgo_redirect(href: str) -> str:
    """DuckDuckGo links through `//duckduckgo.com/l/?uddg=<encoded target>`; return the target."""
    parts = urlsplit(href)
    if parts.path.startswith("/l/") and "uddg" in parse_qs(parts.query):
        return parse_qs(parts.query)["uddg"][0]
    if href.startswith("//"):
        return f"https:{href}"
    return href


def parse_duckduckgo_results(html: str) -> list[SearchResult]:
    parser = _DuckDuckGoResultParser()
    parser.feed(html)
    return [
        SearchResult(
            url=item["url"],
            title=_collapse_whitespace(unescape(item.get("title", ""))),
            description=_collapse_whitespace(unescape(item.get("description", ""))),
        )
        for item in parser.results
        if item["url"].startswith("http")
    ]


def _collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def write_cached_page(connection: psycopg.Connection, page: FetchedPage) -> None:
    """Store a page under its normalized URL. Sourcing uses this too, so text Exa returned with a
    search result is already cached when qualification asks for that page."""
    connection.execute(
        """
        INSERT INTO page_cache (url, final_url, title, text, status_code, backend, fetched_at)
        VALUES (%(url)s, %(final_url)s, %(title)s, %(text)s, %(status_code)s, %(backend)s,
                %(fetched_at)s)
        ON CONFLICT (url) DO UPDATE SET
            final_url = EXCLUDED.final_url, title = EXCLUDED.title, text = EXCLUDED.text,
            status_code = EXCLUDED.status_code, backend = EXCLUDED.backend,
            fetched_at = EXCLUDED.fetched_at
        """,
        {
            "url": page.url,
            "final_url": page.final_url,
            "title": page.title,
            "text": page.text,
            "status_code": page.status_code,
            "backend": page.backend,
            "fetched_at": page.fetched_at,
        },
    )


@dataclass
class WebResearcher:
    """The interface the pipeline uses: cached pages, ledgered spend, free fallback."""

    connection: psycopg.Connection
    backend: WebBackend
    run_id: uuid.UUID = field(default_factory=uuid.uuid4)
    fallback: WebBackend | None = None
    cache_max_age: timedelta = PAGE_CACHE_MAX_AGE
    # Which failures send a call to the fallback, and whether searches may fall back at all.
    # With Exa as the backend only an unreadable page does (PageNeedsRendering).
    fallback_on: type[WebBackendUnavailable] = WebBackendUnavailable
    fallback_searches: bool = True
    # Set by `build_web_researcher` when it opened the client itself; an injected client stays
    # the caller's to close.
    owned_http_client: httpx.Client | None = None

    def __enter__(self) -> "WebResearcher":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        """Release the HTTP client this researcher owns, if any. Safe to call twice."""
        if self.owned_http_client is not None:
            self.owned_http_client.close()

    def search(
        self, query: str, *, limit: int = DEFAULT_SEARCH_LIMIT, site: str | None = None
    ) -> list[SearchResult]:
        """Web results for `query`, or only pages on `site` (a domain) when it is given."""
        backend = self.backend
        fell_back_from = None
        try:
            response = backend.search(query, limit=limit, site=site)
        except WebBackendUnavailable as failure:
            if (
                self.fallback is None
                or not self.fallback_searches
                or not isinstance(failure, self.fallback_on)
            ):
                self._audit(
                    SEARCH_AUDIT_ACTORS.get(backend.name),
                    f"search fails: {failure}",
                    query,
                    site=site,
                )
                raise
            fell_back_from, backend = backend.name, self.fallback
            response = backend.search(query, limit=limit, site=site)
        cost = self._record_charge(response.charge)
        self._audit(
            SEARCH_AUDIT_ACTORS.get(backend.name),
            f"{'on-site' if site else 'web'} search returns {len(response.results)} results",
            query,
            site=site,
            limit=limit,
            urls=[result.url for result in response.results],
            cost_usd=cost,
            fell_back_from=fell_back_from,
        )
        return response.results

    def fetch_page(self, url: str) -> FetchedPage:
        cache_key = normalize_page_url(url)
        cached = self._read_cache(cache_key)
        if cached is not None:
            # A cached page costs nothing and is not an audit row of its own.
            return cached
        try:
            response = self._fetch_uncached(cache_key)
        except PageFetchFailed as failure:
            # A dead or forbidden page still cost a credit on the paid backend; ledger it so the
            # run's spend matches the provider's usage, then report the failure as before.
            cost = self._record_charge(failure.charge)
            self._audit(
                FETCH_AUDIT_ACTORS.get(
                    failure.charge.service if failure.charge else self.backend.name
                ),
                f"cannot read the page: {failure}",
                cache_key,
                cost_usd=cost,
            )
            raise
        cost = self._record_charge(response.charge)
        self._write_cache(response.page)
        self._audit(
            FETCH_AUDIT_ACTORS.get(response.page.backend),
            f"returns {len(response.page.text)} characters of page text",
            cache_key,
            final_url=response.page.final_url,
            cost_usd=cost,
            from_cache=False,
        )
        return response.page

    def _fetch_uncached(self, cache_key: str) -> FetchResponse:
        try:
            return self.backend.fetch_page(cache_key)
        except WebBackendUnavailable as failure:
            if self.fallback is None or not isinstance(failure, self.fallback_on):
                raise
            # The primary may have billed the attempt (Exa charges for an empty page).
            cost = self._record_charge(getattr(failure, "charge", None))
            self._audit(
                FETCH_AUDIT_ACTORS.get(self.backend.name),
                f"cannot read the page ({failure}); hands it to "
                + BACKEND_LABELS.get(self.fallback.name, self.fallback.name),
                cache_key,
                cost_usd=cost,
            )
            return self.fallback.fetch_page(cache_key)

    def _read_cache(self, cache_key: str) -> FetchedPage | None:
        with self.connection.cursor(row_factory=class_row(FetchedPage)) as cursor:
            cursor.execute(
                """
                SELECT url, final_url, title, text, status_code, backend, fetched_at,
                       true AS from_cache
                FROM page_cache
                WHERE url = %(url)s AND fetched_at > now() - %(max_age)s
                """,
                {"url": cache_key, "max_age": self.cache_max_age},
            )
            return cursor.fetchone()

    def _write_cache(self, page: FetchedPage) -> None:
        write_cached_page(self.connection, page)

    def _audit(self, actor: str | None, action: str, target: str, **detail) -> None:
        """One audit row for a paid backend's call; a backend with no audit actor has none."""
        if actor is not None:
            audit(self.connection, actor, action, target, run_id=self.run_id, **detail)

    def _record_charge(self, charge: Charge | None) -> Decimal | None:
        """Ledger one call's charge and return its cost for the call's audit row."""
        if charge is None:
            return None
        # A non-Claude ledger row names its endpoint and the provider's own units (credits here)
        # and has no model; the token columns keep their zero defaults. Call numbers are unique
        # per run; the next one is read in the same statement.
        with self.connection.transaction():
            self.connection.execute("SELECT 1 FROM runs WHERE run_id=%s FOR UPDATE", (self.run_id,))
            self.connection.execute(
                """
                INSERT INTO cost_ledger
                    (run_id, service, endpoint, call_number, provider_units, cost_usd, request_id,
                     cost_basis)
                SELECT %(run_id)s, %(service)s, %(endpoint)s, coalesce(max(call_number), 0) + 1,
                       %(provider_units)s, %(cost_usd)s, %(request_id)s, %(cost_basis)s
                FROM cost_ledger WHERE run_id = %(run_id)s
                """,
                {
                    "run_id": self.run_id,
                    "service": charge.service,
                    "endpoint": charge.endpoint,
                    "provider_units": charge.credits,
                    "cost_usd": charge.cost_usd,
                    "request_id": charge.request_id,
                    "cost_basis": charge.cost_basis,
                },
            )
        return charge.cost_usd


def build_web_researcher(
    connection: psycopg.Connection,
    settings: Settings,
    *,
    http_client: httpx.Client | None = None,
    run_id: uuid.UUID | None = None,
    prefer_free: bool = False,
    exa_client=None,
) -> WebResearcher:
    """Exa when `EXA_API_KEY` is set, with Firecrawl (or the free backend) only for pages Exa
    cannot read; otherwise Firecrawl with the free backend as fallback; otherwise free.

    Without an injected `http_client` the researcher opens one and owns it: close the researcher
    (it is a context manager) when done.
    """
    owned_http_client: httpx.Client | None = None
    if http_client is None:
        owned_http_client = http_client = httpx.Client()
    free_backend = FreeWebBackend(http_client=http_client)
    backend: WebBackend = free_backend
    fallback: WebBackend | None = None
    fallback_on: type[WebBackendUnavailable] = WebBackendUnavailable
    # An empty key line in .env means "not configured", the same as no line.
    firecrawl_key = _configured(settings.firecrawl_api_key)
    exa_key = _configured(settings.exa_api_key)
    firecrawl = (
        FirecrawlBackend(
            firecrawl_key,
            usd_per_credit=settings.firecrawl_usd_per_credit,
            http_client=http_client,
        )
        if firecrawl_key
        else None
    )
    if prefer_free:
        pass
    elif exa_key or exa_client is not None:
        if exa_client is None:
            from exa_py import Exa

            exa_client = Exa(exa_key)
        backend = ExaWebBackend(exa_client)
        fallback = firecrawl or free_backend
        fallback_on = PageNeedsRendering
    elif firecrawl is not None:
        backend, fallback = firecrawl, free_backend
    return WebResearcher(
        connection=connection,
        backend=backend,
        fallback=fallback,
        fallback_on=fallback_on,
        fallback_searches=not isinstance(backend, ExaWebBackend),
        run_id=run_id or uuid.uuid4(),
        owned_http_client=owned_http_client,
    )


def _configured(secret) -> str:
    return secret.get_secret_value().strip() if secret is not None else ""
