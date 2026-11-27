---
name: source-leads
description: Pull raw business leads out of the open internet by running an Apify actor start-to-finish (POST run → poll until terminal → GET dataset items) and write them to CSV plus the pipeline handoff. Use whenever someone wants to find or scrape prospects, build a list from Google Maps or another directory, says "get me leads", "run the trawler", or when a later pipeline stage has no leads.json to read. Do NOT enrich, qualify, or contact the results — those are separate stages.
---

## Course mode and paths

Run commands from the repository root (the folder containing `pipeline/`).
Use `python3` on macOS/Linux or `py` on Windows. All practice commands must include
`--offline`; this overrides locally saved service keys. Do not start a live call
or send real messages unless the learner explicitly requests it separately.
A missing input file means run the earlier stage offline first, then inspect its
output. Report facts from the output file; never invent owner/contact data.

# Stage 1 — Source

**Game level:** `apify_trawler`, the harbour. **Real tool:** an Apify actor run.

## What this stage is for

Getting a list of potential businesses out of the open web — in this
repository, 25 Chicago bakery listings from Google Maps. Nothing more. A
Google Maps result is a **business listing**, not a verified decision maker. It
may contain a name, address, phone, website, rating, and category; it does not
prove who owns the business or who should be contacted. Stage 2 earns those
contact fields. Do not "helpfully" invent a person here.

## The one thing that goes wrong

**A scrape is asynchronous.** `POST /v2/acts/{actor}/runs` returns a run id and
nothing else — no leads. Code that expects leads from the POST is standing on the
quay waiting for a boat that has not left yet. The four beats are:

| Beat | Call | What you get |
|---|---|---|
| OUT | `POST /v2/acts/{actor}/runs` | a run id, status `READY` |
| WORK | `GET /v2/actor-runs/{id}` (repeat) | status, until it is terminal |
| BACK | …status `SUCCEEDED` | `defaultDatasetId` is now populated |
| LAND | `GET /v2/datasets/{id}/items` | the records |

`SUCCEEDED` is not the only terminal state. `FAILED`, `ABORTED`, and `TIMED-OUT`
are terminal too, and a poll loop written as `while status != "SUCCEEDED"` spins
forever on them. `pipeline/providers/apify.py` has the correct set.

## Run it

The Real Run shown in the game uses the following command and the exact script
at `pipeline/source_bakeries.py`. It is a live, paid run: it needs `APIFY_TOKEN`
and spends Apify credit, so run it only when the learner explicitly asks for a
live run.

```bash
# Live: needs APIFY_TOKEN (in the environment or in .env) and spends Apify credit.
# No --offline here: that flag forces the saved practice listings.
python3 -m pipeline.source_bakeries \
  --query bakeries --location "Chicago, Illinois" --limit 25 \
  --out out/raw_bakery_leads.csv --handoff out/leads.json
```

That script calls Apify's Google Maps actor, waits for it to finish, drops
repeated listings, writes the raw listings to CSV, and writes the same records
to the versioned JSON handoff used by the next stage.

**Fixture mode** is the practice run. With `--offline`, it reads the 25
bundled bakery listings in `fixtures/apify_bakeries_25.json` even if a token is
saved, makes no network call, and costs nothing:

```bash
python3 -m pipeline.source_bakeries --offline
# ✅ 23 raw bakery leads → out/raw_bakery_leads.csv
# ✅ pipeline handoff → out/leads.json · REHEARSAL FIXTURE
```

25 rows go in and 23 leads come out, because two rows are duplicates (see
"Duplicates" below). Report both numbers; never round the 23 back up to 25.

## Flags

| Flag | Meaning |
|---|---|
| `--query` | What to search Google Maps for. Default `bakeries`. |
| `--location` | Where to search. Default `Chicago, Illinois`. |
| `--limit` | Max places the actor crawls, and max leads kept. Default `25`. |
| `--actor` | Apify actor id. Default `compass/crawler-google-places`. |
| `--out` | CSV for a human to open. Default `out/raw_bakery_leads.csv`. |
| `--handoff` | JSON the next stage reads. Default `out/leads.json`. |
| `--fixture` | Listings the mock actor returns. Ignored when a token is set, unless `--offline` is given. |
| `--offline` | Use the saved practice listings even if `APIFY_TOKEN` is set. Every practice run includes it. |

## Duplicates

Google Maps repeats itself, so the stage drops a row when:

1. its place id was already seen (the identical place returned twice), or
2. its phone number **and** street address both match an earlier row (one shop
   listed again under a longer name).

The first listing wins. A blank phone or address never matches anything. In the
bundled fixture that removes the second Sunrise Bakery row and the "Morning
Crumb Bakery & Cafe" row. The rule lives in `schema.dedupe_listings`.

## Output

- `out/raw_bakery_leads.csv` for a human to inspect.
- `out/leads.json` for the enrichment stage, keyed `claude3d.leads.v1` — the
  same key the game writes to `localStorage`.

The raw fields are `id`, `name`, `category`, `address`, `city`, `state`,
`postal_code`, `phone`, `website`, `google_maps_url`, `rating`, `review_count`,
and `source` provenance. There is deliberately no invented owner, role, email,
location count, ordering platform, or qualification verdict.

```json
{"id": "apify-maps-72a168d9e318", "name": "Sunrise Bakery",
 "category": "Bakery", "address": "10 Example Street, Chicago, IL 60614",
 "city": "Chicago", "state": "Illinois", "postal_code": "60614",
 "phone": "+1 312-555-0101", "website": "https://sunrise.example",
 "google_maps_url": "https://maps.example/place/fixture-place-001",
 "rating": 4.7, "review_count": 212,
 "source": {"level": "apify_trawler", "provider": "apify(mock)",
            "actor": "compass/crawler-google-places",
            "place_id": "fixture-place-001", "collectedAt": "…Z"}}
```

A blank `phone` or `website` means "not found in this result". It is not proof
the bakery has none, so leave it blank.

## Cost and safety

An actor run is **billed from the moment it starts**, whether or not you read the
dataset — and killing your Python process does not abort the actor. If a run
overruns the poll timeout, this stage raises rather than silently aborting: stopping
a paid run is your call, not the script's. Abort it in the Apify console.

## Checks before moving on

- Every record passes `schema.is_raw_lead` — no `owner_name`/`email` leaked in.
- Rows with no business name were dropped; blank phones and websites were kept blank.
- Ids are unique. Stage 2 merges by id and a collision silently loses a lead.
- The lead count is lower than the row count by exactly the duplicates dropped.

Next: **enrich_leads**.
