"""enrich.py — STAGE 2. Add the owner, the contact, the size and how the bakery
takes orders.

Source gave you each bakery's listing. Qualify needs four more facts to judge
it. Enrich finds them with a WATERFALL: an ordered list of tools, cheapest
first. Each tool is asked only for what the tools before it could not find.

  1. APOLLO (a contact-data service). One lookup per bakery. It returns
     owner_name, owner_role, email and location_count. It is also asked for
     ordering_platform, and it knows that for only 3 of the 23 bakeries.

  2. HUNTER (an email finder). It searches a company's web domain for people
     and their emails, and costs more per lookup than Apollo. It is asked only
     for the contact fields Apollo left empty, and only for the ones it can
     find: owner_name, owner_role and email. A contact on staff (a baker, a
     shift lead) is not the owner, so Hunter is asked for the owner there too.
     On the course list Hunter finds Tin Roof Bakery's email and Morning
     Crumb's owner. A field Hunter also misses stays empty.

  3. THE RESEARCH AGENT, for each bakery with no ordering_platform yet. It
     reads the bakery's website, finds the order page, and returns "no online
     ordering" or the kind of platform, with the page it read as the source.
     It fills ordering_platform only. Without it you would phone 20 bakeries
     to ask.

  4. YOUR CHECK. An agent answer goes into the record only once you confirm
     it: you open the source page and read the quoted passage. The record
     keeps who confirmed it and the source. An answer you have not confirmed,
     or one where the agent could not tell, leaves ordering_platform empty,
     and Qualify sends that bakery to needs_review.

The course run replays the agent's recorded answers
(fixtures/research_agent_answers.json) and the answers you have already
checked (fixtures/research_confirmations.json). Rye & Rosemary Bakery is not
in the confirmations: you check it yourself with pipeline.review_research.

Every lookup writes a ledger line (field, provider, hit, cost), so you can see
what each lead cost.

Run:  python3 -m pipeline.enrich --in out/leads.json --out out/enriched.json
"""

from __future__ import annotations

import argparse
import os
import json
import logging
from pathlib import Path
from typing import Any

from .providers.enrichment import (RecordedResearchAgent,
                                   get_contact_data_providers,
                                   ordering_platform_from_answer)
from .review_research import (DEFAULT_CONFIRMATIONS, confirm_research_answer,
                              load_confirmations)
from .schema import (ENRICHED_FIELDS, ENRICHED_KEY, PERSON_FIELDS,
                     RESEARCHED_FIELD, STAFF_ROLES, check_python_version_or_exit,
                     dedupe_by_id, is_raw_lead, make_enriched, utc_now)

log = logging.getLogger("pipeline.enrich")


def fields_still_missing(columns: dict[str, Any]) -> list[str]:
    """The fields no provider has answered yet, in ENRICHED_FIELDS order.

    One more rule: a contact on staff (a baker, a shift lead) is not the owner.
    Their name, role and email still count as missing, so the next provider
    looks for the owner. If no provider finds one, the staff contact stays.
    """
    contact_is_staff = columns["owner_role"] in STAFF_ROLES
    return [field for field in ENRICHED_FIELDS
            if columns[field] is None
            or (contact_is_staff and field in PERSON_FIELDS)]


def look_up_contacts(lead: dict[str, Any], contact_providers: list[Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Step 1: the waterfall. Return (columns, ledger lines).

    Ask each provider in order, cheapest first. Each one is asked only for the
    fields that are still missing and that it can find.
    """
    columns = {field: None for field in ENRICHED_FIELDS}
    ledger = []
    for provider in contact_providers:
        fields_to_ask = [field for field in fields_still_missing(columns)
                         if field in provider.fields_it_can_find]
        if not fields_to_ask:
            continue  # Nothing left that this provider could find.

        found = provider.lookup(lead, fields_to_ask)

        if found.get("owner_name") not in (None, columns["owner_name"]):
            # A different person. Forget the last person's role and email,
            # so the record never mixes two people.
            columns["owner_role"] = None
            columns["email"] = None

        for field in fields_to_ask:
            hit = field in found
            if hit:
                columns[field] = found[field]
            ledger.append({
                "field": field,
                "provider": provider.name,
                "hit": hit,
                # A lookup that finds nothing still uses a credit.
                "cost": provider.cost_per_hit if hit else provider.cost_per_miss,
            })
    return columns, ledger


def research_ordering_platform(lead: dict[str, Any], research_agent: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Step 2: ask the research agent. Return (research note, ledger line).

    The agent's answer is kept in the note as `proposed_value`. It is not
    written into the record here. That happens only when you confirm it.
    """
    answer = research_agent.research_ordering_platform(lead)
    proposed_value = ordering_platform_from_answer(answer)
    ledger_line = {"field": RESEARCHED_FIELD, "provider": research_agent.name,
                   "hit": proposed_value is not None,
                   "cost": research_agent.cost_per_run}
    if answer is None:
        return ({"field": RESEARCHED_FIELD, "status": "no_answer",
                 "agent": research_agent.name}, ledger_line)
    note = {
        "field": RESEARCHED_FIELD,
        # awaiting_check: a clear answer you have not confirmed yet.
        # unclear: the agent could not tell, so there is nothing to confirm.
        "status": "awaiting_check" if proposed_value is not None else "unclear",
        "agent": research_agent.name,
        "verdict": answer["verdict"],
        "proposed_value": proposed_value,
        "source_url": answer["evidence"]["source_url"],
        "quote": answer["evidence"]["quote"],
    }
    return note, ledger_line


def enrich_one(lead: dict[str, Any], contact_providers: list[Any],
               research_agent: Any | None,
               confirmations: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Enrich one bakery: the contact waterfall, then the agent if needed, then your check."""
    columns, ledger = look_up_contacts(lead, contact_providers)

    if columns[RESEARCHED_FIELD] is not None:
        answered_by = next(line["provider"] for line in ledger
                           if line["field"] == RESEARCHED_FIELD and line["hit"])
        research = {"field": RESEARCHED_FIELD, "status": "not_needed",
                    "answered_by": answered_by}
    elif research_agent is None:
        research = {"field": RESEARCHED_FIELD, "status": "not_run"}
    else:
        research, ledger_line = research_ordering_platform(lead, research_agent)
        ledger.append(ledger_line)

    record = make_enriched(lead, columns, ledger, research)

    confirmation = confirmations.get(lead["id"])
    if research["status"] == "awaiting_check" and confirmation is not None:
        try:
            record = confirm_research_answer(record, confirmation)
        except ValueError as error:
            log.warning("%s: confirmation not applied (%s)", lead.get("name"), error)
    return record


def enrichment_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    """The numbers Enrich reports: records, complete ones, and research status."""
    statuses = [(record["enrichment"].get("research") or {}).get("status")
                for record in records]
    return {
        "enriched": len(records),
        "complete": sum(1 for record in records if record["enrichment"]["complete"]),
        "ordering_from_apollo": statuses.count("not_needed"),
        "researched": sum(1 for status in statuses
                          if status in ("confirmed", "awaiting_check", "unclear", "no_answer")),
        "confirmed": statuses.count("confirmed"),
        "awaiting_check": statuses.count("awaiting_check"),
        "unclear": statuses.count("unclear") + statuses.count("no_answer"),
    }


def enrich_leads(records: list[dict[str, Any]], *, use_agent: bool = True,
                 contact_providers: list[Any] | None = None,
                 research_agent: Any | None = None,
                 confirmations: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Enrich every lead and return the claude3d.enriched.v1 payload.

    `contact_providers` is the waterfall, cheapest first. It defaults to
    [Apollo, Hunter]. `confirmations` defaults to the course's recorded
    checks. Pass [] to see every agent answer waiting for your check.
    """
    providers = contact_providers or get_contact_data_providers()
    agent = None
    if use_agent:
        agent = research_agent or RecordedResearchAgent()
    if confirmations is None:
        confirmations = load_confirmations(DEFAULT_CONFIRMATIONS)
    confirmations_by_lead_id = {entry["lead_id"]: entry for entry in confirmations}

    leads = dedupe_by_id(records)
    log.info("enriching %d leads: %s for contacts%s", len(leads),
             " then ".join(provider.name for provider in providers),
             f", {agent.name} for ordering_platform where they have no answer"
             if agent else ", research agent skipped (--no-agent)")

    out = [enrich_one(lead, providers, agent, confirmations_by_lead_id) for lead in leads]

    by_provider: dict[str, float] = {}
    for record in out:
        for line in record["enrichment"]["ledger"]:
            by_provider[line["provider"]] = round(
                by_provider.get(line["provider"], 0.0) + line["cost"], 6)
    total = round(sum(by_provider.values()), 4)
    counts = enrichment_counts(out)

    log.info("apollo answered ordering_platform for %d/%d | research agent asked "
             "about %d: %d confirmed, %d awaiting your check, %d unclear",
             counts["ordering_from_apollo"], len(out), counts["researched"],
             counts["confirmed"], counts["awaiting_check"], counts["unclear"])
    log.info("cost $%.4f total, $%.4f/lead | %d/%d complete",
             total, total / max(1, len(out)), counts["complete"], len(out))

    return {
        "key": ENRICHED_KEY,
        "stage": "enrich",
        "at": utc_now(),
        # Live only when every contact provider in the waterfall is live.
        "live": all(getattr(provider, "live", False) for provider in providers),
        "counts": counts,
        "cost": {"basis": "simulated course units, not a provider invoice", "total": total, "per_lead": round(total / max(1, len(out)), 6),
                 "by_provider": by_provider},
        "records": out,
    }


def main(argv: list[str] | None = None) -> int:
    check_python_version_or_exit()
    parser = argparse.ArgumentParser(
        description="Stage 2 — Apollo then Hunter for contacts, the research agent for ordering_platform.")
    parser.add_argument("--in", dest="src", default="out/leads.json")
    parser.add_argument("--out", default="out/enriched.json")
    parser.add_argument("--no-agent", action="store_true",
                        help="skip the research agent; ordering_platform stays "
                             "empty wherever Apollo has no answer")
    parser.add_argument("--confirmations", default=str(DEFAULT_CONFIRMATIONS),
                        help="the agent answers you have already checked")
    parser.add_argument("--offline", action="store_true",
                        help="use bundled practice data even if service keys are set")
    args = parser.parse_args(argv)
    # A mistyped confirmations path must stop the run. Reading it as "no
    # confirmations" would silently overwrite out/enriched.json with every
    # checked answer back at awaiting_check, and Qualify would collapse.
    if args.confirmations and not Path(args.confirmations).is_file():
        parser.error(f"--confirmations file not found: {args.confirmations}. "
                     "Check the name and folder. Nothing was written.")
    if args.offline:
        os.environ["PIPELINE_OFFLINE"] = "1"
    from .providers.base import load_local_env
    load_local_env()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    payload = json.loads(Path(args.src).read_text())
    records = payload["records"] if isinstance(payload, dict) else payload

    # A record that is already enriched is passed through, not billed again.
    already = [record for record in records if not is_raw_lead(record)]
    if already:
        log.warning("%d/%d input records are already enriched; they are passed "
                    "through untouched", len(already), len(records))
    fresh = [record for record in records if is_raw_lead(record)]

    result = enrich_leads(fresh, use_agent=not args.no_agent,
                          confirmations=load_confirmations(args.confirmations))
    result["records"] = dedupe_by_id(already + result["records"])

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n")
    counts = result["counts"]
    print(f"✅ {counts['enriched']} enriched ({counts['complete']} complete, "
          f"{counts['awaiting_check']} agent answer(s) awaiting your check) "
          f"${result['cost']['total']:.4f} → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
