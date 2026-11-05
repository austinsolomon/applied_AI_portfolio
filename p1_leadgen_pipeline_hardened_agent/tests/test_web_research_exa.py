"""Exa as the evidence backend: a fake Exa client, the page cache and ledger against Postgres."""

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from pydantic import SecretStr

from leadgen.settings import Settings
from leadgen.web_research import (
    EXA_WEB_CEILING_PER_CALL,
    ExaWebBackend,
    FetchedPage,
    FetchResponse,
    FirecrawlBackend,
    FreeWebBackend,
    PageNeedsRendering,
    WebBackendMisconfigured,
    WebBackendUnavailable,
    WebResearcher,
    build_web_researcher,
)

PAGE_URL = "https://serenity-planning.example/services"
PAGE_TEXT = "Our CFP® professionals plan for families anywhere in Pasadena."


def exa_result(url=PAGE_URL, title="Serenity Financial Planning", text=PAGE_TEXT):
    return SimpleNamespace(url=url, title=title, text=text)


def exa_response(*results, cost=0.001, statuses=None):
    return SimpleNamespace(
        results=list(results),
        cost_dollars=None if cost is None else SimpleNamespace(total=cost),
        statuses=statuses,
    )


def ledger(connection):
    return connection.execute(
        "SELECT service,endpoint,cost_usd,cost_basis FROM cost_ledger ORDER BY call_number"
    ).fetchall()


class FakeRenderer:
    """Stands in for Firecrawl: reads any page and bills one credit."""

    name = "firecrawl"

    def __init__(self):
        self.fetched = []

    def fetch_page(self, url):
        self.fetched.append(url)
        page = FetchedPage(url, url, "Rendered", "Rendered by Firecrawl", 200, self.name, _now())
        return FetchResponse(page, None)

    def search(self, query, *, limit):
        raise AssertionError("searches never fall back to Firecrawl")


def _now():
    from datetime import UTC, datetime

    return datetime.now(UTC)


def researcher_with(connection, client, fallback=None):
    return WebResearcher(
        connection,
        ExaWebBackend(client, sleep=lambda _: None),
        run_id=uuid.uuid4(),
        fallback=fallback,
        fallback_on=PageNeedsRendering,
        fallback_searches=False,
    )


def test_exa_search_returns_page_text_as_evidence_and_ledgers_the_cost(migrated_connection):
    client = Mock()
    client.search.return_value = exa_response(exa_result(), cost=0.005)
    results = researcher_with(migrated_connection, client).search("serenity pasadena", limit=3)
    assert [(item.url, item.description) for item in results] == [(PAGE_URL, PAGE_TEXT)]
    assert client.search.call_args.kwargs == {
        "type": "auto",
        "num_results": 3,
        "contents": {"text": {"maxCharacters": 1500}},
    }
    assert ledger(migrated_connection) == [("exa", "search", Decimal("0.005"), "provider_reported")]


def test_exa_page_is_cached_so_a_second_fetch_costs_nothing(migrated_connection):
    client = Mock()
    client.get_contents.return_value = exa_response(exa_result())
    researcher = researcher_with(migrated_connection, client)
    first = researcher.fetch_page(PAGE_URL)
    second = researcher.fetch_page(PAGE_URL)
    assert (first.backend, first.text, second.from_cache) == ("exa", PAGE_TEXT, True)
    assert client.get_contents.call_count == 1
    assert len(ledger(migrated_connection)) == 1


def test_unreadable_page_falls_back_to_firecrawl_and_both_calls_are_ledgered(
    migrated_connection,
):
    client = Mock()
    client.get_contents.return_value = exa_response(
        statuses=[SimpleNamespace(id=PAGE_URL, status="error", source="crawl")]
    )
    renderer = FakeRenderer()
    page = researcher_with(migrated_connection, client, renderer).fetch_page(PAGE_URL)
    assert (page.backend, renderer.fetched) == ("firecrawl", [PAGE_URL])
    assert ledger(migrated_connection) == [
        ("exa", "contents", Decimal("0.001"), "provider_reported")
    ]


def test_exa_outage_does_not_fall_back_to_firecrawl(migrated_connection):
    client = Mock()
    client.get_contents.side_effect = ValueError("Request failed with status code 500: boom")
    client.search.side_effect = ValueError("Request failed with status code 500: boom")
    renderer = FakeRenderer()
    researcher = researcher_with(migrated_connection, client, renderer)
    with pytest.raises(WebBackendUnavailable, match="Exa contents failed"):
        researcher.fetch_page(PAGE_URL)
    with pytest.raises(WebBackendUnavailable, match="Exa search failed"):
        researcher.search("serenity", limit=3)
    assert renderer.fetched == []


def test_rate_limited_exa_call_is_retried(migrated_connection):
    client = Mock()
    client.search.side_effect = [
        ValueError("Request failed with status code 429: slow down"),
        exa_response(exa_result()),
    ]
    sleeps = []
    backend = ExaWebBackend(client, sleep=sleeps.append)
    assert len(backend.search("serenity", limit=3).results) == 1
    assert sleeps == [1]


def test_rejected_exa_key_is_reported_not_retried():
    client = Mock()
    client.search.side_effect = ValueError("Request failed with status code 401: bad key")
    with pytest.raises(WebBackendMisconfigured, match="EXA_API_KEY"):
        ExaWebBackend(client).search("serenity", limit=3)
    assert client.search.call_count == 1


def test_missing_exa_cost_is_ledgered_at_the_ceiling_as_estimated(migrated_connection):
    client = Mock()
    client.search.return_value = exa_response(exa_result(), cost=None)
    researcher_with(migrated_connection, client).search("serenity", limit=3)
    assert ledger(migrated_connection) == [("exa", "search", EXA_WEB_CEILING_PER_CALL, "estimated")]


def settings_with(**overrides):
    return Settings(
        database_url="postgresql://leadgen:unused@127.0.0.1:5433/unused",
        _env_file=None,
        **overrides,
    )


@pytest.mark.parametrize(
    "firecrawl_key,renderer", [(SecretStr("fc-test"), FirecrawlBackend), (None, FreeWebBackend)]
)
def test_builder_prefers_exa_and_keeps_firecrawl_for_rendering_only(
    migrated_connection, firecrawl_key, renderer
):
    settings = settings_with(exa_api_key=SecretStr("exa-test"), firecrawl_api_key=firecrawl_key)
    with httpx.Client() as http:
        researcher = build_web_researcher(
            migrated_connection, settings, http_client=http, exa_client=Mock()
        )
    assert isinstance(researcher.backend, ExaWebBackend)
    assert isinstance(researcher.fallback, renderer)
    assert researcher.fallback_on is PageNeedsRendering
    assert researcher.fallback_searches is False


def test_exa_site_search_uses_include_domains(migrated_connection):
    client = Mock()
    client.search.return_value = exa_response(exa_result())
    researcher_with(migrated_connection, client).search(
        "services", limit=3, site="serenity-planning.example"
    )
    assert client.search.call_args.kwargs["include_domains"] == ["serenity-planning.example"]
    assert "site:" not in client.search.call_args.args[0]


def test_exa_page_uses_exas_stored_copy_instead_of_forcing_a_recrawl(migrated_connection):
    client = Mock()
    client.get_contents.return_value = exa_response(exa_result())
    researcher_with(migrated_connection, client).fetch_page(PAGE_URL)
    assert "max_age_hours" not in client.get_contents.call_args.kwargs
    assert "livecrawl" not in client.get_contents.call_args.kwargs


def test_tier1_evidence_reads_the_homepage_then_searches_the_site():
    from leadgen.qualification import gather_evidence

    web = Mock()
    web.fetch_page.return_value = SimpleNamespace(
        final_url="https://desertplanning.com/", text="Desert Planning home page"
    )
    web.search.side_effect = [
        [SimpleNamespace(url="https://desertplanning.com/areas", description="We travel to Mesa.")],
        [],
    ]
    lead = {"website": "https://desertplanning.com", "business_name": "Desert Planning"}
    config = {"pages": [""], "site_searches": 1, "searches": 1, "max_evidence_characters": 18000}

    evidence = gather_evidence(lead, "Phoenix, AZ", web, config)

    web.fetch_page.assert_called_once_with("https://desertplanning.com/")
    assert web.search.call_args_list[0].kwargs == {"limit": 3, "site": "desertplanning.com"}
    assert evidence == {
        "https://desertplanning.com/": "Desert Planning home page",
        "https://desertplanning.com/areas": "We travel to Mesa.",
    }
