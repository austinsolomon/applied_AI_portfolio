"""Record one live Exa response as a redacted test fixture. Paid: one Exa request per mode.

Usage, from the project folder (seed URL, optional search query):
    uv run python scripts/record_exa_fixture.py https://www.example-massage.com
    uv run python scripts/record_exa_fixture.py https://www.example-massage.com \
        --query "mobile massage business in Phoenix, AZ"

Writes tests/fixtures/exa/find_similar.json (and search.json with --query). Each result keeps
only its URL (query string and fragment removed), title and domain, plus the request's reported
cost_dollars total. Text, highlights, authors, dates and any contact details are dropped.
"""

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from leadgen.contacts import normalize_domain
from leadgen.settings import get_settings
from leadgen.sourcing_exa import SEARCH_RESULTS_PER_QUERY, exa_client

FIXTURES = Path("tests/fixtures/exa")


def redact(response, request):
    cost = getattr(response, "cost_dollars", None)
    total = cost.get("total") if isinstance(cost, dict) else getattr(cost, "total", None)
    results = []
    for result in response.results:
        url = urlunsplit(urlsplit(result.url)._replace(query="", fragment=""))
        results.append({"url": url, "title": result.title, "domain": normalize_domain(url)})
    return {"request": request, "cost_dollars": total, "results": results}


def write(name, fixture):
    path = FIXTURES / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf-8")
    print(f"recorded {len(fixture['results'])} results, ${fixture['cost_dollars']}: {path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("seed", help="a qualified lead's own website")
    parser.add_argument("--query", help="also record one neural search")
    arguments = parser.parse_args()
    client = exa_client(get_settings())
    response = client.find_similar(
        arguments.seed, num_results=10, exclude_source_domain=True, contents=False
    )
    write("find_similar", redact(response, {"seed": arguments.seed}))
    if arguments.query:
        response = client.search(
            arguments.query, type="neural", num_results=SEARCH_RESULTS_PER_QUERY, contents=False
        )
        write("search", redact(response, {"query": arguments.query}))


if __name__ == "__main__":
    main()
