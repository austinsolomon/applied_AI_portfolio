"""Stage 4: check qualification, prepare and lint email, track replies and meetings.

Only bakeries that pass all four rules enter the course's email run. The local
fixture records delivery, replies and a separately confirmed meeting. A reply
is not a booking, and a booking is not a sale. Follow-up schedules are plans;
this rehearsal does not send any real messages.
"""

from __future__ import annotations

from .meetings import attach_meetings
from .qualify import ICP

import argparse
import os
import json
import logging
import re
from pathlib import Path
from typing import Any

from .providers.bakery_outreach_fixture import (BAKERY_RUN_CHANNELS, OFFER,
                                                BakeryFixtureSendProvider,
                                                BakeryTemplateCopyProvider)
from .providers.copy import CHANNEL_MODEL, get_copy_provider, get_send_provider
from .schema import OUTREACH_KEY, check_python_version_or_exit, utc_now

log = logging.getLogger("pipeline.outreach")

# Words that move a message toward the spam folder. Short and boring on purpose:
# a 400-word blocklist produces false positives, and a lint everybody overrides
# is a lint nobody runs.
SPAM_WORDS = ("free", "guarantee", "guaranteed", "risk-free", "act now",
              "limited time", "no obligation", "click here", "buy now",
              "100%", "winner", "urgent")

LINK_RE = re.compile(r"https?://\S+")
# Three or more consecutive capitals reads as shouting. Two is fine, so "IL"
# passes. "PDF" and "BBQ" do not: reword the sentence, never the check.
CAPS_RE = re.compile(r"\b[A-Z]{3,}\b")

MAX_LINKS = 1
MAX_WORDS = 120


def lint(message: dict[str, str]) -> list[str]:
    """Return the deliverability problems in one message. Empty list = sendable."""
    problems: list[str] = []
    text = f"{message.get('subject','')} {message.get('body','')}"
    lower = text.lower()

    caps = [word for word in CAPS_RE.findall(text)
            if word not in {"CRM", "API", "SDR", "ICP", "SaaS"}]
    if caps:
        problems.append(f"all-caps:{','.join(sorted(set(caps))[:4])}")

    links = LINK_RE.findall(text)
    if len(links) > MAX_LINKS:
        problems.append(f"links:{len(links)}>{MAX_LINKS}")

    hits = [phrase for phrase in SPAM_WORDS if phrase in lower]
    if hits:
        problems.append(f"spam-words:{','.join(hits)}")

    if text.count("!") > 1:
        problems.append(f"exclamations:{text.count('!')}")

    words = len(message.get("body", "").split())
    if words > MAX_WORDS:
        problems.append(f"length:{words}>{MAX_WORDS}")

    return problems


def allocate(message_count: int, channels: list[str],
             prior: dict[str, dict[str, int]] | None = None) -> dict[str, int]:
    """Split message_count messages across channels.

    ROUND 1 IS EVEN, AND THAT IS NOT LAZINESS. With no measurements, an even
    split is the only allocation that buys information about every channel.
    From round 2 we weight by MEASURED conversion-per-send — never by delivery
    rate, which is the number that tempts everyone toward high-volume email and
    is exactly the trap delivery_city sets (email delivers 88% and converts 10%;
    linkedin delivers 52% and converts 50%).

    Every channel keeps a floor of one message so a good channel that had one
    unlucky round can climb back — an allocator that can zero a channel can
    never learn it was wrong.
    """
    if not channels:
        return {}
    if not prior:
        base, extra = divmod(message_count, len(channels))
        return {channel: base + (1 if position < extra else 0)
                for position, channel in enumerate(channels)}

    scores = {}
    for channel in channels:
        last_round = prior.get(channel, {})
        sent = max(1, last_round.get("sent", 0))
        scores[channel] = last_round.get("replied", 0) / sent
    total = sum(scores.values())
    if total <= 0:
        return allocate(message_count, channels)

    alloc = {channel: max(1, int(message_count * scores[channel] / total))
             for channel in channels}
    # Integer flooring plus the per-channel floor can drift off message_count;
    # settle the difference on the best channel rather than silently sending a
    # different number of messages than the caller asked for.
    drift = message_count - sum(alloc.values())
    if drift:
        best = max(channels, key=lambda channel: scores[channel])
        alloc[best] = max(1, alloc[best] + drift)
    return alloc


def plan_sequence(lead_id: str, channel: str,
                  steps: tuple[int, ...] = (0, 3, 7)) -> list[dict[str, Any]]:
    """The follow-up schedule, as data. NOTHING IS SENT FROM HERE.

    Days 0 / 3 / 7 is a conventional three-touch cadence. Emitting the plan
    rather than executing it means a learner can inspect and change the cadence
    before a single follow-up leaves the building — and means a bug in this repo
    cannot mail anyone five times.
    """
    return [{"lead_id": lead_id, "channel": channel, "step": step_number,
             "day_offset": day_offset, "status": "planned"}
            for step_number, day_offset in enumerate(steps)]


def pick_copy_provider():
    """ANTHROPIC_API_KEY set -> Claude writes. Unset -> the bakery templates."""
    provider_from_keys = get_copy_provider()
    return provider_from_keys if provider_from_keys.live else BakeryTemplateCopyProvider()


def pick_send_provider():
    """A sender key set -> the live adapter. Unset -> outcomes from the fixture."""
    provider_from_keys = get_send_provider()
    return provider_from_keys if provider_from_keys.live else BakeryFixtureSendProvider()


def fit_to_channel(copy_provider, item: dict[str, Any], offer: str,
                   channel: str) -> dict[str, str]:
    """Re-word one linted draft for the channel it was allocated to.

    An Instagram message has no subject line and the second email campaign is
    shorter, so a writer that knows how may re-word the draft. THE RE-WORDED
    MESSAGE IS LINTED TOO. If it fails, it is thrown away and the draft that
    already passed goes out instead. Nothing unlinted is ever sent.
    """
    write_for_channel = getattr(copy_provider, "write_for_channel", None)
    if write_for_channel is None:
        return item["message"]
    message = write_for_channel(item["lead"], offer, channel)
    return item["message"] if lint(message) else message


# Which column holds the address each lane writes to. An Instagram message goes
# to the bakery's public profile, found by its name, so there is no column to
# check. A bakery record has no LinkedIn column, so that lane falls back to email.
ADDRESS_FIELD_BY_CHANNEL = {"email": "email", "email_campaign_2": "email",
                            "instagram_dm": None,
                            "linkedin_inmail": "linkedin"}
NO_VERIFIED_ADDRESS = "no-verified-address"


def channel_with_a_verified_address(lead: dict[str, Any], channel: str) -> str | None:
    """The lane this message may really go out on, or None to hold it.

    If the allocated lane needs an address and the record has none, the
    message goes by email instead. If the email is blank too, nothing is sent.
    On the 25-bakery list this never fires. It is here so a changed run cannot
    spend a message on a blank address.
    """
    for candidate in (channel, "email"):
        address_field = ADDRESS_FIELD_BY_CHANNEL.get(candidate, "email")
        if address_field is None or lead.get(address_field):
            return candidate
    return None


def run_outreach(records: list[dict[str, Any]], *, offer: str = OFFER,
                 channels: list[str] | None = None,
                 prior: dict[str, dict[str, int]] | None = None,
                 copy_provider=None, send_provider=None, drafts_only: bool = False) -> dict[str, Any]:
    # The course follows Outbound email; other channels remain optional examples.
    channels = channels or list(BAKERY_RUN_CHANNELS)
    copy_provider = copy_provider or pick_copy_provider()
    send_provider = send_provider or (None if drafts_only else pick_send_provider())

    # 1 + 2: write, then lint. Held messages never reach the allocator, so a
    # spammy batch shrinks the send rather than poisoning the domain.
    drafted, held = [], []
    for entry in records:
        lead = entry.get("lead", entry)
        reasons = ICP().check(lead)
        if entry.get("qualified") is False or entry.get("junk") or reasons:
            held.append({"lead": lead, "message": {}, "lint": ["not-qualified", *reasons]})
            continue
        message = copy_provider.write(lead, offer)
        problems = lint(message)
        (held if problems else drafted).append(
            {"lead": lead, "message": message, "lint": problems})

    if held:
        log.warning("HELD %d/%d messages that failed the deliverability lint — "
                    "fix the copy, do not override the lint", len(held), len(records))

    if drafts_only:
        return {
            "key": OUTREACH_KEY, "stage": "outreach", "at": utc_now(),
            "mode": "drafts_only", "live": False,
            "uses_live_provider": bool(getattr(copy_provider, "live", False)),
            "offer": offer,
            "counts": {"drafted": len(drafted), "held": len(held), "sent": 0,
                       "delivered": 0, "bounced": 0, "replied": 0,
                       "spammed": 0, "meetings": 0},
            "drafts": [{"lead_id": item["lead"]["id"], "email": item["lead"].get("email"),
                        "message": item["message"]} for item in drafted],
            "held_records": held, "records": [], "sent_messages": [],
            "meetings": [], "by_channel": {}, "allocation": {}, "sequence": [],
        }

    # 3: allocate.
    alloc = allocate(len(drafted), channels, prior)
    log.info("allocation: %s", alloc)

    # 4: send and track.
    sent_messages, results, sequence = [], [], []
    cursor = 0
    for allocated_channel, count in alloc.items():
        for item in drafted[cursor:cursor + count]:
            lead = item["lead"]
            channel = channel_with_a_verified_address(lead, allocated_channel)
            if channel is None:
                held.append({**item, "lint": [NO_VERIFIED_ADDRESS]})
                continue
            message = fit_to_channel(copy_provider, item, offer, channel)
            sent_messages.append({"lead_id": lead["id"], "channel": channel,
                                  "message": message})
            results.append(send_provider.send(lead, channel, message))
            sequence.extend(plan_sequence(lead["id"], channel))
        cursor += count

    per_channel: dict[str, dict[str, int]] = {}
    for result in results:
        tally = per_channel.setdefault(result["channel"],
                                       {"sent": 0, "delivered": 0, "bounced": 0, "replied": 0})
        tally["sent"] += 1
        tally["delivered"] += int(result["delivered"])
        tally["bounced"] += int(result["bounced"])
        tally["replied"] += int(result["replied"])
    for tally in per_channel.values():
        # The bottom line of the game's scoreboard: replies per SEND, not per
        # delivery. Per-delivery flatters low-delivery channels; the money
        # question is what one send is worth.
        tally["reply_rate"] = round(tally["replied"] / max(1, tally["sent"]), 4)

    sent = sum(tally["sent"] for tally in per_channel.values())
    delivered = sum(tally["delivered"] for tally in per_channel.values())
    replied = sum(tally["replied"] for tally in per_channel.values())
    log.info("sent %d | delivered %d | replied %d | held %d",
             sent, delivered, replied, len(held))

    return attach_meetings({
        "key": OUTREACH_KEY,
        "stage": "outreach",
        "at": utc_now(),
        "offer": offer,
        "live": bool(getattr(copy_provider, "live", False)
                     and getattr(send_provider, "live", False)),
        "uses_live_provider": bool(getattr(copy_provider, "live", False)
                     or getattr(send_provider, "live", False)),
        "counts": {
            "drafted": len(drafted), "held": len(held),
            "sent": sent, "delivered": delivered,
            "bounced": sum(tally["bounced"] for tally in per_channel.values()),
            "replied": replied,
            "spammed": sent - delivered,
        },
        "allocation": alloc,
        "by_channel": per_channel,
        "records": results,
        "sent_messages": sent_messages,
        "held_records": held,
        "sequence": sequence,
    })


def main(argv: list[str] | None = None) -> int:
    check_python_version_or_exit()
    parser = argparse.ArgumentParser(description="Stage 4 — copy, lint, allocate, send.")
    parser.add_argument("--in", dest="src", default="out/qualified.json")
    parser.add_argument("--out", default="out/outreach.json")
    parser.add_argument("--offer", default=OFFER)
    parser.add_argument("--channel", action="append", default=None,
                        choices=list(CHANNEL_MODEL),
                        help="repeatable; default: " + " ".join(BAKERY_RUN_CHANNELS))
    parser.add_argument("--prior", default=None,
                        help="a previous outreach.json; its by_channel drives re-allocation")
    parser.add_argument("--drafts-only", action="store_true",
                        help="save checked drafts without selecting or calling any sender")
    parser.add_argument("--offline", action="store_true",
                        help="use bundled practice data even if service keys are set")
    args = parser.parse_args(argv)
    if args.offline:
        os.environ["PIPELINE_OFFLINE"] = "1"
    from .providers.base import load_local_env
    load_local_env()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    payload = json.loads(Path(args.src).read_text())
    records = payload["records"] if isinstance(payload, dict) else payload

    prior = None
    if args.prior:
        prior = json.loads(Path(args.prior).read_text()).get("by_channel")

    result = run_outreach(records, offer=args.offer, channels=args.channel, prior=prior, drafts_only=args.drafts_only)
    out = Path("out/drafts.json" if args.drafts_only and args.out == "out/outreach.json" else args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n")
    counts = result["counts"]
    if args.drafts_only:
        print(f"Saved {counts['drafted']} checked drafts and {counts['held']} held records to {out}. No sender was called.")
        return 0
    print(f"✅ sent {counts['sent']} → delivered {counts['delivered']} → replied {counts['replied']} "
          f"({counts['held']} held by the lint) → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
