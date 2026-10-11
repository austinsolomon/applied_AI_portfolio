# Phoenix live sample — 2026-10-11

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

Cloud deployment, scheduling and secret management remain out of scope. The Exa network path was
covered by fakes but was not exercised by this live sample. This run validates the pipeline and
recovery mechanisms; it is not a claim of unattended cloud production readiness.
