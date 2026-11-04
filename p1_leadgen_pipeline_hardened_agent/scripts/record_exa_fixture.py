"""Record one live Exa company search as a redacted test fixture. Paid: one Exa request.

Usage, from the project folder (the city fills the first query phrasing in category.yaml):
    uv run python scripts/record_exa_fixture.py "Phoenix, AZ"

Writes tests/fixtures/exa/company_search.json. Each result keeps only its URL (query string and
fragment removed), title and domain, plus the request's reported cost_dollars total. Page text,
highlights, authors, dates and any contact details are dropped.
"""

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from leadgen.category_config import load_category_config
from leadgen.contacts import normalize_domain
from leadgen.settings import get_settings
from leadgen.sourcing_exa import company_search, exa_client, exa_queries

FIXTURES = Path("tests/fixtures/exa")


def redact(response, request):
    cost = getattr(response, "cost_dollars", None)
    total = cost.get("total") if isinstance(cost, dict) else getattr(cost, "total", None)
    results = []
    for result in response.results:
        url = urlunsplit(urlsplit(result.url)._replace(query="", fragment=""))
        results.append({"url": url, "title": result.title, "domain": normalize_domain(url)})
    return {"request": request, "cost_dollars": total, "results": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("city", help='a planned city, e.g. "Phoenix, AZ"')
    arguments = parser.parse_args()
    category = load_category_config()
    query = exa_queries(category, [arguments.city])[0]
    response = company_search(exa_client(get_settings()), query, category)
    fixture = redact(response, {"query": query, "type": "auto", "category": "company"})
    path = FIXTURES / "company_search.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf-8")
    print(f"recorded {len(fixture['results'])} results, ${fixture['cost_dollars']}: {path}")


if __name__ == "__main__":
    main()
