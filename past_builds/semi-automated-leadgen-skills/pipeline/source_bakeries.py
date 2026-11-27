"""source_bakeries.py — STAGE 1. Source 25 bakery listings from Google Maps.

GAME LEVEL: apify_trawler (the harbour). Fire at a dock post, the boat steams
out, works its patch, comes back low, and dumps the haul on the quay.

This is the exact script shown in the Apify Trawler ``Real Run``.  It is kept
inside the repository so the cinematic, the downloadable teaching repository,
and the live command all point at one implementation.

WHAT THIS STAGE OWES YOU: a list of raw leads — bakery listings exactly as
Google Maps described them, nothing more — written to a CSV a human can open
and to a handoff file in the shape the game's localStorage["claude3d.leads.v1"]
holds.  See pipeline/schema.py for why the missing owner columns are the point.

THE ASYNC BEAT IS PRESERVED ON PURPOSE.  run_actor() is start -> poll -> fetch,
even against the mock.  Code written against a one-shot mock breaks the day a
token is pasted in; that is the mistake the trawler level exists to prevent.

The machine starts with a clean project directory plus system Python, Git, a
browser, and Claude Code.  There are no runtime packages to install: the Apify
transport is the stdlib client in ``pipeline.providers.apify``.

Live command::

    APIFY_TOKEN=apify_api_... python3 -m pipeline.source_bakeries \
      --query bakeries --location "Chicago, Illinois" --limit 25 \
      --out out/raw_bakery_leads.csv --handoff out/leads.json

Without ``APIFY_TOKEN`` the command uses the bundled rehearsal fixture and
spends nothing.  That free path exists for tests and for a first run; the Real
Run capture is made from the same command with a token present.
"""

from __future__ import annotations

import argparse
import os
import csv
import json
import logging
from pathlib import Path
from typing import Any, Iterable

from .providers.apify import get_apify_provider
from .providers.base import load_local_env
from .schema import (LEADS_KEY, RAW_LEADS_SCHEMA, check_python_version_or_exit,
                     dedupe_listings, make_lead, stable_lead_id, utc_now)

log = logging.getLogger("pipeline.source_bakeries")

DEFAULT_ACTOR = "compass/crawler-google-places"
DEFAULT_QUERY = "bakeries"
DEFAULT_LOCATION = "Chicago, Illinois"
DEFAULT_FIXTURE = "fixtures/apify_bakeries_25.json"

CSV_FIELDS = (
    "id", "name", "category", "address", "city", "state", "postal_code",
    "phone", "website", "google_maps_url", "rating", "review_count",
    "source_provider", "source_actor", "collected_at",
)


def actor_input(*, query: str, location: str, limit: int) -> dict[str, Any]:
    """Build the Google Maps actor input shown in the Real Run."""
    return {
        "searchStringsArray": [query],
        "locationQuery": location,
        "maxCrawledPlacesPerSearch": limit,
        "language": "en",
        "skipClosedPlaces": True,
    }


def lead_id_for_listing(listing: dict[str, Any], row_index: int) -> str:
    """One stable id per Google Maps place, so a repeated place collapses.

    A listing with no place id at all still needs an id, so it falls back to
    name + address + row position. That fallback never merges two rows — which
    is the safe direction: a missed duplicate costs one enrichment lookup, a
    wrong merge silently loses a bakery.
    """
    place_id = str(listing.get("placeId") or listing.get("cid") or "").strip()
    if not place_id:
        place_id = "|".join((
            str(listing.get("title") or listing.get("name") or ""),
            str(listing.get("address") or ""),
            str(row_index),
        ))
    return stable_lead_id(place_id)


def normalise_places(items: Iterable[dict[str, Any]], *, actor: str,
                     provider_name: str) -> list[dict[str, Any]]:
    """Keep Google Maps data raw: a business listing, not a contact person.

    This is the ONE place that knows the actor's field names (title,
    categoryName, reviewsCount ...). Everything downstream sees the schema,
    never the vendor — which is what makes swapping actors a one-function change.

    The enrichment stage will find the owner and a way to reach them. Sourcing
    must not pretend that a bakery listing is already a person who can be
    emailed, and a blank phone or website stays blank: blank means "not found
    in this result", not proof the bakery has none.
    """
    collected_at = utc_now()
    records: list[dict[str, Any]] = []
    for row_index, listing in enumerate(items):
        name = str(listing.get("title") or listing.get("name") or "").strip()
        if not name:
            continue  # a row with no business name is not a lead
        rating = listing.get("totalScore") or listing.get("rating")
        records.append(make_lead(
            lead_id_for_listing(listing, row_index), name,
            category=listing.get("categoryName") or listing.get("category") or "",
            address=listing.get("address") or "",
            city=listing.get("city") or "",
            state=listing.get("state") or listing.get("stateCode") or "",
            postal_code=listing.get("postalCode") or "",
            phone=listing.get("phone") or listing.get("phoneUnformatted") or "",
            website=listing.get("website") or "",
            google_maps_url=listing.get("url") or listing.get("placeUrl") or "",
            rating=float(rating) if rating else None,
            review_count=int(listing.get("reviewsCount")
                             or listing.get("reviewCount") or 0),
            place_id=str(listing.get("placeId") or listing.get("cid") or ""),
            provider=provider_name, actor=actor, collected_at=collected_at,
        ))
    # Google Maps repeats itself: the same place twice, or one shop listed again
    # under a longer name with the same phone and address. First listing wins.
    return dedupe_listings(records)


def source_bakeries(*, query: str = DEFAULT_QUERY,
                    location: str = DEFAULT_LOCATION, limit: int = 25,
                    actor: str = DEFAULT_ACTOR,
                    fixture: list[dict[str, Any]] | None = None,
                    provider=None) -> dict[str, Any]:
    """Run one actor to completion and return the handoff payload."""
    provider = provider or get_apify_provider(fixture=fixture)
    run_input = actor_input(query=query, location=location, limit=limit)
    if provider.live:
        log.info("local system: Python is preparing one authenticated actor request")
        log.info("external system: actor=%s query=%r location=%r", actor, query, location)
    else:
        log.info("practice source: reading saved Google Maps rows; no external request")

    # EXTERNAL BOUNDARY: run_actor starts the POST, polls the remote run, and
    # fetches its dataset.  The Real Run highlights this exact line on screen.
    items = provider.run_actor(actor, run_input, limit=limit)

    records = normalise_places(items, actor=actor, provider_name=provider.name)[:limit]
    log.info("received %d raw bakery leads from %d Google Maps rows",
             len(records), len(items))
    return {
        "key": LEADS_KEY,
        "schema": RAW_LEADS_SCHEMA,
        "stage": "source",
        "at": utc_now(),
        "provider": provider.name,
        "live": provider.live,
        "query": {"text": query, "location": location, "limit": limit},
        "actor": actor,
        "counts": {"dataset_items": len(items), "leads": len(records)},
        "records": records,
    }


def write_csv(records: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for record in records:
            source = record.get("source", {})
            writer.writerow({
                **{key: record.get(key, "") for key in CSV_FIELDS},
                "source_provider": source.get("provider", ""),
                "source_actor": source.get("actor", ""),
                "collected_at": source.get("collectedAt", ""),
            })


def main(argv: list[str] | None = None) -> int:
    check_python_version_or_exit()
    parser = argparse.ArgumentParser(
        description="Stage 1 — source bakery listings through Apify Google Maps.")
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--location", default=DEFAULT_LOCATION)
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--actor", default=DEFAULT_ACTOR)
    parser.add_argument("--out", default="out/raw_bakery_leads.csv")
    parser.add_argument("--handoff", default="out/leads.json")
    parser.add_argument("--fixture", default=DEFAULT_FIXTURE,
                        help="free rehearsal dataset used when APIFY_TOKEN is unset")
    parser.add_argument("--offline", action="store_true",
                        help="use bundled practice data even if service keys are set")
    args = parser.parse_args(argv)
    if args.offline:
        os.environ["PIPELINE_OFFLINE"] = "1"
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    load_local_env()

    fixture = None
    fixture_path = Path(args.fixture)
    if not fixture_path.exists() and not fixture_path.is_absolute():
        # Run from some other folder? Look next to the repository's own files.
        fixture_path = Path(__file__).resolve().parent.parent / args.fixture
    if fixture_path.exists():
        fixture = json.loads(fixture_path.read_text())

    payload = source_bakeries(
        query=args.query, location=args.location, limit=args.limit,
        actor=args.actor, fixture=fixture,
    )
    csv_path = Path(args.out)
    handoff_path = Path(args.handoff)
    write_csv(payload["records"], csv_path)
    handoff_path.parent.mkdir(parents=True, exist_ok=True)
    handoff_path.write_text(json.dumps(payload, indent=2) + "\n")
    mode = "LIVE APIFY" if payload["live"] else "REHEARSAL FIXTURE"
    print(f"✅ {len(payload['records'])} raw bakery leads → {csv_path}")
    print(f"✅ pipeline handoff → {handoff_path} · {mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
