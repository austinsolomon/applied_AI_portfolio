"""Own-domain lookalikes, unioned across category-qualified seeds, with a neural-search fallback
for a market that has no seeds yet."""

from decimal import Decimal

from exa_py import Exa

from leadgen.budget import paid_operation, record_cost
from leadgen.category_config import is_on_host
from leadgen.contacts import normalize_domain
from leadgen.raw_leads import RawLead, persist_leads
from leadgen.sourcing_maps import SourcingError

# Exa reports its charge per request; when it does not, the reserved ceiling is recorded instead.
EXA_CEILING_PER_CALL = Decimal("0.02")
MAX_SEEDS = 50
MAX_SEARCH_QUERIES = 10
SEARCH_RESULTS_PER_QUERY = 10


def category_signal(text, category):
    tokens = {
        word
        for keyword in category.keywords
        for word in keyword.lower().split()
        if len(word) > 3 and word not in {"home", "mobile", "couples", "sports"}
    }
    return any(token in text.lower() for token in tokens)


def select_seeds(connection, market_id, category, *, limit=MAX_SEEDS):
    rows = connection.execute(
        "SELECT business_name,website FROM crm_leads WHERE market_id=%s "
        "AND qualification_reason IS NOT NULL AND website_domain IS NOT NULL "
        "AND status IN ('available','assigned','converted') ORDER BY lead_id",
        (market_id,),
    ).fetchall()
    seeds = {}
    for name, website in rows:
        domain = normalize_domain(website)
        if (
            domain
            and not is_on_host(domain, category.hosts_never_identifying_a_business)
            and category_signal(name + " " + domain, category)
            and not domain.endswith(".example")
        ):
            seeds.setdefault(domain, website)
    return list(seeds.values())[: min(MAX_SEEDS, limit)]


def _record_exa_cost(connection, run_id, response, ceiling, endpoint):
    reported = getattr(response, "cost_dollars", None)
    total = getattr(reported, "total", None) if reported else None
    if isinstance(reported, dict):
        total = reported.get("total")
    # Keep a conservative ceiling when the provider omits its charge.
    if total is None:
        cost, basis = ceiling, "estimated"
    else:
        cost, basis = Decimal(str(total)), "provider_reported"
    record_cost(connection, run_id, "exa", cost, endpoint=endpoint, units=1, cost_basis=basis)


def _collect(response, candidates, category, *, skip_domain=None):
    """Add one response's own-domain, on-category results; repeat sightings raise consensus."""
    seen = set()
    for result in response.results:
        domain = normalize_domain(result.url)
        title = result.title or domain or ""
        if (
            not domain
            or domain == skip_domain
            or domain in seen
            or is_on_host(domain, category.hosts_never_identifying_a_business)
            or not category_signal(title + " " + domain, category)
        ):
            continue
        seen.add(domain)
        if domain in candidates:
            candidates[domain].seed_consensus += 1
        else:
            candidates[domain] = RawLead(domain, title, website=result.url)


def _save_consensus(connection, run_id, candidates):
    for lead in candidates.values():
        connection.execute(
            "UPDATE raw_leads SET seed_consensus=%s WHERE run_id=%s "
            "AND source='exa' AND source_key=%s",
            (lead.seed_consensus, run_id, lead.source_key),
        )


def _exa_call(call):
    try:
        return call()
    except Exception as error:
        raise SourcingError("Exa lookup failed. Check EXA_API_KEY and account credits.") from error


def source_exa(
    connection,
    run_id,
    market_id,
    category,
    client,
    *,
    seeds=None,
    ceiling_per_seed=EXA_CEILING_PER_CALL,
):
    seeds = select_seeds(connection, market_id, category) if seeds is None else seeds[:MAX_SEEDS]
    candidates = {}
    for seed in seeds:
        seed_domain = normalize_domain(seed)
        with paid_operation(connection, run_id, f"exa:{market_id}:{seed_domain}", ceiling_per_seed):
            response = _exa_call(
                lambda seed=seed: client.find_similar(
                    seed, num_results=10, exclude_source_domain=True, contents=False
                )
            )
            _record_exa_cost(connection, run_id, response, ceiling_per_seed, "findSimilar")
            _collect(response, candidates, category, skip_domain=seed_domain)
            persist_leads(connection, run_id, market_id, "exa", candidates.values())
    _save_consensus(connection, run_id, candidates)
    return list(candidates.values())


def search_queries(category, cities, keywords=None):
    """One plain-language query per planned keyword and city, e.g. 'mobile massage business in
    Phoenix, AZ'. Keywords default to the category's first, matching a one-keyword plan."""
    keywords = list(keywords or category.keywords[:1])
    queries = [f"{keyword} business in {city}" for city in cities for keyword in keywords]
    return list(dict.fromkeys(queries))[:MAX_SEARCH_QUERIES]


def source_exa_search(
    connection,
    run_id,
    market_id,
    category,
    client,
    *,
    cities,
    keywords=None,
    num_results=SEARCH_RESULTS_PER_QUERY,
    ceiling_per_query=EXA_CEILING_PER_CALL,
):
    """Fallback for a market with no qualified seeds: neural search by category and city.

    Results go through the same host and category filters as lookalikes. Here seed_consensus
    counts how many queries surfaced a domain.
    """
    candidates = {}
    for query in search_queries(category, cities, keywords):
        with paid_operation(
            connection, run_id, f"exa:{market_id}:search:{query}", ceiling_per_query
        ):
            response = _exa_call(
                lambda query=query: client.search(
                    query, type="neural", num_results=num_results, contents=False
                )
            )
            _record_exa_cost(connection, run_id, response, ceiling_per_query, "search")
            _collect(response, candidates, category)
            persist_leads(connection, run_id, market_id, "exa", candidates.values())
    _save_consensus(connection, run_id, candidates)
    return list(candidates.values())


def exa_client(settings):
    if not settings.exa_api_key or not settings.exa_api_key.get_secret_value().strip():
        raise SourcingError("EXA_API_KEY is not set")
    return Exa(settings.exa_api_key.get_secret_value())
