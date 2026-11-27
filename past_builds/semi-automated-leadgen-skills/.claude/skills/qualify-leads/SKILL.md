---
name: qualify-leads
description: Apply four local fit rules to enriched bakery records; forward only qualified leads, preserve rejected and review-needed records, and explain each saved decision. Use for qualification and filtering; not enrichment or sending.
---

## Course mode and paths

Run commands from the repository root (the folder containing `pipeline/`).
Use `python3` on macOS/Linux or `py` on Windows. All practice commands must include
`--offline`; this overrides locally saved service keys. Do not start a live call
or send real messages unless the learner explicitly requests it separately.
A missing input file means run the earlier stage offline first, then inspect its
output. Report facts from the output file; never invent owner/contact data.

# Stage 3 — Qualify

Source → Enrich → **Qualify** → Outreach.

Qualify checks whether each bakery fits the offer: a $500 website that accepts online orders. It reads `out/enriched.json`, applies rules locally, and writes `out/qualified.json`. It makes no outside calls.

| Field | Pass rule |
|---|---|
| `review_count` | 20–1,000, including both limits |
| `location_count` | 1–3, including both limits |
| `owner_role` | Manager, General Manager, Co-owner or Owner |
| `ordering_platform` | `none`, confirmed as a fact; a blank value does not mean none |

An ideal customer profile (ICP) is this written set of fit rules. Every rule must pass. Missing or unrecognized required facts go to `review_records`; return to Enrich to resolve them. Qualification never invents an answer.

In the baseball game, read all four facts before choosing Qualified, Rejected or Needs review. The field maps contact role horizontally and review count vertically; the frame shows only two of the four checks. A wrong choice gets a correction on the same bakery. After the saved decision, you can swing at the pitch for an optional batting score. Hitting or missing never changes qualification. Practise nine unique bakeries, then apply the same rules to the full saved list.

```python
reasons = ICP().check(lead)
if not reasons:
    qualified.append(lead)
```

This uses Phase 3 records and conditions: read fields, evaluate rules, then select the correct output. The production function also separates unknowns and keeps named reasons.

Run locally:

```sh
python3 -m pipeline.qualify --offline --in out/enriched.json --out out/qualified.json
```

The fixed rehearsal yields 9 qualified, 12 rejected and 2 needing review. One review record, Rye & Rosemary, would qualify after its research answer is checked. Dos Rios remains unresolved.

- `records`: only the 9 qualified leads; this is Outreach's input.
- `rejected_records`: all 12 rejected leads and their reasons.
- `review_records`: the 2 unresolved leads, with the missing fields named.

Older `chased_records` and `taken_records` fields divide rejected records for compatibility. They are not the player's batting score. `--no-chase` combines those rejections under `taken_records`; neither mode forwards them. Changing the rules changes eligibility: `--location-max 5` makes Butter & Thyme's four locations pass. This deliberate rule change differs from ignoring a failed rule.

Takeaway: a decision audit keeps every outcome, while the Outreach handoff contains only qualified records.
