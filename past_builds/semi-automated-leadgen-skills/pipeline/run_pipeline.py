"""run_pipeline.py — run Source → Enrich → Qualify → Outreach, one JSON file
between each stage.

Files between stages let you read what each stage produced, re-run one stage
without re-running the one before it, and keep what Enrich paid for if a later
stage crashes.

    out/leads.json      claude3d.leads.v1       raw leads
    out/enriched.json   claude3d.enriched.v1    + contacts, ordering_platform,
                                                research notes, cost ledger
    out/qualified.json  claude3d.qualified.v1   qualified records only
    out/outreach.json   claude3d.outreach.v1    sends + outcomes
    out/funnel.json     claude3d.funnel.v1      the cross-stage numbers

The funnel file holds the numbers across all four stages: HAULED -> LEADS ->
ENRICHED (COMPLETE) -> QUALIFIED/CHASED/MISSED -> SENT -> INBOX. Keys and order
match shared/funnel_ledger.js's STAGE_KEYS, so the game's reader renders both.

Run:  python3 -m pipeline.run_pipeline
"""

from __future__ import annotations

import argparse
import os
import json
import logging
from pathlib import Path
from typing import Any

from .enrich import enrich_leads
from .outreach import run_outreach
from .providers.bakery_outreach_fixture import OFFER
from .qualify import ICP, qualify_leads
from .schema import check_python_version_or_exit, utc_now
from .source_bakeries import DEFAULT_FIXTURE, source_bakeries

log = logging.getLogger("pipeline.run")

# Same order and spelling as funnel_ledger.js STAGE_KEYS. Kept as a list so the
# report renders in pipeline order rather than dict-insertion order by accident.
STAGE_KEYS = ["hauled", "bottles", "leads", "enriched", "complete",
              "qualified", "chased", "missed",
              "sent", "inboxed", "spammed"]


def build_funnel(source: dict, enriched: dict, qualified: dict,
                 outreach: dict) -> dict[str, Any]:
    """Assemble the cross-stage ledger the game's HUD reads."""
    stage_counts = {
        "hauled": source["counts"]["dataset_items"],
        "bottles": max(1, source["counts"]["leads"] // 5),   # 4-5 leads per bottle
        "leads": source["counts"]["leads"],
        "enriched": enriched["counts"]["enriched"],
        "complete": enriched["counts"]["complete"],
        "qualified": qualified["counts"]["qualified"],
        "chased": qualified["counts"]["chased"],
        "missed": qualified["counts"]["missed"],
        "sent": outreach["counts"]["sent"],
        "inboxed": outreach["counts"]["delivered"],
        "spammed": outreach["counts"]["spammed"],
    }
    return {"key": "claude3d.funnel.v1", "at": utc_now(),
            "order": STAGE_KEYS, "stages": stage_counts,
            "qualification": {key: qualified["counts"][key]
                              for key in ("qualified", "rejected", "needs_review")},
            "cost": enriched["cost"],
            "replies": outreach["counts"]["replied"],
            "meetings": outreach["counts"].get("meetings", 0),
            "customers": 0,
            # True only when EVERY provider in the chain was live. A run with one
            # mocked stage is a mocked run; claiming otherwise in a report is how
            # somebody ends up trusting fabricated numbers.
            "live": all([source["live"], enriched["live"], outreach["live"]])}


def render(funnel: dict[str, Any]) -> str:
    """One line across all four stages.

    Saved legacy stage keys remain compatible with older course readers.
    The visible report names the actual decisions and delivery outcomes.
    """
    stages = funnel["stages"]
    decisions = funnel.get("qualification", {})
    return (
        f"PIPELINE  HAULED {stages['hauled']} → LEADS {stages['leads']} → "
        f"ENRICHED {stages['enriched']} (COMPLETE {stages['complete']}) → "
        f"QUALIFIED {stages['qualified']} "
        f"(REJECTED {decisions.get('rejected', 'unknown')}, NEEDS REVIEW {decisions.get('needs_review', 'unknown')}) → "
        f"SENT {stages['sent']} → DELIVERED {stages['inboxed']} "
        f"(UNDELIVERED {stages['spammed']}) → REPLIES {funnel['replies']} → "
        f"MEETINGS {funnel.get('meetings', 0)} (Opportunities to pitch; no sale recorded)"
    )


def main(argv: list[str] | None = None) -> int:
    check_python_version_or_exit()
    parser = argparse.ArgumentParser(description="Run all four stages end to end.")
    parser.add_argument("--outdir", default="out")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--fixture", default=DEFAULT_FIXTURE,
                        help="free rehearsal dataset used when APIFY_TOKEN is unset")
    parser.add_argument("--offer", default=OFFER)
    parser.add_argument("--no-agent", action="store_true",
                        help="skip the research agent in Enrich")
    parser.add_argument("--no-chase", action="store_true")
    parser.add_argument("--offline", action="store_true",
                        help="use bundled practice data even if service keys are set")
    args = parser.parse_args(argv)
    if args.offline:
        os.environ["PIPELINE_OFFLINE"] = "1"
    from .providers.base import load_local_env
    load_local_env()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    fixture = None
    fixture_path = Path(args.fixture)
    if not fixture_path.exists() and not fixture_path.is_absolute():
        # Run from some other folder? Look next to the repository's own files.
        fixture_path = Path(__file__).resolve().parent.parent / args.fixture
    if fixture_path.exists():
        fixture = json.loads(fixture_path.read_text())

    def write_handoff(file_name: str, payload: dict) -> dict:
        (outdir / file_name).write_text(json.dumps(payload, indent=2) + "\n")
        return payload

    sourced = write_handoff("leads.json",
                            source_bakeries(limit=args.limit, fixture=fixture))
    enriched = write_handoff("enriched.json",
                             enrich_leads(sourced["records"],
                                          use_agent=not args.no_agent))
    qualified = write_handoff("qualified.json",
                              qualify_leads(enriched["records"], ICP(),
                                            accept_out_of_zone=not args.no_chase))
    outreach = write_handoff("outreach.json",
                             run_outreach(qualified["records"], offer=args.offer))
    funnel = write_handoff("funnel.json",
                           build_funnel(sourced, enriched, qualified, outreach))

    print()
    print(render(funnel))
    print(f"enrichment cost ${funnel['cost']['total']:.4f} "
          f"(${funnel['cost']['per_lead']:.4f}/lead)")
    for entry in qualified["review_records"]:
        print(f"needs_review: {entry['name']} — {', '.join(entry['reasons'])}")
    awaiting = [record["name"] for record in enriched["records"]
                if (record["enrichment"].get("research") or {}).get("status") == "awaiting_check"]
    for name in awaiting:
        print(f"next: check the agent's answer for {name}: "
              f"python3 -m pipeline.review_research --lead \"{name}\"")
    print(f"mode: {'LIVE' if funnel['live'] else 'PRACTICE (saved data — nothing was sent)'}")
    print(f"handoff files in {outdir}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
