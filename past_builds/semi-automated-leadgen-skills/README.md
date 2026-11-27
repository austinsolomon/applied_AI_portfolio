# Semi-automated leadgen skills

A four-stage lead-generation pipeline (source → enrich → qualify → outreach) built as plain Python
plus Claude Code skills, with a human checking the uncertain cases. It runs end to end offline with
mocked providers, and each stage can be switched to a real API one at a time. Built as Repository 1
of a course I wrote (Automation Academy, paired with the browser game *Lead Gen: The Game*); the
[research agent](../research-agent-four-frameworks/) is Repository 2 and fills one enrichment field.

## What it does

The example market is independent bakeries. The pipeline finds them on Google Maps, enriches each
with an owner contact and whether they already take online orders, qualifies them against four
rules, and drafts and sends outreach, holding any message that fails a deliverability check.

```
   source ──leads.json──▶ enrich ──enriched.json──▶ qualify ──qualified.json──▶ outreach
   Apify actor           Apollo: owner, role,       four rules,               Claude drafts
   (Google Maps)           email, locations         no outside calls          + sequencer
                         Hunter: gaps Apollo left
                         research agent:
                           ordering platform
                         (a person confirms)
```

| Stage | Provider | What it decides |
|---|---|---|
| Source | Apify Google Maps actor | Raw bakery leads from Maps rows |
| Enrich | Apollo, then Hunter for gaps, then the research agent for ordering platform | Owner, role, email, locations, ordering platform; cost per lead |
| Qualify | Plain Python, four rules | Qualified, rejected, or needs review |
| Outreach | Claude for copy, an email sequencer for sending | Drafts, deliverability lint, sends, replies, meetings |

Stages hand off JSON files with fixed, versioned schemas, so each stage can be run, inspected, and
replaced on its own.

## Offline run

```bash
python3 -m pipeline.run_pipeline --offline
```

No install, key, or spend. It prints every stage and the full funnel:

```
provider apify      MOCK   (set APIFY_TOKEN for live)
practice source: reading saved Google Maps rows; no external request
received 23 raw bakery leads from 25 Google Maps rows
provider apollo     MOCK   (set APOLLO_API_KEY for live)
provider hunter     MOCK   (set HUNTER_API_KEY for live)
provider research   RECORDED (Repository 2's agent, research_agent_answers.json)
enriching 23 leads: apollo(mock) then hunter(mock) for contacts, research_agent(recorded) for ordering_platform where they have no answer
apollo answered ordering_platform for 3/23 | research agent asked about 20: 18 confirmed, 1 awaiting your check, 1 unclear
cost $2.2460 total, $0.0977/lead | 21/23 complete
qualified 9 | rejected 12 | needs_review 2 (1 would qualify once you confirm the agent's answer)
provider claude     MOCK   (set ANTHROPIC_API_KEY for live)
provider sender     MOCK   (set EMAIL_SENDER_API_KEY for live)
HELD 1/9 messages that failed the deliverability lint — fix the copy, do not override the lint
allocation: {'email': 8}
sent 8 | delivered 5 | replied 2 | held 1

PIPELINE  HAULED 25 → LEADS 23 → ENRICHED 23 (COMPLETE 21) → QUALIFIED 9 (REJECTED 12, NEEDS REVIEW 2) → SENT 8 → DELIVERED 5 (UNDELIVERED 3) → REPLIES 2 → MEETINGS 1 (Opportunities to pitch; no sale recorded)
enrichment cost $2.2460 ($0.0977/lead)
needs_review: Rye & Rosemary Bakery — ordering_platform:unknown:awaiting_check
needs_review: Dos Rios Panaderia — ordering_platform:unknown:unclear
next: check the agent's answer for Rye & Rosemary Bakery: python3 -m pipeline.review_research --lead "Rye & Rosemary Bakery"
mode: PRACTICE (saved data — nothing was sent)
handoff files in out/
```

The two "needs review" leads are where a person steps in: one is waiting for someone to confirm
the research agent's answer, the other stays unclear. Confirming the first makes it the tenth
qualified lead, without sending another email.

## Skills, MCP, and humans in the loop

- **Claude Code skills:** one per stage (source, enrich, qualify, outreach), in `skills/` and as
  project skills in `.claude/skills/`, so Claude Code can run the pipeline from a plain request
  like "enrich these leads".
- **MCP:** the outreach tools (checked email, meeting actions) are exposed through a local MCP
  server (`pipeline/outreach_mcp.py`).
- **Human review:** `pipeline.review_research` and `pipeline.review_contact` show the evidence and
  record who confirmed it; nothing uncertain is promoted automatically.
- **Deliverability lint:** messages that fail it are held, not overridden.

## Going live

Every provider starts mocked. Setting one key (`APIFY_TOKEN`, `APOLLO_API_KEY`, `HUNTER_API_KEY`,
`ANTHROPIC_API_KEY`, `EMAIL_SENDER_API_KEY`) switches only that stage to the real API, so you can pay
for one stage while the rest stay mocked. See `.env.example`.

## Layout

```
pipeline/   the stages, providers, review commands, MCP server, and schema
skills/     Claude Code skills, one per stage
fixtures/   saved provider responses for the offline run
captures/   a recorded real Apify run and its recording guide
docs/       one walkthrough per stage (1_source.md onward)
tests/      pytest suite for the stages and handoffs
```

Requires Python 3.11+. Run the tests in a virtual environment with `pytest`.

Licensed for personal use only (see `LICENSE`).
