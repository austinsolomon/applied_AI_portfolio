# Lead-gen agent

A Claude Agent SDK agent that does the job of a lead-generation operator. It reads a demand dashboard, finds the
markets furthest below their lead goal, plans the scrapes, and asks a human to approve the plan and a budget once.
From there it runs unattended: sources businesses from Google Maps and Exa, drops leads already in the CRM, enriches
contact details, qualifies each lead against a category rubric with cited evidence, and writes qualified leads to
the CRM. Every decision is recorded for a recurring evaluation review.

The demo category is mobile and in-home massage providers in US markets.

```mermaid
flowchart LR
    D[Demand dashboard] --> G[Find gaps]
    G --> P[Plan scrapes]
    P --> A{Human approves<br/>plan + budget}
    A --> S[Sample, grade,<br/>full run: Google Maps]
    S --> X[Lookalikes: Exa]
    X --> DD[Dedupe vs CRM]
    S --> DD
    DD --> E[Enrich phone + email]
    E --> Q[Qualify with evidence]
    Q --> C[(CRM)]
    C --> D
```

## Status

| Milestone | State |
|---|---|
| Python package, pinned toolchain, lockfile | planned |
| Lint, tests, CI, container | planned |
| Postgres schema, demo data, dashboard | planned |
| Raw agent loop on the Claude API | planned |
| Sourcing, dedupe, enrichment, qualification, CRM write | planned |
| Orchestrator agent, approval, budget guard, evals | planned |
| Scheduled cloud deployment | planned |

## Design

The full design, including the trade-offs behind each choice, is in [docs/design.md](docs/design.md). In short:

- **Claude Agent SDK, not a hosted agent platform**, so the loop, approval, budget, state and scheduling are
  built and understood here, and cost stays fully controlled.
- **One human decision per run.** After approval, a pre-tool-use hook blocks any action outside the approved plan or
  budget instead of asking again.
- **One Postgres database** serves as CRM, plan store, run state, job queue, page cache, cost ledger and eval log.
- **Agents only where reasoning is needed.** Contact enrichment is a plain API call. Qualification is one
  structured-output model call over gathered evidence, and escalates to an agentic research loop only when the
  evidence is unclear.
