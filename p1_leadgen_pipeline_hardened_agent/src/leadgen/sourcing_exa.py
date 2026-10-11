"""Own-domain lookalikes, unioned across category-qualified seeds."""

from decimal import Decimal

from exa_py import Exa

from leadgen.budget import paid_operation, record_cost
from leadgen.category_config import is_on_host
from leadgen.contacts import normalize_domain
from leadgen.raw_leads import RawLead, persist_leads
from leadgen.sourcing_maps import SourcingError


def category_signal(text, category):
    tokens = {
        word
        for keyword in category.keywords
        for word in keyword.lower().split()
        if len(word) > 3 and word not in {"home", "mobile", "couples", "sports"}
    }
    return any(token in text.lower() for token in tokens)


def select_seeds(connection, market_id, category, *, limit=50):
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
    return list(seeds.values())[: min(50, limit)]


def source_exa(
    connection, run_id, market_id, category, client, *, seeds=None, ceiling_per_seed=Decimal("0.02")
):
    seeds = select_seeds(connection, market_id, category) if seeds is None else seeds[:50]
    candidates = {}
    for seed in seeds:
        seed_domain = normalize_domain(seed)
        with paid_operation(connection, run_id, f"exa:{market_id}:{seed_domain}", ceiling_per_seed):
            try:
                response = client.find_similar(
                    seed, num_results=10, exclude_source_domain=True, contents=False
                )
            except Exception as error:
                raise SourcingError(
                    "Exa lookup failed. Check EXA_API_KEY and account credits."
                ) from error
            reported = getattr(response, "cost_dollars", None)
            total = getattr(reported, "total", None) if reported else None
            if isinstance(reported, dict):
                total = reported.get("total")
            # Keep a conservative ceiling when the provider omits its charge.
            cost = Decimal(str(total)) if total is not None else ceiling_per_seed
            record_cost(connection, run_id, "exa", cost, endpoint="findSimilar", units=1)
            seen = set()
            for result in response.results:
                domain = normalize_domain(result.url)
                title = result.title or domain or ""
                if (
                    not domain
                    or domain == seed_domain
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
            persist_leads(connection, run_id, market_id, "exa", candidates.values())
    for lead in candidates.values():
        connection.execute(
            "UPDATE raw_leads SET seed_consensus=%s WHERE run_id=%s "
            "AND source='exa' AND source_key=%s",
            (lead.seed_consensus, run_id, lead.source_key),
        )
    return list(candidates.values())


def exa_client(settings):
    if not settings.exa_api_key or not settings.exa_api_key.get_secret_value().strip():
        raise SourcingError("EXA_API_KEY is not set")
    return Exa(settings.exa_api_key.get_secret_value())
