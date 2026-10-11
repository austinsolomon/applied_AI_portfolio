"""Bounded Google Maps runs; persist partial datasets even when the actor is aborted."""

import math
import time
from decimal import Decimal

from apify_client import ApifyClient

from leadgen.budget import paid_operation, record_cost
from leadgen.eval_review import record_eval
from leadgen.plans import attach_scraper_run
from leadgen.raw_leads import RawLead, persist_leads

ACTOR = "compass/crawler-google-places"
TERMINAL = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}


class SourcingError(Exception):
    """A source failed; messages never contain provider credentials."""


def maps_client(settings):
    if not settings.apify_api_key or not settings.apify_api_key.get_secret_value().strip():
        raise SourcingError("APIFY_API_KEY is not set")
    return ApifyClient(settings.apify_api_key.get_secret_value(), max_retries=0)


def map_item(item):
    key = item.get("placeId") or item.get("cid") or item.get("url")
    name = item.get("title")
    if not key or not name:
        return None
    location = item.get("location") or {}
    return RawLead(
        source_key=str(key),
        business_name=name,
        google_place_id=item.get("placeId"),
        phone=item.get("phone"),
        website=item.get("website"),
        latitude=location.get("lat"),
        longitude=location.get("lng"),
        categories=item.get("categories") or [item.get("categoryName") or ""],
    )


def apify_cost(actor_run):
    """Event counts can settle before usageTotalUsd after an abort."""
    reported = Decimal(str(actor_run.get("usageTotalUsd") or 0))
    pricing = actor_run.get("pricingInfo", {})
    if pricing.get("pricingModel") != "PAY_PER_EVENT":
        return reported
    events = pricing.get("pricingPerEvent", {}).get("actorChargeEvents", {})
    counted = sum(
        (
            Decimal(str(events.get(name, {}).get("eventPriceUsd", 0))) * count
            for name, count in actor_run.get("chargedEventCounts", {}).items()
        ),
        Decimal(0),
    )
    return max(reported, counted)


def source_maps(
    connection,
    run_id,
    cell,
    client,
    *,
    limit=50,
    ceiling=Decimal("1"),
    poll_seconds=5,
    max_polls=60,
    sleep=time.sleep,
    stage="sample",
):
    if limit < 1 or max_polls < 1 or ceiling <= 0:
        raise ValueError("Sourcing limits must be positive")
    with paid_operation(
        connection, run_id, f"maps:{cell.cell_id}:{stage}", ceiling, cell_id=cell.cell_id
    ):
        previous_charge = Decimal(0)
        if stage == "full" and cell.apify_run_id:
            previous_charge = connection.execute(
                "SELECT coalesce(sum(cost_usd),0) FROM cost_ledger WHERE service='apify' "
                "AND request_id=%s",
                (cell.apify_run_id,),
            ).fetchone()[0]
        actor_run = None
        try:
            if stage == "full" and cell.apify_run_id:
                actor_run = client.run(cell.apify_run_id).resurrect(
                    max_total_charge_usd=ceiling + previous_charge,
                    max_items=cell.expected_raw_leads,
                    timeout_secs=300,
                )
            else:
                actor_run = client.actor(ACTOR).start(
                    run_input={
                        "searchStringsArray": [cell.keyword],
                        "locationQuery": cell.city,
                        "maxCrawledPlacesPerSearch": cell.expected_raw_leads,
                        "language": "en",
                    },
                    max_total_charge_usd=ceiling,
                    max_items=cell.expected_raw_leads,
                    timeout_secs=300,
                )
            attach_scraper_run(connection, cell.cell_id, actor_run["id"])
            resource = client.run(actor_run["id"])
            for _ in range(max_polls):
                actor_run = resource.get() or actor_run
                page = client.dataset(actor_run["defaultDatasetId"]).list_items(limit=limit)
                leads = [lead for item in page.items if (lead := map_item(item))]
                persist_leads(
                    connection, run_id, cell.market_id, "google_maps", leads, cell_id=cell.cell_id
                )
                unique = len({lead.source_key for lead in leads})
                if actor_run["status"] in TERMINAL:
                    break
                spend = apify_cost(actor_run)
                if unique >= limit or spend >= ceiling + previous_charge:
                    actor_run = resource.abort()
                    break
                sleep(poll_seconds)
        except Exception as error:
            from leadgen.budget import trace

            trace(
                connection,
                run_id,
                f"maps:{cell.cell_id}:{stage}",
                "provider_error",
                {
                    "error_type": type(error).__name__,
                    "http_status": getattr(error, "status_code", None),
                },
            )
            raise SourcingError(
                "Google Maps sourcing failed. Check Apify credentials, balance and actor status."
            ) from error
        finally:
            if actor_run:
                resource = client.run(actor_run["id"])
                if actor_run["status"] not in TERMINAL:
                    actor_run = resource.abort()
                actor_run = resource.get() or actor_run
                # Read the complete final dataset: an abort can leave more rows than the sample cap.
                leads = [
                    lead
                    for item in client.dataset(actor_run["defaultDatasetId"]).iterate_items()
                    if (lead := map_item(item))
                ]
                persist_leads(
                    connection, run_id, cell.market_id, "google_maps", leads, cell_id=cell.cell_id
                )
                spend = apify_cost(actor_run)
                record_cost(
                    connection,
                    run_id,
                    "apify",
                    max(Decimal(0), spend - previous_charge),
                    endpoint=ACTOR,
                    units=len(leads),
                    request_id=actor_run["id"],
                )
        return leads


def distance_km(latitude, longitude, center_latitude, center_longitude):
    lat1, lat2 = math.radians(latitude), math.radians(center_latitude)
    delta_lat = lat2 - lat1
    delta_lon = math.radians(center_longitude - longitude)
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return 6371 * 2 * math.asin(math.sqrt(min(1, value)))


def grade_sample(connection, run_id, cell, leads, *, discount=0.15):
    market = connection.execute(
        "SELECT center_latitude,center_longitude,service_radius_km FROM markets WHERE market_id=%s",
        (cell.market_id,),
    ).fetchone()
    pure = sum(
        any(
            "massage" in category.lower() and "school" not in category.lower()
            for category in lead.categories
        )
        for lead in leads
    )
    in_market = sum(
        lead.latitude is not None
        and lead.longitude is not None
        and distance_km(lead.latitude, lead.longitude, market[0], market[1]) <= market[2]
        for lead in leads
    )
    count = len(leads)
    purity = pure / count if count else 0
    coverage = in_market / count if count else 0
    verdict = (
        "PROCEED"
        if count and purity >= 0.9 and coverage >= 0.9
        else ("RECONFIGURE" if count and purity >= 0.5 else "ABORT")
    )
    metrics = {
        "count": count,
        "category_purity": purity,
        "in_market_share": coverage,
        "discounted_purity": max(0, purity - discount),
        "cell_id": cell.cell_id,
    }
    record_eval(
        connection,
        kind="sample_verdict",
        subject=f"{cell.city} · {cell.keyword}",
        verdict=verdict,
        evidence=metrics,
        run_id=run_id,
        reason="Category purity and known-coordinate market share; unknown coordinates count out.",
    )
    connection.execute(
        "UPDATE scrape_plan_cells SET status=%s WHERE cell_id=%s", (verdict.lower(), cell.cell_id)
    )
    return verdict
