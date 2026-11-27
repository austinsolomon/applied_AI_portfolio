"""A reply starts scheduling. Only a confirmed time becomes an Opportunity.

This module reads confirmations; it never sends invitations. Practice events are
accepted only for an offline run. Live runs require their own recorded confirmation.
"""
from __future__ import annotations
from pathlib import Path
import json
from datetime import datetime

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/meeting_confirmations.json"

def confirmed_meetings(outreach: dict, confirmations: list[dict] | None = None) -> list[dict]:
    if confirmations is None:
        confirmations = [] if (outreach.get("live") or outreach.get("uses_live_provider")) else json.loads(FIXTURE.read_text())
    replies = {r["lead_id"] for r in outreach.get("records", [])
               if r.get("sent") and r.get("delivered") and r.get("replied")}
    events, seen = [], set()
    for event in confirmations:
        if event.get("lead_id") not in replies or event.get("status") != "confirmed":
            continue
        if not event.get("confirmation") or not event.get("evidence"):
            continue
        if (outreach.get("live") or outreach.get("uses_live_provider")) and event.get("simulated", True):
            continue
        try:
            start, end = (datetime.fromisoformat(event[k]) for k in ("starts_at", "ends_at"))
            valid = start.tzinfo is not None and end.tzinfo is not None and end > start
        except (ValueError, KeyError, TypeError):
            valid = False
        if valid and event.get("id") and event["id"] not in seen:
            events.append(dict(event)); seen.add(event["id"])
    return events

def attach_meetings(outreach: dict, confirmations: list[dict] | None = None) -> dict:
    events = confirmed_meetings(outreach, confirmations)
    return {**outreach, "meetings": events,
            "counts": {**outreach.get("counts", {}), "meetings": len(events), "customers": 0},
            "next_stage": "Sales conversations: an Opportunity to pitch, not a sale."}
