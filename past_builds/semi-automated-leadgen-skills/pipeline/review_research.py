"""review_research.py — part of Enrich: you check an agent answer before it
goes into the record.

The research agent returns an answer for ordering_platform with the page it
read and the passage it quotes. That answer waits in the record's
`enrichment.research` note. It becomes the record's ordering_platform only
when you confirm it. The record then keeps who confirmed it, when, and the
source. After that, Qualify reads the record once.

Show the answer and its source:
    python3 -m pipeline.review_research --lead "Rye & Rosemary Bakery"

Open the source page, read the passage. If it says what the agent says:
    python3 -m pipeline.review_research --lead "Rye & Rosemary Bakery" \\
        --reviewed-by "Your name" --confirm

Then:
    python3 -m pipeline.qualify

This command changes out/enriched.json only. It calls no service and sends
nothing.
"""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .schema import (ENRICHED_FIELDS, NO_ORDERING_PLATFORM,
                     ONLINE_ORDERING_PLATFORMS, RESEARCHED_FIELD,
                     check_python_version_or_exit, derive_columns, utc_now)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIRMATIONS = REPO_ROOT / "fixtures" / "research_confirmations.json"


def load_confirmations(path: Path | str | None) -> list[dict[str, Any]]:
    """Read a confirmations file. A missing path means no confirmations."""
    if not path or not Path(path).exists():
        return []
    payload = json.loads(Path(path).read_text())
    return payload["confirmations"] if isinstance(payload, dict) else payload


def confirm_research_answer(record: dict[str, Any],
                            confirmation: dict[str, Any]) -> dict[str, Any]:
    """Copy a checked agent answer into the record. Return a new record.

    `confirmation` names the bakery, the field, the value, the source page and
    quote you read, and who you are. Every one must match the agent's answer,
    or nothing changes and a ValueError says why.
    """
    research = (record.get("enrichment") or {}).get("research") or {}
    if research.get("status") == "unclear":
        raise ValueError("The agent could not tell. There is no answer to confirm; "
                         "the bakery stays in needs_review until someone finds out.")
    if research.get("status") != "awaiting_check":
        raise ValueError("This bakery has no agent answer waiting for a check.")
    if not str(confirmation.get("reviewed_by") or "").strip():
        raise ValueError("Say who checked the source (reviewed_by).")
    if confirmation.get("lead_id") != record.get("id"):
        raise ValueError("This confirmation belongs to a different bakery.")
    if confirmation.get("field", RESEARCHED_FIELD) != RESEARCHED_FIELD:
        raise ValueError("The research agent answers ordering_platform only.")
    value = confirmation.get("value")
    if value not in (NO_ORDERING_PLATFORM, *ONLINE_ORDERING_PLATFORMS):
        raise ValueError("The value must be 'none' or a known kind of platform.")
    if value != research.get("proposed_value"):
        raise ValueError("The value does not match the agent's answer.")
    source = urlparse(str(confirmation.get("source_url") or ""))
    if source.scheme not in ("http", "https") or not source.netloc:
        raise ValueError("Keep the source page you read.")
    if confirmation["source_url"] != research.get("source_url"):
        raise ValueError("The source page does not match the agent's answer.")
    if not str(confirmation.get("quote") or "").strip():
        raise ValueError("Keep the passage you read.")

    confirmed = deepcopy(record)
    confirmed[RESEARCHED_FIELD] = value
    confirmed.update(derive_columns(confirmed))
    enrichment = confirmed["enrichment"]
    # Imported game records may explicitly mark a present answer unverified.
    # This human check clears only the field it actually reviewed.
    if "unverified" in enrichment:
        enrichment["unverified"] = [field for field in enrichment["unverified"]
                                    if field != RESEARCHED_FIELD]
    enrichment["filled"] = [field for field in ENRICHED_FIELDS
                            if confirmed.get(field) not in (None, "")]
    enrichment["complete"] = len(enrichment["filled"]) == len(ENRICHED_FIELDS)
    enrichment["research"].update({
        "status": "confirmed",
        "confirmed_value": value,
        "confirmed_by": confirmation["reviewed_by"].strip(),
        "confirmed_at": confirmation.get("reviewed_at") or utc_now(),
    })
    return confirmed


def find_record(records: list[dict[str, Any]], lead: str) -> int | None:
    """Index of the record whose id or bakery name matches `lead`."""
    wanted = lead.strip().casefold()
    for index, record in enumerate(records):
        if wanted in (str(record.get("id")).casefold(), str(record.get("name")).casefold()):
            return index
    return None


def main(argv: list[str] | None = None) -> int:
    check_python_version_or_exit()
    parser = argparse.ArgumentParser(
        description="Enrich: check one research-agent answer before it enters the record.")
    parser.add_argument("--in", dest="src", default="out/enriched.json")
    parser.add_argument("--lead", required=True, help="bakery name or lead id")
    parser.add_argument("--reviewed-by", default="")
    parser.add_argument("--confirm", action="store_true",
                        help="you opened the source page and it says what the agent says")
    args = parser.parse_args(argv)

    path = Path(args.src)
    payload = json.loads(path.read_text())
    records = payload["records"]
    index = find_record(records, args.lead)
    if index is None:
        parser.error(f"No bakery called {args.lead!r} in {path}.")
    record = records[index]
    research = (record.get("enrichment") or {}).get("research") or {}

    print(f"{record['name']}: ordering_platform = {record.get(RESEARCHED_FIELD)!r}")
    print(f"  research status: {research.get('status')}")
    if research.get("verdict"):
        print(f"  agent answer:    {research.get('proposed_value')!r} — "
              f"{research['verdict']['researchReason']}")
        print(f"  source:          {research.get('source_url')}")
        print(f"  passage:         \"{research.get('quote')}\"")

    if not args.confirm:
        if research.get("status") == "awaiting_check":
            print("Open the source page and read the passage. If it says the same, "
                  "run again with --reviewed-by \"Your name\" --confirm.")
        return 0

    confirmation = {"lead_id": record["id"], "field": RESEARCHED_FIELD,
                    "value": research.get("proposed_value"),
                    "source_url": research.get("source_url"),
                    "quote": research.get("quote"),
                    "reviewed_by": args.reviewed_by}
    try:
        records[index] = confirm_research_answer(record, confirmation)
    except ValueError as error:
        parser.error(str(error))

    # Imported here: enrich.py imports this module.
    from .enrich import enrichment_counts
    payload["counts"] = enrichment_counts(records)
    path.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"Confirmed by {args.reviewed_by.strip()}. {record['name']} now has "
          f"ordering_platform = {research.get('proposed_value')!r}, "
          f"source {research.get('source_url')}.")
    print("Next: python3 -m pipeline.qualify")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
