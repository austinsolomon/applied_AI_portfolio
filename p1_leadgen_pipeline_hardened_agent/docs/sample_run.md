# Phoenix live sample — 2026-10-11

One approved plan targets 50 mobile-massage businesses in Phoenix, Arizona. The total approved
budget is 5 USD, with a 1 USD Maps ceiling and a separate 0.75 USD model allowance. The same run
is continuing from saved checkpoints; it has not launched a second sourcing campaign.

Maps sourced 50 businesses. The first qualification pass produced 9 qualified, 9 rejected and
32 review decisions. The bounded research pass is in progress. Final counts and reconciliation
will be recorded when it finishes.

Two recoveries were needed: the initial 0.25 USD sample ceiling was below Apify's 0.50 USD minimum,
so the rejected request created no actor run; one judge response then hit its output-token limit,
so the response was retained as review without repeating that judge request. Both cases now have
regression coverage. The runner retains uncertain reservations until provider reconciliation.

Apify's settled charge was 0.2002 USD for 50 place events and four actor-start memory units. Its
usageTotalUsd initially lagged the event counters after abort; the implementation now takes the
larger of reported usage and priced event counts. Firecrawl timeouts are accounted for at their
full reserved ceiling and explicitly marked estimated when no final usage is available. These
estimates must not be presented as an exact provider invoice.

Exa found no eligible real-domain seeds in the initial demo CRM and made no search calls. Existing
contacts avoided most Blitz lookups; its one domain lookup used the configured subscription rate.
Full source records, contacts and fetched pages remain in the database. This report contains no
business phone numbers or email addresses.
