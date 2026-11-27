# Stage 2 — Enrich · Apollo, Hunter, the research agent, and your check

| | |
|---|---|
| **Game level** | `dog_enrichment` — Hound Ridge |
| **Real tools** | Apollo (a contact-data service), Hunter (an email finder) and the research agent from Repository 2 |
| **Skill** | `skills/enrich_leads/SKILL.md` |
| **Scripts** | `pipeline/enrich.py`, `pipeline/review_research.py` |
| **Handoff** | `out/enriched.json`, key `claude3d.enriched.v1` |

## What this stage does

Source → **Enrich** → Qualify → Outreach.

Source gave you each bakery's Google Maps listing: name, category, address,
city, state, zip, phone, website, map link, rating and review count. That is
free. It says nothing about who runs the bakery or how it takes orders.

Qualify needs those facts to judge a bakery. Enrich finds them and writes one
record per bakery. Qualify reads that record and nothing else.

## The five fields

| Field | What it holds | Who fills it | Example (Sunrise Bakery) |
|---|---|---|---|
| `owner_name` | the person you write to | Apollo, else Hunter | Rosa Delgado |
| `owner_role` | a rung on the role ladder | Apollo, else Hunter | Owner |
| `email` | that person's address | Apollo, else Hunter | rosa@sunrise.example |
| `location_count` | how many shops; 0 means no storefront | Apollo | 1 |
| `ordering_platform` | `none`, or the kind of online ordering it has | Apollo (3 of 23), else the research agent, after you confirm | none |

Three more are worked out from those: `has_website`, `takes_online_orders` and
`is_chain`.

## Step 1: the contact waterfall (Apollo, then Hunter)

A waterfall is an ordered list of providers, cheapest first. Each provider is
asked only for the fields still missing after the providers before it, and
only for the fields it can find. `look_up_contacts()` is one loop over that
list:

```python
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
```

`get_contact_data_providers()` returns the list: `[Apollo, Hunter]`.

**Apollo** is a contact-data service. You give it a bakery; it returns what its
database holds. It is asked for all five fields. It knows the owner, role,
email and number of shops for almost every bakery on the list. It knows
`ordering_platform` for only 3 of 23 (Velvet Whisk Patisserie, Poppyseed
Bakehouse, Daily Dozen Donuts & Bakery). Contact databases track people and
companies, not how a bakery takes orders. Apollo bills per field asked: $0.004
when it finds the field, $0.001 when it does not. A lookup that finds nothing
still uses a credit.

**Hunter** (hunter.io) is an email finder. It searches a company's web domain
for the people who work there and their emails. It can find `owner_name`,
`owner_role` and `email`, never `location_count` or `ordering_platform`. It
costs more per lookup ($0.010 found, $0.003 not found), so it goes second and
only sees the gaps Apollo left:

- **Tin Roof Bakery.** Apollo has the manager, Luis Navarro, but no email.
  Hunter is asked for `email` only, and finds luis@tin-roof-bakery.example.
- **Morning Crumb.** Apollo's contact is a baker on staff, not the owner. A
  contact on staff (Counter Staff, Baker, Shift Lead) counts as no owner yet,
  so Hunter is asked for the owner's name, role and email. It finds the owner,
  Nora Lindqvist. A new person replaces the old one whole: the record never
  keeps one person's name with another person's email.
- **Fig & Feather Bakery.** Apollo's contact is a shift lead, so Hunter is
  asked for the owner too, and misses. The shift lead stays, and Qualify
  rejects the bakery on the owner-role rule.

Every other bakery is complete after Apollo, so Hunter is never asked. A field
Hunter also misses stays empty.

## Step 2: the research agent

Only the bakery's own website says how it takes orders. Without the agent you
would phone 20 bakeries to ask.

For each bakery with no `ordering_platform` after the waterfall, `research_ordering_platform()` asks
the research agent. It reads the bakery's website, finds the order page, and
returns "no online ordering" or the kind of platform, with the page it read as
the source. It fills `ordering_platform` only.

The agent is the one you build in Repository 2 (research-agent-four-ways). This
repo replays its recorded answers from `fixtures/research_agent_answers.json`,
so the run is offline and free. Each answer has Repository 2's verdict shape
plus the page and passage you check:

| Key | Meaning |
|---|---|
| `verdict.supportsNoOnlineOrdering` | `true`: the pages show no online ordering |
| `verdict.researchReason` | one sentence |
| `verdict.sourceUrls` | the pages the verdict rests on |
| `platform_kind` | the kind of online ordering, when the verdict is `false` and a page shows one; `null` when the agent could not tell |
| `evidence.source_url`, `evidence.quote` | the page and passage you check |

`ordering_platform_from_answer()` maps an answer to a value: `true` gives
`"none"`, `false` with a kind gives that kind, and `false` with no kind gives
nothing, because the agent could not tell.

The agent's answer is **not** written into the record at this step. It waits
in `enrichment.research` as `proposed_value`, with its source and quote.

Run with `--no-agent` to skip this step. `ordering_platform` then stays empty
for the 20 bakeries Apollo could not answer, and Qualify sends them all to
needs_review.

## Step 3: your check

You check the source before an answer goes into the record. Open the page,
read the passage, and confirm it if it says what the agent says.
`confirm_research_answer()` then writes the value into `ordering_platform`, and
the record keeps who confirmed it, when, and the source:

```json
"research": {
  "field": "ordering_platform", "status": "confirmed",
  "agent": "research_agent(recorded)", "verdict": {...},
  "proposed_value": "none",
  "source_url": "https://sunrise.example/menu", "quote": "...",
  "confirmed_value": "none",
  "confirmed_by": "course fixture (checked in the lesson)",
  "confirmed_at": "2026-09-26T00:00:00Z"
}
```

A confirmation must name the same bakery, value and source page as the answer.
Anything else is refused and the record does not change.

**In the course run** you have already checked 18 of the 20 answers, so Enrich
reads those checks from `fixtures/research_confirmations.json`. Two are left:

- **Rye & Rosemary Bakery.** The agent read one page further, to `/order`, and
  found orders by phone or email form only. The answer is waiting for your
  check.
- **Dos Rios Panaderia.** The agent could not tell: the bakery's own pages never
  say how it takes orders, and the only outside listing is undated. There is
  nothing to confirm.

Check Rye yourself:

```bash
python3 -m pipeline.review_research --lead "Rye & Rosemary Bakery"
python3 -m pipeline.review_research --lead "Rye & Rosemary Bakery" --reviewed-by "Your name" --confirm
python3 -m pipeline.qualify
```

The first command shows the answer, the source and the passage, and changes
nothing. The second writes the confirmed value into `out/enriched.json`. Then
Qualify reads the record once and finds 10 qualified bakeries. Dos Rios stays in
needs_review.

## The research status on each record

| `research.status` | Meaning | `ordering_platform` |
|---|---|---|
| `not_needed` | Apollo answered | Apollo's value |
| `confirmed` | the agent answered and you confirmed it | the confirmed value |
| `awaiting_check` | the agent answered; not confirmed yet | empty |
| `unclear` | the agent could not tell | empty |
| `not_run` | `--no-agent` | empty |

## The ledger

Every lookup writes a line, so you can see what each lead cost:

```json
"ledger": [
  {"field": "owner_name",        "provider": "apollo(mock)",             "hit": true,  "cost": 0.004},
  {"field": "owner_role",        "provider": "apollo(mock)",             "hit": true,  "cost": 0.004},
  {"field": "email",             "provider": "apollo(mock)",             "hit": true,  "cost": 0.004},
  {"field": "location_count",    "provider": "apollo(mock)",             "hit": true,  "cost": 0.004},
  {"field": "ordering_platform", "provider": "apollo(mock)",             "hit": false, "cost": 0.001},
  {"field": "ordering_platform", "provider": "research_agent(recorded)", "hit": true,  "cost": 0.09}
]
```

That is Sunrise Bakery: $0.107. Apollo answered every contact field, so Hunter
was never asked and wrote no line. The agent line is billed at $0.09, roughly what
a run might consume; these are not current prices. Replaying the saved answer is free.

Tin Roof Bakery's ledger has one Hunter line between Apollo's and the agent's:

```json
  {"field": "email", "provider": "hunter(mock)", "hit": true, "cost": 0.01},
```

## What the 25 listings produce

```
apollo answered ordering_platform for 3/23 | research agent asked about 20: 18 confirmed, 1 awaiting your check, 1 unclear
cost $2.2460 total, $0.0977/lead | 21/23 complete
```

- 23 enriched, 21 complete. The two incomplete records are Rye & Rosemary
  (awaiting your check) and Dos Rios (unclear). Tin Roof Bakery is complete:
  Hunter found the email Apollo lacked.
- Apollo $0.397, Hunter $0.049, research agent $1.80.
- Morning Crumb's contact is its owner, found by Hunter, so Qualify can
  qualify it. Fig & Feather's contact is still a shift lead.

## Going live

Set `APOLLO_API_KEY`, `HUNTER_API_KEY`, or both. Each key switches only its
own provider; a blank one leaves that provider on practice data. The live
classes are `HttpApolloProvider` and `HttpHunterProvider` in
`pipeline/providers/enrichment.py`. Nothing else in Enrich changes.

The research agent is not run live from this repo. Run it in Repository 2 and
save its answers in the shape of `fixtures/research_agent_answers.json`. Then
check them here before they enter the record.

Next: [Stage 3 — Qualify](3_qualify.md).
