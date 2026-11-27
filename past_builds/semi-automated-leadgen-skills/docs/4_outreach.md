# Stage 4 — Outreach

Source → Enrich → Qualify → **Outreach** → confirmed meetings.

Outreach contacts qualified bakeries, handles replies and records confirmed meetings. A delivery is not a reply, a reply is not a booking, and a meeting is not a sale. The course rehearsal uses email only.

The script reads `out/qualified.json` and writes `out/outreach.json`. Before writing any copy it verifies all four qualification rules again. Rejected, unknown or explicitly unqualified entries are held and never sent.

1. Prepare a short message using the bakery's actual facts and the $500 website offer.
2. Check the draft with `lint`: a small program that names message problems.
3. Hold failed drafts. For passing drafts, check the email address before sending.
4. Save distinct delivery and reply receipts.
5. Record a meeting only from a separate confirmation naming the bakery and time.

```python
for entry in records:
    lead = entry.get("lead", entry)
    reasons = ICP().check(lead)
    if reasons:
        continue
    message = copy_provider.write(lead, offer)
    problems = lint(message)
```

This reuses Phase 3 records, loops, conditions and ordered steps. The production code also rejects explicit unqualified flags, holds failed messages with reasons, and checks addresses.

The relay game represents the work with a message moving through an email delivery route. Failed delivery cannot produce a reply. After the rehearsal, inspect the interested reply, propose an available time and confirm the bakery's agreement. Only the completed confirmation enters the calendar.

Run the free local rehearsal with `--offline`, which ignores provider keys:

```sh
python3 -m pipeline.outreach --offline --in out/qualified.json --out out/outreach.json
```

| Outcome | Count |
|---|---:|
| Qualified inputs | 9 |
| Draft held by lint | 1 |
| Emails sent | 8 |
| Delivered | 5 |
| Bounced | 3 |
| Replied | 2 |
| Separately confirmed meeting | 1 |
| Customers | 0 |

Sunrise and Honeycomb reply. Sunrise has the saved scheduling confirmation; Honeycomb does not. Night Owl's draft is held. Riverbend, Flour & Hearth and Sweet Thistle bounce. These are fictional receipts, not predictions about real businesses.

Lint checks capitals, links, spam words, exclamation marks and body length. It returns a list of problems; an empty list means the draft passed these checks, not that delivery or a reply is guaranteed. The prompt asks for under 90 words; the lint ceiling is 120 words.

The output retains `records` (receipts), `sent_messages`, `held_records`, `meetings`, separate counts and `sequence`. Follow-up entries are only plans with status `planned`; this script does not execute them. Optional channel templates and the allocator remain available for later experiments, outside this email lesson.

The offline sender uses saved fixtures and contacts nobody. Live sending is intentionally unimplemented. The offline meeting fixture is loaded only for a delivered, replied record and never enters a live result. In the game a player must complete the confirmation task before receiving meeting credit.

Takeaway: finish at a confirmed meeting and preserve the evidence for every preceding step.


## Connect this stage through MCP

After the Python rehearsal, follow `docs/5_tools_mcp.md` for the local tool
connection. The four `course-outreach` tools reuse the same qualification, draft
and meeting checks. The server and `python3 -m pipeline.mcp_demo` always use
saved local data; they do not need an `--offline` flag and never activate a live
provider from keys. Claude itself still uses the learner's own account.

When the learner requests this exercise, find the bakery's existing ID in
`out/qualified.json`. Call `prepare_checked_email`; inspect its actual returned
draft and recipient. Only then call `send_checked_email` with that draft ID and
recipient. Acceptance alone leaves delivery and reply unknown. Call
`read_outreach_results`, and show the separate scheduling evidence before
`record_confirmed_meeting`. Hold missing or contradictory information. Never
invent an ID, address, reply, agreed time or confirmation.

Keep the MCP rehearsal separate from `out/outreach.json`. Repeated tool requests
return the existing receipt; do not count them as extra messages. In the next
repository the same request/result pattern connects the research agent to its
page-reading tool inside Enrich.
