# Phoenix live sample — 2026-10-11

> These runs used the first demo category, mobile and in-home massage; the numbers below are kept
> as recorded. The category then became independent private chefs on 2026-11-05; that day's chef
> runs (plans 7 and 9) are history under that previous category and are not reported here. The
> demo category is now independent CFP® financial planners.

One approved plan targeted 50 mobile-massage businesses in Phoenix, Arizona, using one city and
one keyword. It finished with **12 qualified, 14 rejected and 24 review decisions**. Ten qualified
businesses were inserted into the CRM; two lacked a usable phone or email and were skipped.
There were no CRM conflicts. Human review is still needed for the 24 unresolved decisions.

The total approved budget was 5 USD, with a 1 USD Maps ceiling and a separate 0.75 USD model
allowance. The final ledger total is **1.385297 USD**, including **0.374400 USD in conservative
web estimates**. Anthropic usage was **0.097097 USD** on `claude-haiku-5-5`.

## Results

| Step | Result |
|---|---|
| Google Maps | 50 persisted businesses from one Apify campaign |
| Exa | 0: no eligible real-domain seeds in the initial demo CRM; no paid Exa calls |
| Dedupe | 50 new, 0 duplicates |
| Contact enrichment | 50 checked; existing contacts avoided all but one domain lookup |
| First-tier qualification | 50 decisions: 9 qualified, 9 rejected, 32 review |
| Bounded research | All 32 review cases researched; final decisions 12 / 14 / 24 |
| CRM | 10 inserted, 2 without usable contacts, 0 conflicts |

The run retained the same ID through recovery: `b1d352b5-2159-4153-b522-1f647cfbf5fc`.
Stage checkpoint counters report work in the successful resumed invocation (48 judged and
24 researched); the table above counts all persisted decisions across the entire run.

## Accounting

| Service | Ledger USD | Basis |
|---|---:|---|
| Apify | 0.200200 | Settled actor usage and priced events: 50 place events plus four start-memory units |
| Anthropic | 0.097097 | Response token usage and SDK final session costs |
| Firecrawl | 1.088000 | 223 accounted credits at 0.0032 USD, plus 117 uncertain attempts at their full reserved ceiling |
| BlitzAPI | 0.000000 | One domain lookup; zero billed records in the response |
| Exa | 0.000000 | No eligible seeds, no calls |
| **Total** | **1.385297** | Includes **0.374400 estimated**, not an exact invoice |

Model costs break down into structured judging (0.046436 USD), bounded research sessions
(0.048136 USD), and orchestration including recovery (0.002525 USD). Web research accounted for
1.088000 USD, contact enrichment for zero, and sourcing for 0.200200 USD. Dedupe and CRM writes
made no provider calls.

Apify's `usageTotalUsd` initially lagged event counters after abort. The implementation now takes
the larger of reported usage and priced event counts. The ledger includes the settled delta.
Firecrawl timeouts, free fallbacks after paid failures, and unsupported-site errors lacked final
billing information; their full ceilings are marked estimated. Credit conversion also depends on
the configured subscription rate. **Exact Firecrawl invoice reconciliation remains open**; this
sample does not claim that every provider's invoice matches the ledger within rounding.

## Recoveries and replay

This was one campaign resumed from durable checkpoints, with no second sourcing campaign:

1. The initial 0.25 USD sample ceiling was below Apify's 0.50 USD minimum. A read-only provider
   check confirmed no actor run was created. The rejected reservation was reconciled and the
   sample ceiling corrected.
2. A judge response hit its output-token limit. Already-reported usage was retained and the
   incomplete decision restored as review without repeating that request. Incomplete outputs
   now become review automatically, and the output allowance is 1,400 tokens.
3. A research session reached its four-turn limit. The SDK reported final usage before raising
   an iterator error. Its saved evidence and usage were recovered without repeating research.
   Bounded stops now close the iterator after its final result and proceed to re-judging.
4. Unsupported-site responses were retained as page failures, with conservative estimated
   charges. The implementation now does this automatically. All started/uncertain reservations
   were reconciled; none remained at the end.

A completed-plan rerun returned `already_finished: true`; ledger count and sum remained exactly
**368 rows and 1.385297 USD** before and after. It made no provider calls.

The committed `tests/fixtures/phoenix_live_decisions.json` contains three redacted observed
judgments, with only their short cited fragments. Examples: A Relaxing Touch Massage qualified,
Total Medical & Wellness rejected, and Get Better Faster Injury & Sports recovery remained review.
These are machine observations, not human-labelled quality benchmarks. Full source records,
contacts and pages remain in Postgres; no business phone numbers or emails are in the fixtures.

## Hardening acceptance evidence

| Requirement | Evidence |
|---|---|
| Block outside-plan and over-budget calls | Real-Postgres reservation and hook tests; concurrent reservations cannot both spend the last budget |
| Same-plan rerun performs no paid work | Live completed-plan rerun above and SDK replay test |
| Aborted scrape persists, repeated persistence adds nothing | Apify fake abort/persist test plus the live aborted campaign's 50 saved rows |
| Workers claim once; retry then stop | Real-Postgres queue concurrency, attempt-limit and stale-worker ownership tests |
| Seven-day page cache avoids repeat fetch | Fake-provider cache hit/miss tests |
| Reformatted phone never creates a duplicate | Shared-schema CRM dedupe and transactional conflict tests |
| CI cannot spend | No provider keys; fake/replay providers; external socket guard |
| Provider dollar reconciliation | Apify settled usage reconciled; model usage captured; Firecrawl estimates remain unresolved against an invoice |
| Key/credit failures give plain messages | Mocked invalid-key, empty-balance and CLI failure tests |

Cloud deployment, scheduling and secret management were out of scope for this sample (they are now deployed; see [operations](operations.md#production-deployment)). The Exa network path was
covered by fakes but was not exercised by this live sample; see the Exa live run below. This run validates the pipeline and
recovery mechanisms; it is not a claim of unattended cloud production readiness.

## Exa live run — 2026-11-04

A second approved plan targeted Phoenix again, so Exa could use the ten real qualified leads from
the first run as seeds: `leadgen plan --market "Phoenix, AZ" --target 30 --max-cities 1
--max-keywords 1 --budget-usd 3`. Plan 2, run `f2423f88-f23e-4b4e-90c8-4342201d4d0c`. Real Apify,
Exa, Firecrawl and Anthropic calls; no demo data and no fakes.

Exa had three roles in this run: lookalike sourcing (`find_similar`), and, once the evidence
backend was switched to Exa during recovery, search with page text and page contents for
qualification.

### Sourcing

| Step | Result |
|---|---|
| Eligible seeds (`select_seeds`) | 6 of 10 qualified leads; 4 excluded (one Instagram page, three names or domains without a category keyword) |
| Exa mode | `find_similar`, 10 results per seed, `contents=False` |
| Exa candidates kept | 37 own-domain, on-category businesses (raw result counts per call were not recorded) |
| Google Maps | 18 businesses |
| Dedupe | 52 new, 3 duplicates (all Maps); no Exa lead was already in the CRM |
| Seed consensus | 30 leads found by 1 seed, 4 by 2 seeds, 3 by 3 seeds |

### Qualification

| | Maps | Exa |
|---|---:|---:|
| Judged | 15 of 15 | 35 of 37 |
| Qualified / rejected / review | 2 / 3 / 10 | 11 / 4 / 20 |
| Qualified ÷ judged | 13% | 31% |

| Exa seed consensus | Judged | Qualified | Rate |
|---|---:|---:|---:|
| 2 or 3 seeds | 7 | 3 | 43% |
| 1 seed | 28 | 8 | 29% |

Consensus ordering did its job: every lead found by two or more seeds was judged before the first
failure stopped qualification. The two unjudged Exa leads were both consensus 1. The samples
are small; the consensus split is a signal to keep measuring, not a result.

### Cost

| Service | Ledger USD | Notes |
|---|---:|---|
| Exa | 0.111000 | 6 `find_similar` at 0.004; 12 searches with text at 0.007; 27 page-contents calls, 0.003 total |
| Firecrawl | 0.608000 | 0.211200 provider-reported; 0.396800 conservative estimates from the first invocation's failed fallbacks |
| Anthropic | 0.031350 | `claude-haiku-5-5` judging and orchestration |
| Apify | 0.000200 | Understated: usage had not settled when recorded |
| BlitzAPI | 0.000000 | |
| **Total** | **0.750550** | Approved budget 3 USD |

Exa sourcing cost 0.024 USD for 11 qualified leads: **0.0022 USD per qualified lead**. The whole
run, all services, cost 0.058 USD per qualified lead (13 qualified). The code reserves 0.02 USD
per Exa call; Exa's reported prices were 5× (find_similar) to 200× (contents) below that.

### What stopped the run, and what changed

1. **First invocation.** Qualification used Firecrawl for evidence. Firecrawl `/v2/search`
   failed in about 300 ms for 14 leads (rate limited), and the free DuckDuckGo fallback returned
   HTTP 202. Firecrawl's usage history showed fewer credits than were already ledgered, so the 14
   reservations were reconciled as not billed.
2. **Code changes before resuming.** Exa became the evidence backend: search with page text, and
   `get_contents` for pages. Firecrawl is kept only for pages Exa has no text for. Failed qualify
   jobs are now retried on resume, with their own reservation keys.
3. **Second invocation.** 12 of the 14 leads were judged on Exa evidence. Two failed: Exa still
   indexes two dead domains, and Firecrawl's DNS-failure response surfaced as a generic error
   instead of an unreadable page. 16 Firecrawl fallback scrapes ended `uncertain` and need
   reconciliation before the research tier and CRM write can resume.

### Open items from this run

- Exa returned no text for 24 of 27 pages. Qualification guesses subpages (`/services`,
  `/about`) that often do not exist, and `max_age_hours=168` forces live recrawls that small sites
  fail. Using Exa search hits and highlights as evidence should replace most page fetches.
- Firecrawl `success:false` on a scrape should be an unreadable page (`PageFetchFailed`), not a
  job failure.
- The host lists need booking platforms, directories and registries that came through as
  businesses: `glossgenius.com`, `qdexx.com`, `excellenttherapists.com`, `carenary.com`,
  `stedi.com`, `linkedin.com`.
- Raw Exa result counts per call are not persisted, so "returned vs filtered" cannot be reported.
- Done after this run: `exa-py` 2.25 deprecates `find_similar`, so lookalike sourcing now calls its
  documented replacement, `search("pages similar to <seed>", exclude_domains=[seed])`. The
  numbers above were produced by `find_similar`.
- Research tier and CRM write: pending reconciliation of the 16 uncertain reservations.

The redacted fixtures in `tests/fixtures/exa/` (`similar.json`, recorded with `find_similar`
before the migration, and `search.json`, recorded the same day) keep only URL, title, domain and reported cost. The replay tests run them through the real
sourcing code.
