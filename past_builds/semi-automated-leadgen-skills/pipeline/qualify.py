"""qualify.py — STAGE 3. Four rules on the enriched record. No outside calls.

Qualify decides which enriched bakeries go to Outreach. It reads the record
Enrich wrote and applies four rules. It calls no service and no agent.

THE OFFER THE RULES PROTECT. One student sells $500 websites that let Chicago
bakeries take orders online. So a bakery fits when it is a going concern
(reviews), independent (locations), reachable through someone who can say yes
(owner role), and NOT already taking online orders (ordering platform).

    review_count        20 to 1,000
    location_count      1 to 3
    owner_role          Manager up to Owner
    ordering_platform   must be "none"

THE HANDOFF. Only qualified records enter Outreach. Rejected and review-needed
records remain in separate buckets, with reasons. Optional batting never changes
the saved decision. Older report fields remain for file compatibility.

WHY BANDS AND NOT A SCORE. A 0-100 fit score feels sophisticated and is
unfalsifiable: nobody can say which lead it excluded or why. Bands and a short
list are auditable — every rejection names the rule it broke, in `reasons`.

UNKNOWN FIELDS GO TO needs_review. If Enrich left a rule field empty (Apollo
had no answer, or you have not confirmed the research agent's answer), the lead
goes to needs_review with the field named in `reasons`.

Run:  python3 -m pipeline.qualify --in out/enriched.json --out out/qualified.json
"""

from __future__ import annotations

import argparse
import os
import json
import logging
from pathlib import Path
from typing import Any

from .schema import (NO_ORDERING_PLATFORM, ONLINE_ORDERING_PLATFORMS,
                     OWNER_ROLE_LADDER, QUALIFIED_KEY, RESEARCHED_FIELD,
                     check_python_version_or_exit, make_qualified_entry, utc_now)

log = logging.getLogger("pipeline.qualify")

# The rules with no near miss. A bakery that already takes online orders has
# nothing to buy, and a bakery whose contact cannot say yes is the wrong person
# to write to. Breaking either one is TAKEN, never chased. The number bands
# (reviews, locations) are the only rules a lead can miss narrowly.
HARD_RULES = ("ordering_platform", "owner_role")


class ICP:
    """One ideal-customer profile: three bands and one short list. That is all.

    Defaults are the course's own rule — 20..1000 reviews, 1..3 locations, a
    contact between Manager and Owner on the role ladder, and no online
    ordering platform today. The list works by membership: a bakery whose
    platform is ON the list already takes online orders, so it is out.
    """

    def __init__(self, *, review_min: int = 20, review_max: int = 1000,
                 location_min: int = 1, location_max: int = 3,
                 role_min: str = "Manager", role_max: str = "Owner",
                 online_ordering_platforms: tuple[str, ...] = ONLINE_ORDERING_PLATFORMS):
        self.review_min = review_min
        self.review_max = review_max
        self.location_min = location_min
        self.location_max = location_max
        self.online_ordering_platforms = tuple(
            platform.lower() for platform in online_ordering_platforms)
        try:
            self.role_min_rank = OWNER_ROLE_LADDER.index(role_min)
            self.role_max_rank = OWNER_ROLE_LADDER.index(role_max)
        except ValueError as error:
            raise ValueError(
                f"role band must use ladder names {list(OWNER_ROLE_LADDER)}"
            ) from error
        if self.role_min_rank > self.role_max_rank:
            raise ValueError("role_min sits above role_max on the ladder")
        if review_min > review_max or location_min > location_max:
            raise ValueError("a band's minimum sits above its maximum")

    def check(self, lead: dict[str, Any]) -> list[str]:
        """Return the list of rules this lead breaks. Empty list = in zone.

        Reasons, not a boolean, because the only way to tune an ICP is to see
        WHICH edge your rejects are falling off. A stage that returns True/False
        gives a user no way to discover their band is one notch too narrow.
        """
        reasons: list[str] = []
        # Imported game records can carry a proposed value alongside an explicit
        # unverified marker. A present value is not yet a checked fact.
        unchecked = (lead.get("enrichment") or {}).get("unverified") or []
        lead = {**lead, **{field: None for field in unchecked
                          if field in ("review_count", "location_count",
                                       "owner_role", "ordering_platform")}}

        review_count = lead.get("review_count")
        if type(review_count) is not int:
            reasons.append("review_count:unknown")
        elif review_count < self.review_min:
            reasons.append(f"review_count:{review_count}<{self.review_min}")
        elif review_count > self.review_max:
            reasons.append(f"review_count:{review_count}>{self.review_max}")

        location_count = lead.get("location_count")
        if type(location_count) is not int:
            reasons.append("location_count:unknown")
        elif location_count < self.location_min:
            reasons.append(f"location_count:{location_count}<{self.location_min}")
        elif location_count > self.location_max:
            reasons.append(f"location_count:{location_count}>{self.location_max}")

        owner_role = lead.get("owner_role")
        if not owner_role:
            reasons.append("owner_role:unknown")
        elif owner_role not in OWNER_ROLE_LADDER:
            reasons.append(f"owner_role:unrecognized({owner_role!r})")
        else:
            role_rank = OWNER_ROLE_LADDER.index(owner_role)
            if role_rank < self.role_min_rank:
                reasons.append(
                    f"owner_role:{owner_role}<{OWNER_ROLE_LADDER[self.role_min_rank]}")
            elif role_rank > self.role_max_rank:
                # Above the band is a rejection, not a bonus. Selling to a
                # chain's head office is a different sale and usually a wasted
                # message — the band has a ceiling for a reason.
                reasons.append(
                    f"owner_role:{owner_role}>{OWNER_ROLE_LADDER[self.role_max_rank]}")

        ordering_platform = str(lead.get("ordering_platform") or "").lower()
        if not ordering_platform:
            # Blank means nobody found out, which is not the same as "none".
            reasons.append("ordering_platform:unknown")
        elif ordering_platform in self.online_ordering_platforms:
            reasons.append(
                f"ordering_platform:{ordering_platform}:already-takes-online-orders")
        elif ordering_platform != NO_ORDERING_PLATFORM:
            reasons.append(f"ordering_platform:unrecognized({ordering_platform!r})")

        return reasons


def unknown_field_reasons(lead: dict[str, Any], reasons: list[str]) -> list[str]:
    """The needs_review reasons: each unknown rule field, named.

    For ordering_platform the reason also says why it is unknown, from the
    research note Enrich wrote: e.g. "ordering_platform:unknown:awaiting_check"
    (the agent answered; you have not confirmed it) or
    "ordering_platform:unknown:unclear" (the agent could not tell).
    """
    named = []
    for reason in reasons:
        if not (reason.endswith(":unknown") or ":unrecognized(" in reason):
            continue
        if reason.startswith(RESEARCHED_FIELD + ":"):
            research = (lead.get("enrichment") or {}).get("research") or {}
            status = research.get("status")
            if status and status not in ("not_needed", "confirmed"):
                reason = f"{reason}:{status}"
        named.append(reason)
    return named


def would_qualify_once_checked(lead: dict[str, Any], icp: "ICP") -> bool:
    """True if the agent answer waiting for your check would make this lead fit.

    Used only to count MISSED. The decision itself never uses an unconfirmed
    answer.
    """
    research = (lead.get("enrichment") or {}).get("research") or {}
    if research.get("status") != "awaiting_check":
        return False
    return not icp.check({**lead, RESEARCHED_FIELD: research.get("proposed_value")})


def qualify_leads(records: list[dict[str, Any]], icp: ICP | None = None,
                  *, accept_out_of_zone: bool = True) -> dict[str, Any]:
    """Apply four rules; retain practice mistakes without sending them onward.

    accept_out_of_zone records numeric near misses as chased for comparison.
    Both modes send only qualified records to Outreach.
    """
    icp = icp or ICP()
    qualified: list[dict[str, Any]] = []
    chased: list[dict[str, Any]] = []
    missed: list[dict[str, Any]] = []
    taken: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []

    for lead in records:
        reasons = icp.check(lead)
        unknown = unknown_field_reasons(lead, reasons)
        if unknown:
            # A rule Qualify cannot check. It does not guess either way.
            review.append(make_qualified_entry(
                lead, qualified=False, junk=False, reasons=unknown, value=0.0))
            continue

        in_zone = not reasons
        broke_hard_rule = any(reason.split(":")[0] in HARD_RULES for reason in reasons)

        if in_zone:
            # An in-zone lead always goes on the list. There is no scenario
            # where the right move is to let a fitting lead walk.
            qualified.append(make_qualified_entry(
                lead, qualified=True, junk=False, reasons=[], value=1.0))
        elif broke_hard_rule:
            # Nothing to sell, or nobody who can say yes.
            taken.append(make_qualified_entry(
                lead, qualified=False, junk=False, reasons=reasons, value=0.0))
        elif accept_out_of_zone:
            # A near miss: only a number band broke. The swing at a bad pitch.
            chased.append(make_qualified_entry(
                lead, qualified=False, junk=True, reasons=reasons, value=0.0))
        else:
            taken.append(make_qualified_entry(
                lead, qualified=False, junk=False, reasons=reasons, value=0.0))

    # MISSED: a fit that never reached the list. Here that is a needs_review
    # lead whose agent answer, once you check it, would qualify it.
    missed = [entry for entry in review
              if would_qualify_once_checked(entry["lead"], icp)]

    # Mistakes are telemetry, never permission to contact an unqualified lead.
    delivery = qualified

    log.info("qualified %d | rejected %d | needs_review %d (%d would "
             "qualify once you confirm the agent's answer)",
             len(qualified), len(chased) + len(taken), len(review), len(missed))

    return {
        "key": QUALIFIED_KEY,
        "stage": "qualify",
        "at": utc_now(),
        "icp": {
            "review_count": [icp.review_min, icp.review_max],
            "location_count": [icp.location_min, icp.location_max],
            "owner_role": [OWNER_ROLE_LADDER[icp.role_min_rank],
                           OWNER_ROLE_LADDER[icp.role_max_rank]],
            "ordering_platform": NO_ORDERING_PLATFORM,
            "online_ordering_platforms": list(icp.online_ordering_platforms),
        },
        "counts": {
            "qualified": len(qualified),
            "chased": len(chased),
            "taken": len(taken),
            "missed": len(missed),
            "needs_review": len(review),
            "delivered_to_outreach": len(delivery),
            "rejected": len(taken) + len(chased),
        },
        # Only qualified leads may enter Outreach.
        "records": delivery,
        # Kept separately for teaching: these never reach outreach.
        "taken_records": taken,
        "chased_records": chased,
        "rejected_records": taken + chased,
        "review_records": review,
    }


def main(argv: list[str] | None = None) -> int:
    check_python_version_or_exit()
    parser = argparse.ArgumentParser(description="Stage 3 — four rules on the enriched record.")
    parser.add_argument("--in", dest="src", default="out/enriched.json")
    parser.add_argument("--out", default="out/qualified.json")
    parser.add_argument("--review-min", type=int, default=20)
    parser.add_argument("--review-max", type=int, default=1000)
    parser.add_argument("--location-min", type=int, default=1)
    parser.add_argument("--location-max", type=int, default=3)
    parser.add_argument("--role-min", default="Manager")
    parser.add_argument("--role-max", default="Owner")
    parser.add_argument("--platform", action="append", default=None,
                        help="a kind of online ordering that rules a bakery out; "
                             "repeatable, replaces the default list")
    parser.add_argument("--no-chase", action="store_true",
                        help="record numeric near misses as rejected rather than chased practice mistakes")
    parser.add_argument("--offline", action="store_true",
                        help="use bundled practice data even if service keys are set")
    args = parser.parse_args(argv)
    if args.offline:
        os.environ["PIPELINE_OFFLINE"] = "1"
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    payload = json.loads(Path(args.src).read_text())
    records = payload["records"] if isinstance(payload, dict) else payload

    icp = ICP(review_min=args.review_min, review_max=args.review_max,
              location_min=args.location_min, location_max=args.location_max,
              role_min=args.role_min, role_max=args.role_max,
              online_ordering_platforms=(tuple(args.platform) if args.platform
                                         else ONLINE_ORDERING_PLATFORMS))
    result = qualify_leads(records, icp, accept_out_of_zone=not args.no_chase)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2) + "\n")
    counts = result["counts"]
    print(f"✅ {counts['qualified']} qualified; {counts['rejected']} rejected; "
          f"{counts['delivered_to_outreach']} to outreach "
          f"({counts['needs_review']} need review) → {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
