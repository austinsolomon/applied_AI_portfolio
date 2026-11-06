"""Exa sourcing: plain-language company searches per planned city.

Each phrasing in `category.exa_queries` (for example "independent CFP® financial planners
offering retirement and comprehensive planning in Phoenix, AZ") runs as one Exa search with
`type="auto"` and `category="company"`, as Exa's search guide recommends. Directories and shared
platforms are excluded in the request itself and again locally. A business found by several
phrasings gets a higher `query_consensus`, so paid stages work it first. Each result's page text
comes back with the search and goes straight into the page cache, so qualification can usually
read the homepage without another request.
"""

from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import urljoin

from exa_py import Exa

from leadgen.audit import EXA_SEARCH, audit
from leadgen.budget import paid_operation, record_cost, trace
from leadgen.category_config import is_on_host
from leadgen.contacts import is_public_domain, normalize_domain
from leadgen.raw_leads import RawLead, persist_leads
from leadgen.sourcing_maps import SourcingError
from leadgen.web_research import FetchedPage, normalize_page_url, write_cached_page

# Exa's auto search costs 0.007 USD for up to 10 results and 0.001 USD per result after that, so
# 25 results is about 0.022 USD. The reserved ceiling covers that with margin; when Exa omits
# cost_dollars, the ceiling is recorded as an estimate.
EXA_CEILING_PER_CALL = Decimal("0.03")
RESULTS_PER_QUERY = 25
MAX_QUERIES = 10
PAGE_TEXT_CHARACTERS = 6000


def category_signal(text, category):
    """True when the text names the category, by one of its configured signal terms."""
    lowered = text.lower()
    return any(term.lower() in lowered for term in category.signal_terms)


def exa_queries(category, cities):
    """Every phrasing for every planned city, in plan order, without repeats."""
    queries = [template.format(city=city) for city in cities for template in category.exa_queries]
    return list(dict.fromkeys(queries))[:MAX_QUERIES]


def company_search(client, query, category, *, num_results=RESULTS_PER_QUERY):
    return client.search(
        query,
        type="auto",
        category="company",
        num_results=num_results,
        exclude_domains=sorted(category.hosts_never_identifying_a_business),
        contents={"text": {"maxCharacters": PAGE_TEXT_CHARACTERS}},
    )


def homepage_cache_key(url):
    """The URL qualification will ask for when it reads this lead's homepage."""
    return normalize_page_url(urljoin(url.rstrip("/") + "/", ""))


def _record_exa_cost(connection, run_id, response, ceiling):
    reported = getattr(response, "cost_dollars", None)
    total = getattr(reported, "total", None) if reported else None
    if isinstance(reported, dict):
        total = reported.get("total")
    # Keep a conservative ceiling when the provider omits its charge.
    if total is None:
        cost, basis = ceiling, "estimated"
    else:
        cost, basis = Decimal(str(total)), "provider_reported"
    record_cost(connection, run_id, "exa", cost, endpoint="search", units=1, cost_basis=basis)
    return cost


def _collect(connection, response, candidates, category):
    """Add one response's own-domain, on-category results and cache their page text. A domain
    already found by an earlier phrasing raises its consensus instead."""
    seen = set()
    for result in response.results:
        domain = normalize_domain(result.url)
        title = result.title or domain or ""
        if (
            not is_public_domain(domain)
            or domain in seen
            or is_on_host(domain, category.hosts_never_identifying_a_business)
            or not category_signal(title + " " + domain, category)
        ):
            continue
        seen.add(domain)
        if domain in candidates:
            candidates[domain].query_consensus += 1
            continue
        candidates[domain] = RawLead(domain, title, website=result.url)
        text = (getattr(result, "text", None) or "").strip()
        if text:
            write_cached_page(
                connection,
                FetchedPage(
                    url=homepage_cache_key(result.url),
                    final_url=result.url,
                    title=result.title or None,
                    text=text,
                    status_code=200,
                    backend="exa",
                    fetched_at=datetime.now(UTC),
                ),
            )


def source_exa(
    connection,
    run_id,
    market_id,
    category,
    client,
    *,
    cities,
    num_results=RESULTS_PER_QUERY,
    ceiling_per_query=EXA_CEILING_PER_CALL,
):
    """The planned searches: every phrasing in the category config, for every planned city."""
    run_exa_queries(
        connection,
        run_id,
        market_id,
        category,
        client,
        exa_queries(category, cities),
        num_results=num_results,
        ceiling_per_query=ceiling_per_query,
    )
    return run_exa_leads(connection, run_id, market_id)


def run_exa_leads(connection, run_id, market_id):
    """Every Exa lead this run has found in the market so far, with its query consensus."""
    rows = connection.execute(
        "SELECT source_key,business_name,website,query_consensus FROM raw_leads "
        "WHERE run_id=%s AND market_id=%s AND source='exa' ORDER BY raw_lead_id",
        (run_id, market_id),
    ).fetchall()
    return [
        RawLead(source_key, name, website=website, query_consensus=consensus)
        for source_key, name, website, consensus in rows
    ]


def run_exa_queries(
    connection,
    run_id,
    market_id,
    category,
    client,
    queries,
    *,
    num_results=RESULTS_PER_QUERY,
    ceiling_per_query=EXA_CEILING_PER_CALL,
):
    """Run company searches and add their businesses to what this run already found.

    A business an earlier query (planned or the agent's) already found gains query consensus
    instead of a second row. Each query's outcome is traced as `exa_query`, so the sourcing agent
    can read how every search did. Returns those outcomes."""
    candidates = {lead.source_key: lead for lead in run_exa_leads(connection, run_id, market_id)}
    outcomes = []
    for query in queries:
        with paid_operation(connection, run_id, f"exa:{market_id}:{query}", ceiling_per_query):
            try:
                response = company_search(client, query, category, num_results=num_results)
            except Exception as error:
                raise SourcingError(
                    "Exa search failed. Check EXA_API_KEY and account credits."
                ) from error
            before = {domain: lead.query_consensus for domain, lead in candidates.items()}
            cost = _record_exa_cost(connection, run_id, response, ceiling_per_query)
            _collect(connection, response, candidates, category)
            new = [domain for domain in candidates if domain not in before]
            repeat = [
                domain for domain in before if candidates[domain].query_consensus > before[domain]
            ]
            filtered = [
                result.url
                for result in response.results
                if normalize_domain(result.url) not in candidates
            ]
            audit(
                connection,
                EXA_SEARCH,
                f"company search returns {len(response.results)} results: {len(new)} new "
                f"businesses, {len(repeat)} already found, "
                f"{len(response.results) - len(new) - len(repeat)} filtered out",
                query,
                run_id=run_id,
                type="auto",
                category="company",
                num_results=num_results,
                cost_usd=cost,
                new=new,
                found_again=repeat,
                filtered=filtered,
            )
            persist_leads(
                connection, run_id, market_id, "exa", [candidates[domain] for domain in new]
            )
        outcome = {
            "query": query,
            "results": len(response.results),
            "new": len(new),
            "found_again": len(repeat),
            "filtered": len(response.results) - len(new) - len(repeat),
            "new_examples": [candidates[domain].business_name for domain in new[:5]],
            "filtered_examples": filtered[:5],
        }
        trace(connection, run_id, "exa_query", "searched", outcome)
        outcomes.append(outcome)
    for lead in candidates.values():
        connection.execute(
            "UPDATE raw_leads SET query_consensus=%s WHERE run_id=%s "
            "AND source='exa' AND source_key=%s",
            (lead.query_consensus, run_id, lead.source_key),
        )
    return outcomes


def exa_client(settings):
    if not settings.exa_api_key or not settings.exa_api_key.get_secret_value().strip():
        raise SourcingError("EXA_API_KEY is not set")
    return Exa(settings.exa_api_key.get_secret_value())
