# Repo 01 — Lead Gen Skills Pipeline

The real machine from **Lead Gen: The Game** — one stage per level, as executable
Claude Code skills plus plain Python. You played the harbour, the dog pack, the
ballpark, and the delivery fleet. This is the same four stages against real APIs,
with the same record shapes, so the example, fields and stage handoffs match the course. The browser game and local files keep separate progress; there is no automatic save transfer.

```
apify_trawler          dog_enrichment               qualification_baseball   delivery_city
      │                       │                               │                    │
      ▼                       ▼                               ▼                    ▼
   source ──leads.json──▶ enrich ──enriched.json──────▶ qualify ──qualified.json──▶ outreach
   Apify actor          Apollo: owner, role,          four rules,          Claude + sequencer
   (Google Maps)          email, locations            no outside calls
                        Hunter: the gaps Apollo
                          left (owner, email)
                        research agent:
                          ordering_platform
                        you confirm its answer
```

## What a buyer should do first

The local practice run needs only Python. No provider API key is needed. You need
no terminal experience beyond copy-pasting the commands below in order.

1. **Check your Python version.** Open a terminal and run `python3 --version`. You
   need **3.11 or newer**. If you have an older one (or none), install a current
   Python from [python.org/downloads](https://www.python.org/downloads/) first —
   every command below assumes `python3` already points at 3.11+. Every script in
   this repo also checks this itself and prints a plain-English fix if it doesn't,
   so you can't get stuck silently.
2. **Get your copy and `cd` into this exact folder** (`leadgen-skills-pipeline`,
   not the folder above it). See "Getting your copy" below.
3. **Run the one command in "Run it right now."** It needs no install, no venv, and
   no key, and it should print the same funnel numbers shown in this README. If it
   does, the whole machine works on your machine.
4. **Only then** open `docs/1_source.md` and read the four stages in order, or ask
   Claude Code to "enrich these leads" and let the project skills in `.claude/skills/` walk you
   through it in English.
5. **Only add a real API key once you've read "Going live" below**, and only for
   the one stage you actually want to pay for. Every other stage stays mocked.

## Getting your copy

Download the ZIP from the course and unzip it, or use the personal copy command
on your course account page. Both give you a folder called
`leadgen-skills-pipeline`. You do not need a GitHub account.

### Getting updates

If you used the copy command, open your course account page, get a fresh command
for this repository and run the one under **Update my copy**, from the folder
that holds `leadgen-skills-pipeline`. It keeps your own changes. A plain
`git pull` will not work, because your copy carries no access token. If you
started from the ZIP, download the ZIP again.

## Run it right now — no accounts, no keys, no spend

```bash
cd leadgen-skills-pipeline
python3 -m pipeline.run_pipeline --offline
```

On Windows, type `py` where these commands say `python3`, for example
`py -m pipeline.run_pipeline --offline`. `py` is the Python launcher that the
python.org installer adds.

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

The transcript shows the standard practice run. The practice
providers look their answers up in fixture files and never roll dice, so your
run prints the same numbers on any machine. The lines after the blank line are
the summary. The lines above it are each stage reporting as it finishes.

The list is 25 made-up Google Maps listings for bakeries in Chicago, Illinois. The
seller is a one-person web studio that builds websites so a bakery can take
orders online, at $500 a site. One bakery to follow from end to end is Sunrise
Bakery: sourced as listing 1, enriched for $0.107, qualified, emailed, and one of
the two owners who reply.

`--offline` forces all practice providers, even if keys are saved on your computer. Without this flag, a provider key can select live mode. Nothing above touched the network and nothing was mailed to anyone. The
handoff files, the cost ledger, and the funnel report are the same shape a live
run produces — only the numbers become real when you paste a key in.

Every stage script checks your Python version before it does anything else. On
something older than 3.11 you get one plain sentence telling you to install a
newer Python and make a virtual environment with it — not a raw `SyntaxError`
buried three files deep.

### Running the tests (needs a virtual environment)

A modern macOS or Linux blocks a bare `pip install` outside a virtual environment
(`externally-managed-environment`, PEP 668). Make one first — it costs one extra
line and nothing breaks:

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install pytest
pytest                            # offline tests
```

**The test suite passes with zero keys and zero network access.** That is a hard
guarantee, enforced by a test that fails if anything opens a socket.

pytest reports one test as skipped. That test checks the optional MCP connection
exercise (`pipeline/outreach_mcp.py`, `pipeline/mcp_demo.py`), and it runs once
you install the exercise's extra library as described in
[docs/5_tools_mcp.md](docs/5_tools_mcp.md). See "Connect the Outreach tools
through MCP" below.

## The four stages

| # | Stage | Game level | Real tool | The lesson |
|---|---|---|---|---|
| 1 | [`source`](docs/1_source.md) | apify_trawler | Apify actor | A scrape is **asynchronous**: POST → poll → fetch. The POST returns a run id, not leads. |
| 2 | [`enrich`](docs/2_enrich.md) | dog_enrichment | Apollo, Hunter + the research agent | A waterfall, cheapest first. Apollo, a contact-data service, finds the owner, their role, an email and the number of shops. Hunter, an email finder that costs more per lookup, is asked only for the contact fields Apollo left open: it finds Tin Roof Bakery's email and Morning Crumb's owner. Apollo knows how a bakery takes orders for only 3 of 23. For the other 20, the research agent reads the bakery's website and returns an answer with its source. You confirm an answer before it goes into the record. |
| 3 | [`qualify`](docs/3_qualify.md) | qualification_baseball | four rules | Four rules on the enriched record, no outside calls. Every rejection names the rule it broke. A field that is still unknown sends the bakery to needs_review. Rejected and uncertain rows stay out of Outreach. The game saves a checked decision before optional batting. |
| 4 | [`outreach`](docs/4_outreach.md) | delivery_city | Claude + a sequencer | Lint before you send, use email in the required example, and distinguish sent, delivered, reply and confirmed meeting. |

Each stage is a Claude Code skill (`.claude/skills/*/SKILL.md`) and a standalone script
(`pipeline/*.py`). Ask Claude to "enrich these leads" and it loads the skill,
which tells it when the stage applies and invokes the matching script.

### The Apify Real Run shown in the game

The passive cinematic after `apify_trawler` reads these files directly from
this repository while it plays:

- `skills/source_leads/SKILL.md`
- `pipeline/source_bakeries.py`
- `pipeline/providers/apify.py`
- `captures/apify_bakeries_25.json`

It shows Claude source 25 Chicago bakery listings through Apify's Google Maps
actor, drop the two repeated listings, write the 23 that remain to
`out/raw_bakery_leads.csv`, and save the same records to `out/leads.json`. The
game does not contain a second pretend copy of the code.

## Layout

```
.claude/skills/       four discoverable Claude Code skills, one per stage
skills/               matching copies read by the game preview
pipeline/
  schema.py           record shapes — the contract with the game's localStorage
  providers/          every external call, each with a practice twin, plus the
                      two fixture files the practice twins look their answers up in
  source_bakeries.py  stage 1
  enrich.py           stage 2
  review_research.py  stage 2: you confirm a research-agent answer
  qualify.py          stage 3
  outreach.py         stage 4
  run_pipeline.py     all four, chained through JSON handoff files
fixtures/             the 25 bakery listings every offline run starts from
                      (two of them are deliberate repeats), the research
                      agent's recorded answers, and the answers you have
                      already confirmed
captures/             sanitized Real Run metadata; never credentials
tests/                pytest — all providers mocked, no network
docs/                 one page per stage, tying the level to the real tool
```

## The handoff files speak the game's schema

| File | localStorage key | Written by |
|---|---|---|
| `out/leads.json` | `claude3d.leads.v1` | source |
| `out/enriched.json` | `claude3d.enriched.v1` | enrich |
| `out/qualified.json` | `claude3d.qualified.v1` | qualify |
| `out/outreach.json` | `claude3d.outreach.v1` | outreach |
| `out/funnel.json` | `claude3d.funnel.v1` | run_pipeline |

Keys, field names, and the funnel's stage order are transcribed from the level
source (`trawler_haul.js`, `hunt_mechanic.js`, `delivery_handoff.js`,
`funnel_ledger.js`) and named in the comments of `pipeline/schema.py`. If the two
ever disagree, "I played the level, now I run the real thing" stops being one
continuous lesson and becomes two unrelated products.

Files, not one in-process function, for the same reason the game uses
localStorage: you can inspect the intermediate state, re-run one stage without
re-running the expensive one before it, and a crash in stage 4 does not throw
away the money stage 2 already spent.

## Going live

Copy `.env.example` to `.env` and fill in only what you want to run for real —
each key is independent, and a blank one just leaves that stage mocked.

| Key | Stage | Where |
|---|---|---|
| `APIFY_TOKEN` | source | apify.com → Settings → Integrations |
| `APOLLO_API_KEY` | enrich (contact data) | your Apollo account |
| `HUNTER_API_KEY` | enrich (email finder, for Apollo's gaps) | hunter.io → API |
| `ANTHROPIC_API_KEY` | outreach copy | console.anthropic.com |
| `EMAIL_SENDER_API_KEY` | outreach send | Instantly / Resend / SMTP |

Three deliberate speed bumps on the way to live, each one a mistake you cannot
take back:

- **The research agent is not run live from this repo.** Enrich replays the
  recorded answers of the agent you build in Repository 2
  (research-agent-four-ways). To use a live agent, run it there and save its
  answers in the same shape as `fixtures/research_agent_answers.json`.
- **The live send adapter is unimplemented** and raises. Wiring it mails real
  people from your domain.
- **The funnel is only marked `live` when every stage was live.** A run with one
  mocked stage is a mocked run, and a report that claims otherwise is how somebody
  ends up trusting fabricated numbers.

## Reading the funnel

The report follows the same records through the four stages:

```
HAULED → LEADS → ENRICHED (COMPLETE n) → QUALIFIED (REJECTED n, NEEDS REVIEW n)
       → SENT → DELIVERED (UNDELIVERED n) → REPLIES → MEETINGS
```

`COMPLETE` counts records with all required facts. Qualification produces three
separate lists: qualified, rejected and needs review. Only the qualified list
enters Outreach. Unknown facts stay in review; they are not failed sales.
A sent email can bounce, and a delivered email can receive no reply.
`UNDELIVERED` counts sent emails that were not delivered. `MEETINGS` counts
confirmed meetings only: a meeting needs a separate agreed time and
confirmation. It is still not a sale.

The saved example starts with 25 listings and removes two repeats: 23 bakeries,
21 complete records, 9 qualified, 12 rejected and 2 needing review. Outreach
holds one draft, sends 8 emails, receives 5 delivery receipts and 2 replies,
and records 1 confirmed meeting with 0 customers. Rye & Rosemary remains in
review until you check its evidence.

Older JSON keys such as `chased`, `missed`, `inboxed` and `spammed` remain for
compatibility with earlier course readers. They are not this game's batting
score. Use the three qualification lists and actual delivery receipts to
interpret the result.

## Your place in the system

Sales funnel → Lead generation → Enrich → Research is a zoom into smaller parts. Source → Enrich → Qualify → Outreach is forward progress through the pipeline.

## The research agent inside Enrich

Apollo and Hunter track people and companies, not how a bakery takes orders.
Only the bakery's own website says. Without the agent, someone would inspect the websites or ask the bakeries directly.

For each bakery Apollo could not answer, the research agent reads the bakery's
website, finds the order page, and returns "no online ordering" or the kind of
platform, with the page it read as the source. It fills `ordering_platform`
only. The agent is the one you build in Repository 2. Here, Enrich replays its
recorded answers from `fixtures/research_agent_answers.json`, in Repository 2's
verdict shape: `supportsNoOnlineOrdering`, a one-sentence `researchReason`, and
`sourceUrls`, plus the page and passage you check. Run Enrich with `--no-agent`
to skip it.

You check the source before an answer goes into the record. The record keeps
who confirmed it and the source. Then the record goes to Qualify once.

**How the course run handles confirmations.** You have already checked 18 of
the 20 answers in the course, so the default run reads those checks from
`fixtures/research_confirmations.json`. Two bakeries are left:

- **Rye & Rosemary Bakery.** The agent read one page further, to `/order`: orders
  by phone or email form only. Its answer is waiting for your check, so the
  default run sends Rye to needs_review. Check it yourself:

  ```bash
  python3 -m pipeline.review_research --lead "Rye & Rosemary Bakery"
  python3 -m pipeline.review_research --lead "Rye & Rosemary Bakery" --reviewed-by "Your name" --confirm
  python3 -m pipeline.qualify
  ```

  The first command shows the answer, the source page and the passage. The
  second writes `ordering_platform = "none"` into `out/enriched.json` with your
  name and the source. Qualify then prints 10 qualified: Rye is the tenth.
- **Dos Rios Panaderia.** The agent could not tell: the bakery's own pages never
  say how it takes orders, and the only outside listing is undated. There is no
  answer to confirm, so Dos Rios stays in needs_review.

Outreach distinguishes a send, delivery, reply and confirmed meeting. A confirmed meeting is an Opportunity to pitch in Sales conversations. The fictional baseline records one meeting and no customers.

A skill is reusable instructions for running a task. An agent is a program that uses a model to choose its next action within a bounded job. A skill can call an agent; user invocation and action selection are different choices. Automatically starting those jobs or continuing the entire pipeline is an extension exercise.

## Phase 5: run a skill after you run Python

A skill is a saved set of instructions Claude can follow. Open Claude Code in
this repository, then enter `/source-leads`, `/enrich-leads`, `/qualify-leads`,
and `/outreach` in that order. The files in `.claude/skills/` are discovered
automatically; no global installation is needed. The skills run the same
Python commands with `--offline` and inspect the same `out/` files.

Claude Code itself requires the learner's own access and may incur model usage;
the Python-only path above needs neither Claude nor a paid account. We do not
run model calls in the tests. See the [official skills documentation](https://code.claude.com/docs/en/skills).

## Connect the Outreach tools through MCP

The game finished lead generation and introduced the tower's tool connection.
Now make that connection real. MCP is a standard way for an application to
discover tools, call them and receive results. One local server exposes
`prepare_checked_email`, `send_checked_email`, `read_outreach_results` and
`record_confirmed_meeting`, using the same checks and saved bakery receipts.

Follow [the local MCP exercise](docs/5_tools_mcp.md) after running Python and the
skills. It adds the optional `.[mcp]` libraries and runs a real local client/server
exchange with `python3 -m pipeline.mcp_demo`, saving each request and result.
No model or external sending service runs. A send acknowledgement leaves delivery
and reply unknown until their results are read; a separate confirmation is needed
to record a meeting. Repeated requests do not duplicate sends or meetings.

The guide also connects the same server to Claude Code using your own account.
Skills provide instructions, tools perform actions, and MCP carries requests and
results. The next project adds a researcher that chooses its next tool action
based on returned evidence.

## Phase 6: bring back one research result

Repository 2's required build uses Claude Agent SDK. Its offline rehearsal saves
`out/research_agent_answers.json`. Import that file into this repository without
sending anything or automatically accepting its answer:

```bash
python3 -m pipeline.import_research --answers ../research-agent-four-ways/out/research_agent_answers.json
python3 -m pipeline.review_research --lead "Rye & Rosemary Bakery"
python3 -m pipeline.review_research --lead "Rye & Rosemary Bakery" --reviewed-by "Your name" --confirm
python3 -m pipeline.qualify --offline
```

Run the normal pipeline first so `out/enriched.json` exists. Import attaches the
new evidence only to records still missing ordering data. It keeps existing
confirmed values. Inspect the cited page or saved excerpt before confirming.
Rye then becomes the tenth qualified bakery; Dos Rios remains uncertain. This
does not re-run Outreach or create a meeting. The original baseline remains
9 qualified, 12 rejected, 2 review, 8 emails, 5 deliveries, 2 replies, 1 meeting
and 0 customers until you deliberately run a new scenario.

## What the live contact lookup can prove

The implemented Apollo adapter calls people search and then people enrichment.
Search does not supply email; enrichment can use account credits. The adapter
checks the current employer domain, maps an explicitly supported Owner/Manager
role, and keeps only a verified business email. It never treats an HQ address as
one shop, and it never interprets a missing website technology as no ordering.
Live shop counts and ordering methods therefore remain unknown until checked.
The richer practice provider is a teaching dataset, not a coverage guarantee.

The Hunter adapter calls domain search on the bakery's own web domain. It keeps
only an explicitly supported Owner/Manager position and only an email Hunter
marks as valid. It is asked only for the fields Apollo left open, and never for
shop counts or ordering methods. A bakery with no website has no domain to
search, so live Hunter misses it. Set `HUNTER_API_KEY` to use it.

For a real list, run Source with your own Apify credentials, then Enrich with your
Apollo credentials; omit `--offline` only when you intend to use those services.
Both adapters have mocked HTTP contract tests; no authenticated Apollo or Hunter run was made
in course verification. Missing records or fields stay empty. If you have checked
a bakery's locations page yourself, record that fact and its source:

```bash
python3 -m pipeline.review_contact --lead "Exact bakery name" --field location_count --value 1 --source-url "https://your-bakery.com/locations" --reviewed-by "Your name"
```

Replace every example value with the fact you actually checked. Other supported
contact fields are owner_name, owner_role and email. For ordering evidence, use
Repository 2 and review_research instead. The ledger's dollar values are simulated
course units, not current provider prices or an invoice for live use.

The optional live send adapter is intentionally absent: prepare and inspect drafts,
then export them for your own sending system. Setting a sender key never silently
activates email. No default command sends messages to real people.

Provider references (checked 2026-09-26): [People search](https://docs.apollo.io/reference/people-api-search),
[People enrichment](https://docs.apollo.io/reference/people-enrichment),
[Hunter domain search](https://hunter.io/api-documentation/v2#domain-search).

To export checked messages without invoking a sender, use the implemented draft
mode. This works on a qualified file from your own list as well as the course data:

```bash
python3 -m pipeline.outreach --drafts-only --offline
```

It writes `out/drafts.json`: message text, recipient address and held records with
reasons, with zero sends and zero meetings. `--offline` also keeps copy generation
local; omit it only if you explicitly want your configured copy provider. A sender
key is ignored in draft mode. Import the reviewed drafts into your own sending
system separately; this course does not implement or silently activate live email.
