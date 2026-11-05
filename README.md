# Applied AI portfolio

Production-style AI systems for go-to-market work, built on Claude. Each project is a self-contained folder with
its own README, design doc, tests and dependencies.

| Project | What it is | Status |
|---|---|---|
| [Lead-gen agent](p1_leadgen_pipeline_hardened_agent/) | A Claude Agent SDK agent that runs a lead-generation pipeline end to end: reads demand gaps, plans the scrapes, gets one human approval, then sources, dedupes, enriches and qualifies leads unattended within a budget. **Exa** finds lookalike businesses from qualified leads, searches markets with no seeds, and supplies the page text Claude must quote to qualify a lead | in progress |

### Where Exa fits in the lead-gen agent

| Exa role | What it does | Live result (Phoenix, 2026-11-04) |
|---|---|---|
| Business sourcing | Plain-language Exa company searches per city (`type="auto"`, `category="company"`), directories excluded; a business found by several phrasings ranks first | Earlier seed-lookalike version: 37 new businesses; 31% of those judged qualified, vs 13% from Google Maps in the same run. The plain-language version has not run live yet |
| Qualification evidence | `search` with page text and `get_contents` for pages; Claude returns schema-checked JSON and must quote this text word for word | Exa cost 0.111 USD for the run; Firecrawl kept only to render JavaScript pages |

Details: [Why Exa](p1_leadgen_pipeline_hardened_agent/README.md#why-exa) and the
[Exa live run](p1_leadgen_pipeline_hardened_agent/docs/sample_run.md#exa-live-run--2026-11-04).
