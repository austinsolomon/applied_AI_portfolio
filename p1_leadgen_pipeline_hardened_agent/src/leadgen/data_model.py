"""A guide to every Postgres table and view: one sentence on what it holds and why, and a few
made-up example rows showing the shape of its data. Shown in the dashboard's Data model tab.

The example rows are invented for teaching (".example" domains, 555 phone numbers) and show a
selection of each table's columns. `tests/test_data_model.py` fails when a table or view exists
in the schema without an entry here, so the guide cannot fall behind the migrations.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TableGuide:
    name: str
    summary: str
    examples: list[dict[str, Any]] = field(default_factory=list)
    kind: str = "table"


RUN = "7c1e2f04-…"
GROUPS: list[tuple[str, list[TableGuide]]] = [
    (
        "Demand and CRM",
        [
            TableGuide(
                "markets",
                "The metro areas the business sells into, each with a center point, service "
                "radius and a goal of how many available leads it should have.",
                [
                    {
                        "market_id": 1,
                        "name": "Phoenix",
                        "state": "AZ",
                        "service_radius_km": 50,
                        "lead_goal": 100,
                    },
                    {
                        "market_id": 2,
                        "name": "Los Angeles",
                        "state": "CA",
                        "service_radius_km": 50,
                        "lead_goal": 100,
                    },
                ],
            ),
            TableGuide(
                "crm_leads",
                "The CRM: one row per business, with its contact details, status and, for "
                "pipeline-found leads, why it qualified and the quote that proves it.",
                [
                    {
                        "lead_id": 401,
                        "market_id": 1,
                        "business_name": "Desert Sage Financial Planning",
                        "status": "available",
                        "phone": "(602) 555-0142",
                        "website_domain": "desertsageplanning.example",
                        "source": "exa",
                        "qualification_reason": "Independent firm with a CFP® planner in Phoenix",
                    },
                    {
                        "lead_id": 12,
                        "market_id": 1,
                        "business_name": "Golden Wealth Planning 3",
                        "status": "assigned",
                        "phone": "(602) 555-0102",
                        "website_domain": "goldenwealthplanning3-phoenix.example",
                        "source": "demo_seed",
                        "qualification_reason": None,
                    },
                ],
            ),
            TableGuide(
                "gap_view",
                "A view, not a table: for each market, available leads against the goal, leads "
                "still needed and the percentage of the goal in hand, read by both the dashboard "
                "and the planner.",
                [
                    {
                        "market_name": "Phoenix",
                        "lead_goal": 100,
                        "available_leads": 10,
                        "leads_needed": 90,
                        "leads_available_pct": 10,
                        "goal_met": False,
                    },
                    {
                        "market_name": "Seattle",
                        "lead_goal": 100,
                        "available_leads": 100,
                        "leads_needed": 0,
                        "leads_available_pct": 100,
                        "goal_met": True,
                    },
                ],
                kind="view",
            ),
            TableGuide(
                "places",
                "Every US city with at least 5,000 people from the 2020 Census, which the "
                "planner uses to turn a market into a list of real cities to search.",
                [
                    {
                        "geoid": "0455000",
                        "state": "AZ",
                        "name": "Phoenix",
                        "population": 1608139,
                        "latitude": 33.57,
                        "longitude": -112.09,
                    },
                    {
                        "geoid": "0444000",
                        "state": "AZ",
                        "name": "Mesa",
                        "population": 504258,
                        "latitude": 33.40,
                        "longitude": -111.72,
                    },
                ],
            ),
        ],
    ),
    (
        "Planning, approval and runs",
        [
            TableGuide(
                "scrape_plans",
                "A proposed sourcing job; it stays a draft until a person approves it.",
                [
                    {"plan_id": 3, "status": "approved", "created_at": "2026-11-05 09:12"},
                    {"plan_id": 4, "status": "draft", "created_at": "2026-11-05 09:40"},
                ],
            ),
            TableGuide(
                "scrape_plan_cells",
                "The rows of a plan: one market, city and keyword each, with the expected number "
                "of businesses and the Google Maps spending cap.",
                [
                    {
                        "cell_id": 7,
                        "plan_id": 3,
                        "market_id": 1,
                        "city": "Phoenix, AZ",
                        "keyword": "financial planner",
                        "expected_raw_leads": 30,
                        "estimated_cost_usd": 1.00,
                        "status": "scraped",
                        "apify_run_id": "aB3xYz",
                    }
                ],
            ),
            TableGuide(
                "approvals",
                "The single human decision per plan: who approved it, when, and the total "
                "budget for every service.",
                [
                    {
                        "approval_id": 3,
                        "plan_id": 3,
                        "budget_usd": 3.00,
                        "approved_by": "operator",
                        "approved_at": "2026-11-05 09:13",
                    }
                ],
            ),
            TableGuide(
                "runs",
                "One execution of an approved plan, and whether it is running, finished, failed "
                "or stopped.",
                [
                    {
                        "run_id": RUN,
                        "plan_id": 3,
                        "status": "running",
                        "started_at": "09:14:02",
                        "finished_at": None,
                    }
                ],
            ),
            TableGuide(
                "pipeline_stages",
                "The run's state machine checkpoints: a stage gets a row with its result once it "
                "is done, so a resumed run skips it.",
                [
                    {
                        "run_id": RUN,
                        "stage": "maps",
                        "result": {"sourced": 18},
                        "completed_at": "09:16:40",
                    },
                    {
                        "run_id": RUN,
                        "stage": "exa",
                        "result": {"sourced": 31, "queries": 3},
                        "completed_at": "09:17:05",
                    },
                ],
            ),
            TableGuide(
                "run_ledger",
                "Every attempt to scrape a cell (sample, then full), so the same cell is never "
                "scraped twice by accident.",
                [
                    {
                        "attempt_id": 9,
                        "run_id": RUN,
                        "cell_id": 7,
                        "stage": "sample",
                        "attempt_number": 1,
                        "status": "succeeded",
                        "leads_found": 10,
                    },
                    {
                        "attempt_id": 10,
                        "run_id": RUN,
                        "cell_id": 7,
                        "stage": "full",
                        "attempt_number": 1,
                        "status": "succeeded",
                        "leads_found": 18,
                    },
                ],
            ),
            TableGuide(
                "cell_jobs",
                "The job queue that enrich and qualify workers take work from, one job per lead, "
                "with attempts and locks so two workers never take the same lead.",
                [
                    {
                        "job_id": 55,
                        "kind": "qualify",
                        "job_key": "214",
                        "status": "running",
                        "attempts": 1,
                        "locked_by": "worker-2",
                    },
                    {
                        "job_id": 54,
                        "kind": "enrich",
                        "job_key": "214",
                        "status": "succeeded",
                        "attempts": 1,
                        "locked_by": None,
                    },
                ],
            ),
        ],
    ),
    (
        "Sourced leads and their evidence",
        [
            TableGuide(
                "raw_leads",
                "Every business Maps or Exa found in a run, before it reaches the CRM, with "
                "dedupe, enrichment and qualification results filled in as stages finish.",
                [
                    {
                        "raw_lead_id": 214,
                        "source": "exa",
                        "business_name": "Desert Sage Financial Planning",
                        "website": "https://desertsageplanning.example/",
                        "query_consensus": 3,
                        "duplicate": False,
                        "enriched": True,
                        "qualification": {"verdict": "qualified"},
                    },
                    {
                        "raw_lead_id": 201,
                        "source": "google_maps",
                        "business_name": "Sonoran Grill",
                        "phone": "(602) 555-0177",
                        "query_consensus": 1,
                        "duplicate": False,
                        "enriched": True,
                        "qualification": {"verdict": "rejected"},
                    },
                ],
            ),
            TableGuide(
                "seen_leads",
                "Every business any run has ever found, keyed like the CRM, so the same business "
                "is not counted as new twice.",
                [
                    {
                        "seen_lead_id": 88,
                        "google_place_id": "ChIJ-example-1",
                        "phone_normalized": "6025550177",
                        "website_domain": "sonorangrill.example",
                    }
                ],
            ),
            TableGuide(
                "page_cache",
                "Web pages already read (from Exa, Firecrawl or a plain fetch), kept for seven "
                "days so a repeat read costs nothing.",
                [
                    {
                        "url": "https://desertsageplanning.example/",
                        "backend": "exa",
                        "status_code": 200,
                        "title": "Desert Sage | Independent financial planning in Phoenix",
                        "text": "Jordan Example, CFP®, leads our fee-only practice in Phoenix…",
                    }
                ],
            ),
            TableGuide(
                "contact_cache",
                "One BlitzAPI lookup per website domain: the company, the best-placed person, "
                "and their email and phone, so a domain is never paid for twice.",
                [
                    {
                        "domain": "desertsageplanning.example",
                        "found": True,
                        "person_name": "Jordan Example",
                        "person_title": "Owner",
                        "email": "jordan@desertsageplanning.example",
                        "records_used": 2,
                    },
                    {
                        "domain": "quietnook.example",
                        "found": False,
                        "person_name": None,
                        "email": None,
                        "records_used": 1,
                    },
                ],
            ),
            TableGuide(
                "agent_columns",
                "Settings for the qualification step: which pages to read, how many searches, "
                "and the evidence size limit.",
                [
                    {
                        "column_name": "qualification",
                        "config": {
                            "pages": [""],
                            "site_searches": 1,
                            "searches": 1,
                            "max_evidence_characters": 18000,
                        },
                    }
                ],
            ),
        ],
    ),
    (
        "Money and control",
        [
            TableGuide(
                "budget_reservations",
                "The budget guard: before every paid call the code reserves that call's maximum "
                "cost here, then marks it succeeded, or uncertain if the outcome is unknown.",
                [
                    {
                        "reservation_id": 301,
                        "operation": "exa:1:independent fee-only financial planning firms…",
                        "ceiling_usd": 0.03,
                        "status": "succeeded",
                    },
                    {
                        "reservation_id": 344,
                        "operation": "web:tier1:214:2:search",
                        "ceiling_usd": 0.02,
                        "status": "uncertain",
                    },
                ],
            ),
            TableGuide(
                "cost_ledger",
                "Every paid call's actual cost: service, endpoint, tokens or units, and whether "
                "the provider reported the amount or it is an estimate.",
                [
                    {
                        "cost_entry_id": 912,
                        "service": "exa",
                        "endpoint": "search",
                        "cost_usd": 0.022,
                        "cost_basis": "provider_reported",
                    },
                    {
                        "cost_entry_id": 913,
                        "service": "claude",
                        "model": "claude-haiku-5-5",
                        "input_tokens": 3120,
                        "output_tokens": 180,
                        "cost_usd": 0.0013,
                        "cost_basis": "provider_reported",
                    },
                ],
            ),
        ],
    ),
    (
        "Evaluation and observability",
        [
            TableGuide(
                "eval_records",
                "Every automated decision (sample grade, qualification verdict, CRM contradiction) "
                "with the evidence behind it, for people to review.",
                [
                    {
                        "eval_record_id": 77,
                        "kind": "judge_decision",
                        "subject": "Desert Sage Financial Planning",
                        "verdict": "qualified",
                        "reason": "Independent firm with a CFP® planner in Phoenix",
                        "model": "claude-haiku-5-5",
                    }
                ],
            ),
            TableGuide(
                "eval_labels",
                "A person's pass/fail on an automated decision; a fail also records the verdict "
                "it should have been, a one-line critique and failure-mode tags, and labels "
                "become test fixtures.",
                [
                    {
                        "eval_record_id": 77,
                        "label": "incorrect",
                        "correct_verdict": "rejected",
                        "failure_modes": ["national-firm branch advisor missed"],
                        "critique": "The page says the advisor is with Edward Jones",
                        "labeled_by": "reviewer",
                    }
                ],
            ),
            TableGuide(
                "traces",
                "A short timeline of each run: stage outcomes, hook decisions and how long each "
                "paid call took.",
                [
                    {
                        "trace_id": 501,
                        "operation": "exa:1:independent fee-only financial planning firms…",
                        "outcome": "succeeded",
                        "latency_ms": 1840,
                    },
                    {
                        "trace_id": 502,
                        "operation": "PreToolUse",
                        "outcome": "allow",
                        "latency_ms": None,
                    },
                ],
            ),
            TableGuide(
                "audit_events",
                "The opt-in trail of one execution (--audit): each major step as who does "
                "what to whom, from 19 actors named Tool (role).",
                [
                    {
                        "audit_event_id": 203,
                        "actor": "Postgres (Budget reservation)",
                        "action": "blocks judge:1:41: Anthropic allowance exhausted (0.75 USD "
                        "per plan)",
                        "target": "Claude API (LLM call)",
                    },
                    {
                        "audit_event_id": 204,
                        "actor": "Exa API (Search)",
                        "action": "company search returns 25 results: 12 new businesses, "
                        "3 already found, 10 filtered out",
                        "target": "independent CFP® financial planners offering retirement and "
                        "comprehensive planning in Phoenix, AZ",
                    },
                ],
            ),
        ],
    ),
    (
        "Housekeeping",
        [
            TableGuide(
                "schema_migrations",
                "Which numbered SQL migration files have been applied, with a checksum so an "
                "edited migration is refused.",
                [
                    {
                        "version": 19,
                        "name": "0019_gap_view_available_over_goal.sql",
                        "checksum": "3f9a…",
                        "applied_at": "2026-11-04 16:15",
                    },
                    {
                        "version": 20,
                        "name": "0020_audit_events.sql",
                        "checksum": "b71c…",
                        "applied_at": "2026-11-04 16:40",
                    },
                ],
            ),
        ],
    ),
]


def all_guides() -> list[TableGuide]:
    return [guide for _, guides in GROUPS for guide in guides]
