# Stage 1 — Source · from the harbour to an Apify actor

| | |
|---|---|
| **Game level** | `apify_trawler` — the harbour, three trawlers, a quay |
| **Real tool** | [Apify](https://apify.com) actor run |
| **Skill** | `skills/source_leads/SKILL.md` |
| **Script** | `pipeline/source_bakeries.py` |
| **Artifacts** | `out/raw_bakery_leads.csv` + `out/leads.json`, key `claude3d.leads.v1` |

## What the level taught you

You fire a shot at a dock post. That post's own trawler steams out to its patch
of sea, works there with the net down for a while, comes back **low in the
water**, and dumps a haul on the quay. Then you walk over and sort the bottles
from the garbage on foot.

The level's own teaching diagram:

```
gun shot   = an API request
dock post  = an endpoint (/crawl /scrape /dataset)
trawler    = a web scraper working its zone
the haul   = raw scraped records, dumped in a pile
bottles    = the few records worth keeping
garbage    = everything else a crawl drags up
```

## What that is, in the real tool

The boat is the async run, and the reason it is a boat rather than a vending
machine is the single most important thing in this stage:

```
OUT    POST /v2/acts/{actor}/runs           → 201 {data:{id, status:"READY"}}
WORK   GET  /v2/actor-runs/{runId}          → poll until status is terminal
BACK   status == "SUCCEEDED"                → defaultDatasetId now populated
LAND   GET  /v2/datasets/{dsId}/items       → the records
```

The POST returns **nothing useful** — a run id, not leads. Code that expects leads
from the POST is standing on the quay waiting for a boat that has not left yet.

`pipeline/providers/apify.py` keeps that shape even in the **mock**: the mock
really does hand out a run id, really does report `RUNNING` for two polls, and
only then produces items. A mock that returned rows in one hop would let you
write code that breaks the moment a token is pasted in.

## The three things that bite

1. **Terminal ≠ SUCCEEDED.** `FAILED`, `ABORTED`, and `TIMED-OUT` are terminal
   too. `while status != "SUCCEEDED"` spins forever on them.
2. **A listing can come back half empty.** A Google Maps row with no phone or
   no website is still a row. On the 25 listings, five have no phone and seven
   have no website. A blank means "not found", never "has none".
3. **Billing starts at the POST.** Killing your Python process does not abort the
   actor. Abort it in the console.

## What the run produces

The game shows Claude run:

```bash
python3 -m pipeline.source_bakeries \
  --query bakeries --location "Chicago, Illinois" --limit 25 \
  --out out/raw_bakery_leads.csv --handoff out/leads.json
```

With no `APIFY_TOKEN` set it reads `fixtures/apify_bakeries_25.json` and prints:

```
received 23 raw bakery leads from 25 Google Maps rows
✅ 23 raw bakery leads → out/raw_bakery_leads.csv
✅ pipeline handoff → out/leads.json · REHEARSAL FIXTURE
```

A raw lead is a business listing. This is the first one:

```json
{"id": "apify-maps-72a168d9e318", "name": "Sunrise Bakery", "category": "Bakery",
 "address": "10 Example Street, Chicago, IL 60614", "city": "Chicago",
 "state": "Illinois", "postal_code": "60614", "phone": "+1 312-555-0101",
 "website": "https://sunrise.example",
 "google_maps_url": "https://maps.example/place/fixture-place-001",
 "rating": 4.7, "review_count": 212,
 "source": {"level": "apify_trawler", "provider": "apify(mock)",
            "actor": "compass/crawler-google-places",
            "place_id": "fixture-place-001", "collectedAt": "2026-…Z"}}
```

The `id` is `apify-maps-` plus the first twelve characters of a hash of the
Google Maps place id, so the same place always gets the same id.

It does **not** carry an owner, a role, an email, the number of shops, how the
bakery takes orders, a qualification verdict, or outreach copy. Finding those is
Enrich's job, so `schema.is_raw_lead()` rejects a record that already carries
any of the five enrichment fields: the four contact fields Apollo, then Hunter,
look up (`owner_name`, `owner_role`, `email`, `location_count`) and
`ordering_platform`, which Apollo or the research agent fills.

The same 23 records are written to CSV for reading and to JSON for the next
stage.

## Twenty-five rows are twenty-three bakeries

Google Maps repeats itself. The fixture has both kinds of repeat on purpose:

- **Row 7 is row 1 again.** Same place id, so the same lead id. Dropped.
- **Row 16 is row 5 under a longer name.** "Morning Crumb Bakery & Cafe" has a new
  place id, and the same phone number and street address as "Morning Crumb".
  Dropped.

The rule lives in `schema.dedupe_listings`: drop a row when its id was already
seen, or when its phone **and** street address both match an earlier row. The
first listing wins. A blank phone or a blank address never matches anything.

Without the second half of that rule the run reports 24 leads, and one bakery is
enriched, qualified and written to twice.

## Try this

```bash
python3 -m pipeline.source_bakeries
python3 -c "import json;d=json.load(open('out/leads.json'));print(d['counts'])"
```

It prints `{'dataset_items': 25, 'leads': 23}`. Open
`fixtures/apify_bakeries_25.json`, find the two rows that were dropped, and say
which half of the rule caught each one.
