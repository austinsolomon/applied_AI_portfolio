---
name: enrich-leads
description: Add the owner, their role, an email, the number of shops, and how the bakery takes orders to raw bakery leads. A contact waterfall looks up the first four: Apollo first, then Hunter (an email finder) only for the contact fields Apollo left open. For each bakery neither can answer on ordering, the research agent from Repository 2 reads the bakery's website and returns an answer with its source, which you confirm before it enters the record. Keeps a per-field cost ledger. Use when leads need owner_name, owner_role, email, location_count or ordering_platform, when someone says "enrich these leads", "find out who owns these bakeries", "run Apollo on this list", "find the missing emails", "check the research answer", or when qualify_leads has nothing to judge. Do NOT use to find new leads (source_leads) or to decide which leads to keep (qualify_leads).
---

## Course mode and paths

Run commands from the repository root (the folder containing `pipeline/`).
Use `python3` on macOS/Linux or `py` on Windows. All practice commands must include
`--offline`; this overrides locally saved service keys. Do not start a live call
or send real messages unless the learner explicitly requests it separately.
A missing input file means run the earlier stage offline first, then inspect its
output. Report facts from the output file; never invent owner/contact data.

# Stage 2 — Enrich

**What it does.** Source gave you each bakery's Google Maps listing: name,
category, address, phone, website, map link, rating and review count. Qualify
needs four more facts to judge a bakery. Enrich finds them and writes one
enriched record per bakery.

**Its place in the pipeline.** Source → **Enrich** → Qualify → Outreach. Enrich
reads `out/leads.json` and writes `out/enriched.json`. Qualify reads that file
and nothing else.

**Game level:** `dog_enrichment`, Hound Ridge.

## The four steps

Steps 1 and 2 are a waterfall: an ordered list of providers, cheapest first.
Each provider is asked only for the fields still missing after the ones before
it, and only for the fields it can find.

1. **Apollo, a contact-data service.** One lookup per bakery. It returns
   `owner_name`, `owner_role`, `email` and `location_count`. It is also asked for
   `ordering_platform` and knows it for only 3 of the 23 bakeries. Contact
   databases track people and companies, not how a bakery takes orders.
2. **Hunter, an email finder.** It searches a company's web domain for people
   and their emails, and costs more per lookup, so it only sees Apollo's gaps.
   It can find `owner_name`, `owner_role` and `email`, never `location_count` or
   `ordering_platform`. A contact on staff (a baker, a shift lead) is not the
   owner, so Hunter is asked for the owner there too. On the course list it
   finds Tin Roof Bakery's email and Morning Crumb's owner, and misses Fig &
   Feather's owner. A field Hunter also misses stays empty.
3. **The research agent**, for each bakery with no `ordering_platform` yet. It reads the
   bakery's website, finds the order page, and returns "no online ordering" or
   the kind of platform, with the page it read as the source. It fills
   `ordering_platform` only. Only the bakery's own website says how it takes
   orders. Without the agent you would phone 20 bakeries to ask.
4. **Your check.** You open the source page and read the quoted passage. An
   answer goes into the record only once you confirm it. The record keeps who
   confirmed it and the source. Then the record goes to Qualify once.

An answer you have not confirmed, or one where the agent could not tell, leaves
`ordering_platform` empty. Qualify then sends that bakery to needs_review.

## The research agent in this repo

The agent is the one you build in Repository 2 (research-agent-four-ways). This
repo does not run it live. Enrich replays its recorded answers from
`fixtures/research_agent_answers.json`, one per bakery, in Repository 2's
verdict shape:

```json
{
  "lead_id": "apify-maps-34ca750a92da",
  "bakery_name": "Rye & Rosemary Bakery",
  "verdict": {
    "supportsNoOnlineOrdering": true,
    "researchReason": "The Order Ahead page only gives a phone number and an emailed order form ...",
    "sourceUrls": ["https://rye-and-rosemary.example/order", "https://rye-and-rosemary.example/about"]
  },
  "platform_kind": null,
  "evidence": {
    "source_url": "https://rye-and-rosemary.example/order",
    "quote": "Order ahead by calling the shop or emailing our order form. We confirm by phone. Pay when you pick up."
  }
}
```

How an answer maps to `ordering_platform`:

| Verdict | `platform_kind` | `ordering_platform` |
|---|---|---|
| `supportsNoOnlineOrdering: true` | — | `"none"` |
| `false` | a kind, e.g. `pos_online_store` | that kind |
| `false` | `null` | stays empty: the agent could not tell |

## Confirmations in the course run

You have already checked 18 of the 20 agent answers in the course. The default
run reads those checks from `fixtures/research_confirmations.json` and copies
each answer into its record. A confirmation must name the same bakery, value
and source page as the answer, or it is not applied.

Two answers are left:

- **Rye & Rosemary Bakery.** The agent read one page further, to `/order`:
  orders by phone or email form only. The answer is waiting for your check.
- **Dos Rios Panaderia.** The agent could not tell. There is nothing to confirm.

Check Rye yourself:

```bash
python3 -m pipeline.review_research --lead "Rye & Rosemary Bakery"
python3 -m pipeline.review_research --lead "Rye & Rosemary Bakery" --reviewed-by "Your name" --confirm
python3 -m pipeline.qualify --offline
```

The first command shows the answer, the source page and the passage, and
changes nothing. The second writes `ordering_platform = "none"` into
`out/enriched.json`, with your name and the source. Qualify then finds 10
qualified bakeries. Trying to confirm Dos Rios is refused.

## Run it

```bash
# Offline: Apollo and Hunter from the fixture, recorded agent answers, recorded checks
python3 -m pipeline.enrich --offline --in out/leads.json --out out/enriched.json

# Without the research agent: ordering_platform stays empty for 20 bakeries
python3 -m pipeline.enrich --offline --in out/leads.json --out out/enriched.json --no-agent

# Use your own confirmations file
python3 -m pipeline.enrich --offline --confirmations my_checks.json

# Live Apollo and Hunter (paid; each key switches only its own provider).
# No --offline here: that flag forces the practice providers even when keys are set.
export APOLLO_API_KEY=...
export HUNTER_API_KEY=...
python3 -m pipeline.enrich --in out/leads.json --out out/enriched.json
```

Run the live command only when the learner explicitly asks for a live, paid
Enrich run. Every practice command above keeps `--offline`.

The live Apollo and Hunter classes are thin adapters in
`pipeline/providers/enrichment.py`. Nothing else changes.

With no key, Apollo and Hunter answer from
`pipeline/providers/bakery_enrichment_fixture.py`. A bakery that is not in that
table gets a miss on every field. The offline providers never make up an owner.

## What it costs

| Provider | Billed | Price |
|---|---|---|
| `apollo(mock)` | per field asked | $0.004 found, $0.001 not found |
| `hunter(mock)` | per field asked, only Apollo's gaps | $0.010 found, $0.003 not found |
| `research_agent(recorded)` | per run | $0.09, whatever the answer |

These are simulated course cost units. The agent amount illustrates what
one live run costs; the recorded answers themselves cost nothing to replay.

## Output

`out/enriched.json`, keyed `claude3d.enriched.v1`. Each record is the raw lead
plus the five fields, three answers worked out from them (`has_website`,
`takes_online_orders`, `is_chain`), and:

```json
"enrichment": {
  "ledger": [{"field": "owner_name", "provider": "apollo(mock)", "hit": true, "cost": 0.004}, ...],
  "cost": 0.107,
  "filled": ["owner_name", "owner_role", "email", "location_count", "ordering_platform"],
  "complete": true,
  "research": {
    "field": "ordering_platform", "status": "confirmed",
    "agent": "research_agent(recorded)", "verdict": {...},
    "proposed_value": "none", "source_url": "https://sunrise.example/menu", "quote": "...",
    "confirmed_value": "none", "confirmed_by": "course fixture (checked in the lesson)",
    "confirmed_at": "2026-09-26T00:00:00Z"
  }
}
```

That is Sunrise Bakery: Apollo found four fields and missed
`ordering_platform`; Hunter was not needed; the agent answered "none"; the
answer was confirmed. Six ledger lines, $0.107.

`research.status` is one of:

| Status | Meaning |
|---|---|
| `not_needed` | Apollo answered `ordering_platform` |
| `confirmed` | the agent answered and you confirmed it |
| `awaiting_check` | the agent answered; you have not confirmed it yet |
| `unclear` | the agent could not tell |
| `not_run` | the agent was skipped (`--no-agent`) |

## Reading the summary lines

```
apollo answered ordering_platform for 3/23 | research agent asked about 20: 18 confirmed, 1 awaiting your check, 1 unclear
cost $2.2460 total, $0.0977/lead | 21/23 complete
```

- **complete** means all five fields are filled. The two incomplete records
  are Rye & Rosemary Bakery (answer awaiting your check) and Dos Rios Panaderia
  (the agent could not tell). Tin Roof Bakery is complete: Hunter found the
  email Apollo lacked.
- Morning Crumb: Apollo lists a baker on staff; Hunter finds the owner, so
  Qualify can qualify it. Fig & Feather: Hunter misses, the shift lead stays,
  and Qualify rejects it on the owner-role rule.
- Cost by provider: Apollo $0.397, Hunter $0.049, research agent $1.80.

## Checks before moving on

- Total cost equals the sum of the ledger lines.
- Every filled `ordering_platform` came from Apollo (`not_needed`) or from a
  `confirmed` answer. None came from an unconfirmed one.
- Re-running on the same input gives the same records and the same cost.

Next: **qualify_leads**.
