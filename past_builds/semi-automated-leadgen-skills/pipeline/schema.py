"""schema.py — THE ONE PLACE THE REPO AND THE GAME AGREE ON WHAT A LEAD IS.

WHY THIS FILE EXISTS AT ALL. The game (3_claude_3D) teaches the pipeline as five
levels, and each level hands the next one a JSON blob in `localStorage` under a
versioned key. This repo is the SAME pipeline with the boats and dogs removed. If
the two disagree about field names, then "I played the level, now I run the real
thing" stops being one continuous lesson and becomes two unrelated products. So
every shape below is transcribed from the level source that owns it, and the
source file is named in the comment so a future edit can be checked against it.

THE FOUR STORES (game key -> stage that writes it):

  claude3d.leads.v1      apify_trawler   raw leads          (also enriched in-place
                                                             by dog_enrichment)
  claude3d.enriched.v1   enrichment      enriched leads     (funnel_ledger.js reads it)
  claude3d.qualified.v1  qualification   accepted list      INCLUDING the junk
  claude3d.outreach.v1   outreach        sends + results    (repo-side; the game's
                                                             delivery_city keeps this
                                                             in window.__city.rounds)

THE WORLD THIS REPO RUNS IN. One student sells $500 websites that let Chicago
bakeries take orders online. Stage 1 asks Google Maps for 25 bakery listings, and
every later stage works on that same list. A lead is therefore a BAKERY, not a
person: the person who can say yes is one of the columns enrichment buys.

A RAW LEAD HAS NO OWNER COLUMN. A Google Maps listing gives you the bakery's
name, category, address, phone, website, map link, rating and review count for
free. It never gives you the owner, their role, an email, how many shops there
are, or how the bakery takes orders. So a raw lead cannot carry those five keys.
Only an enriched lead may have them.

WHICH STAGE FILLS WHAT:

  source    id + LISTING_FIELDS + source{}          (free, from the listing)
  enrich    CONTACT_FIELDS from the contact waterfall (Apollo, then Hunter),
            ordering_platform from Apollo or from the research agent once you
            confirm its answer, + DERIVED_FIELDS + enrichment{}
  qualify   one make_qualified_entry() row per lead: outcome flags + reasons
  outreach  one SEND_RESULT_FIELDS row per message sent, one HELD_RECORD_FIELDS
            row per message the lint stopped
"""

from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timezone
from typing import Any, Iterable

# --------------------------------------------------------- python version gate --
# The code below leans on modern syntax (PEP 604 unions, `from __future__ import
# annotations` resolved lazily) that only works on the floor pyproject.toml
# declares. Without this check, a beginner on an old interpreter (macOS still
# ships 3.9 as /usr/bin/python3 on older OS releases) gets a raw SyntaxError or
# TypeError deep inside a stage module instead of a one-sentence fix.
MINIMUM_PYTHON_VERSION = (3, 11)


def check_python_version_or_exit() -> None:
    """Print a plain-English fix and exit(1) if this interpreter is too old.

    Every stage script's `main()` calls this before it does anything else.
    """
    if sys.version_info < MINIMUM_PYTHON_VERSION:
        running = ".".join(str(part) for part in sys.version_info[:3])
        needed = ".".join(str(part) for part in MINIMUM_PYTHON_VERSION)
        print(
            f"This pipeline needs Python {needed} or newer — you're running "
            f"Python {running} ({sys.executable}).\n"
            f"Fix: install a newer Python and make a virtual environment with it, "
            f"for example:\n"
            f"  python3.11 -m venv .venv && source .venv/bin/activate\n"
            f"Then re-run this command inside that virtual environment.",
            file=sys.stderr,
        )
        raise SystemExit(1)


# ---------------------------------------------------------------- store keys --
# Kept as constants rather than string literals so a version bump (v1 -> v2) is a
# one-line change here and the handoff filenames follow automatically.
LEADS_KEY = "claude3d.leads.v1"
ENRICHED_KEY = "claude3d.enriched.v1"
QUALIFIED_KEY = "claude3d.qualified.v1"
OUTREACH_KEY = "claude3d.outreach.v1"

# What a Google Maps listing gives away for free, in CSV column order. The source
# stage copies these off the actor's dataset row and buys nothing.
LISTING_FIELDS = ("name", "category", "address", "city", "state", "postal_code",
                  "phone", "website", "google_maps_url", "rating", "review_count")

# Raw-lead keys. Anything else on a raw record is a bug in the sourcing stage.
RAW_FIELDS = ("id",) + LISTING_FIELDS + ("source",)

# The handoff payload's `schema` tag for a list of raw business listings.
RAW_LEADS_SCHEMA = "claude3d.raw-business-leads.v1"

# What the contact-data services (Apollo, then Hunter) look up for each bakery.
CONTACT_FIELDS = ("owner_name", "owner_role", "email", "location_count")

# The contact fields that describe one person: the one you write to.
PERSON_FIELDS = ("owner_name", "owner_role", "email")

# The one field the research agent may fill, and only after you confirm it.
# Apollo is asked for it too, but a contact database rarely knows it.
RESEARCHED_FIELD = "ordering_platform"

# Every column Enrich adds. The listing never carries any of these.
ENRICHED_FIELDS = CONTACT_FIELDS + (RESEARCHED_FIELD,)

# Worked out from columns already on the record — never bought, never billed.
DERIVED_FIELDS = ("has_website", "takes_online_orders", "is_chain")

# ------------------------------------------------------------ shared vocabulary --
# The only values `owner_role` may hold, lowest to highest. Index position is the
# ordering; qualification expresses its band as two names on this ladder.
OWNER_ROLE_LADDER = ("Counter Staff", "Baker", "Shift Lead", "Manager",
                     "General Manager", "Co-owner", "Owner", "Corporate Office")

# The roles of people on staff. A contact with one of these roles is not the
# owner, so Enrich keeps looking for the owner with the next provider.
STAFF_ROLES = ("Counter Staff", "Baker", "Shift Lead")

# `ordering_platform` is either NO_ORDERING_PLATFORM or one name from this list.
# Generic kinds of tool, never brand names. Any value ON the list means the bakery
# already takes online orders, so there is nothing to sell it.
NO_ORDERING_PLATFORM = "none"
ONLINE_ORDERING_PLATFORMS = ("pos_online_store", "website_builder_checkout",
                             "restaurant_ordering_app", "delivery_marketplace")

# A bakery with more shops than this is a chain, not an independent.
INDEPENDENT_MAX_LOCATIONS = 3

# The four fields Qualify's rules read. If one is still unknown after Enrich,
# Qualify sends the lead to needs_review and names the field.
RULE_FIELDS = ("review_count", "location_count", "owner_role", "ordering_platform")

# ------------------------------------------------------------- outreach shapes --
# The blanks a message template may use, and the keys of the two row shapes the
# outreach stage writes. Kept here so the copy writer, the sender and the tests
# agree on spelling.
OUTREACH_PLACEHOLDERS = ("bakery_name", "owner_first_name", "review_count",
                         "how_orders_work_today")
SEND_RESULT_FIELDS = ("lead_id", "channel", "subject", "sent", "delivered",
                      "bounced", "replied")
HELD_RECORD_FIELDS = ("lead", "message", "lint")


def utc_now() -> str:
    """ISO-8601 with a Z, matching `collectedAt` in trawler_haul.js.

    datetime.utcnow() is deprecated in 3.12+ and was always ambiguous about
    tzinfo; the explicit timezone.utc form is the one that round-trips.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def stable_lead_id(place_id: str) -> str:
    """The lead id for one Google Maps place: "apify-maps-" + 12 hex characters.

    Hashing the place id (rather than numbering rows) means the same bakery gets
    the same id on every run, so a repeated listing collapses instead of
    becoming a second lead, and a fixture can be keyed by id.
    """
    digest = hashlib.sha256(str(place_id).encode()).hexdigest()[:12]
    return f"apify-maps-{digest}"


def make_lead(
    lead_id: str,
    name: str,
    *,
    category: str = "",
    address: str = "",
    city: str = "",
    state: str = "",
    postal_code: str = "",
    phone: str = "",
    website: str = "",
    google_maps_url: str = "",
    rating: float | None = None,
    review_count: int = 0,
    place_id: str = "",
    level: str = "apify_trawler",
    provider: str = "apify(mock)",
    actor: str = "compass/crawler-google-places",
    collected_at: str | None = None,
) -> dict[str, Any]:
    """Build one RAW lead: a bakery listing, exactly as Google Maps described it.

    A blank `phone` or `website` stays a blank string. Blank means "not found in
    this result", not proof the bakery has none, so nothing here guesses.

    `source` is a nested object rather than flat `source_level`/`source_actor`
    keys because that is what the game writes, and a flattened copy would not
    survive a round trip through localStorage into the enrichment level.
    """
    return {
        "id": lead_id,
        "name": name,
        "category": category,
        "address": address,
        "city": city,
        "state": state,
        "postal_code": postal_code,
        "phone": phone,
        "website": website,
        "google_maps_url": google_maps_url,
        "rating": rating,
        "review_count": review_count,
        "source": {
            "level": level,
            "provider": provider,
            "actor": actor,
            "place_id": place_id,
            "collectedAt": collected_at or utc_now(),
        },
    }


def is_raw_lead(rec: Any) -> bool:
    """True when `rec` is a well-formed raw lead.

    Used by the tests and by enrich.py's input guard. A record that already
    has `owner_name` is not raw, and enriching it again would bill it twice.
    """
    if not isinstance(rec, dict):
        return False
    if not all(k in rec for k in ("id", "name", "review_count")):
        return False
    return not any(k in rec for k in ENRICHED_FIELDS)


def derive_columns(lead: dict[str, Any]) -> dict[str, Any]:
    """The three answers you can work out from columns you already have.

    `None` means "cannot tell yet" — the column it depends on is still blank.
    An unknown is never rounded to False: "we do not know how this bakery takes
    orders" and "this bakery does not take online orders" are different facts.
    """
    platform = lead.get("ordering_platform")
    location_count = lead.get("location_count")
    if platform in ONLINE_ORDERING_PLATFORMS:
        takes_online_orders = True
    elif platform == NO_ORDERING_PLATFORM:
        takes_online_orders = False
    else:
        takes_online_orders = None
    return {
        "has_website": bool(lead.get("website")),
        "takes_online_orders": takes_online_orders,
        "is_chain": (location_count > INDEPENDENT_MAX_LOCATIONS
                     if isinstance(location_count, int) else None),
    }


def make_enriched(lead: dict[str, Any], columns: dict[str, Any],
                  ledger: list[dict[str, Any]],
                  research: dict[str, Any] | None = None) -> dict[str, Any]:
    """Raw lead + the columns Enrich found + the receipt + the research note.

    The receipt (`enrichment.ledger`) is part of the record, so you can see
    what each lead cost without a join. `enrichment.research` says where
    `ordering_platform` came from: Apollo, or the research agent's answer and
    the page it read, and who confirmed it.
    """
    out = dict(lead)
    out.update({k: v for k, v in columns.items() if k in ENRICHED_FIELDS})
    out.update(derive_columns(out))
    # `location_count` 0 is a real answer (a home baker with no shop), so the
    # test for "filled" is "not blank", never plain truthiness.
    filled = [f for f in ENRICHED_FIELDS if out.get(f) not in (None, "")]
    out["enrichment"] = {
        "ledger": ledger,
        "cost": round(sum(e["cost"] for e in ledger), 6),
        "filled": filled,
        # `complete` is the flag funnel_ledger.js counts for the "(COMPLETE n)"
        # readout: r?.complete. A record missing any column is enriched but not
        # complete, and the gap between the two numbers is the lesson.
        "complete": len(filled) == len(ENRICHED_FIELDS),
        "research": research,
        "at": utc_now(),
    }
    return out


def make_qualified_entry(lead: dict[str, Any], *, qualified: bool, junk: bool,
                         reasons: list[str], value: float) -> dict[str, Any]:
    """One row of `claude3d.qualified.v1`, matching delivery_handoff.js.

    The same row the game writes, with the bakery's nouns: id / name /
    owner_name / owner_role / review_count / location_count / ordering_platform /
    qualified / junk / value / contact / at. We keep `lead` alongside so the
    outreach stage does not have to re-join against the enriched file, and add
    `reasons` because a repo user needs to debug their ICP, whereas the player
    just watches the ball miss the zone.
    """
    return {
        "id": lead["id"],
        "name": lead.get("name"),
        "owner_name": lead.get("owner_name"),
        "owner_role": lead.get("owner_role"),
        "review_count": lead.get("review_count"),
        "location_count": lead.get("location_count"),
        "ordering_platform": lead.get("ordering_platform"),
        "qualified": qualified,
        "junk": junk,
        "value": value,
        "contact": lead.get("email") or "none",
        "reasons": reasons,
        "at": utc_now(),
        "lead": lead,
    }


def outreach_placeholder_values(lead: dict[str, Any],
                                how_orders_work_today: str) -> dict[str, str]:
    """Fill the OUTREACH_PLACEHOLDERS for one lead, ready for `str.format`.

    `how_orders_work_today` is passed in rather than read off the lead because
    it is an observation the copy writer makes, not a column anyone bought.
    """
    owner_name = str(lead.get("owner_name") or "").strip()
    # A head office is a desk, not a person: never greet it as "Hi Franchise".
    is_named_person = bool(owner_name) and lead.get("owner_role") != "Corporate Office"
    return {
        "bakery_name": str(lead.get("name") or ""),
        "owner_first_name": owner_name.split()[0] if is_named_person else "there",
        "review_count": f"{int(lead.get('review_count') or 0):,}",
        "how_orders_work_today": how_orders_work_today,
    }


def dedupe_by_id(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge-by-id, last write wins — the same rule the game uses.

    hunt_mechanic.js merges its enriched people into the trawler's store
    "merged and deduped by id". Replaying a stage must therefore be idempotent
    rather than additive, or a second run doubles the funnel numbers.
    """
    out: dict[str, dict[str, Any]] = {}
    for r in records:
        out[r["id"]] = r
    return list(out.values())


def dedupe_listings(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop repeated bakeries from one sourcing run. The FIRST listing wins.

    Two ways the same shop shows up twice in a Google Maps result:
      1. the identical place returned again (same place id, so the same lead id);
      2. a second listing under a slightly different name — a new place id, but
         the same phone number AND the same street address.
    A blank phone or address never matches anything: two bakeries that both
    left the phone field empty are not the same bakery.
    """
    kept: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_phone_and_address: set[tuple[str, str]] = set()
    for record in records:
        phone = str(record.get("phone") or "").strip()
        address = str(record.get("address") or "").strip().casefold()
        phone_and_address = (phone, address)
        if record["id"] in seen_ids:
            continue
        if phone and address and phone_and_address in seen_phone_and_address:
            continue
        seen_ids.add(record["id"])
        if phone and address:
            seen_phone_and_address.add(phone_and_address)
        kept.append(record)
    return kept
