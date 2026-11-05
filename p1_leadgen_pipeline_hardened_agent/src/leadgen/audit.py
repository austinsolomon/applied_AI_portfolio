"""Audit trail: the major steps of one execution as "actor → action → target".

Off by default. `leadgen plan --audit` and `leadgen run --audit` set LEADGEN_AUDIT=1, and then
each major actor writes one row per thing it does. There are exactly 19 actors, each named
"Tool (role)" and listed in `ACTORS` in pipeline order: a person or the weekly policy approving
the plan, the planner pricing it, the Agent SDK calling a stage tool, the budget reserving money,
each paid API answering, the code checks grading what came back, and Postgres recording stage
state, dedupe and the CRM upload. A row from any other actor is skipped. The dashboard's Audit
log tab reads the rows as they arrive.

Bookkeeping is not a row of its own: a paid call's cost is a `cost_usd` detail on that API's
row, a page read from the cache makes no row, and job claims and worker steps make none.

Rules for `target`: the thing acted on (a query, URL, lead name, stage or plan), not a table.
Rules for `detail`: plain facts a reader needs to follow the step (ids, counts, dollars, URLs,
queries, verdicts). Never credentials, and never a business's phone number or email.
Writing an audit row never breaks the pipeline: a failure is swallowed.
"""

import os
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

AUDIT_ENVIRONMENT_VARIABLE = "LEADGEN_AUDIT"
MAX_TEXT = 600

HUMAN_APPROVAL = "CLI (Human approval)"
POLICY_APPROVAL = "Python (Policy approval)"
PLANNER = "Python (Planner)"
SDK_TOOL_CALL = "Claude Agent SDK (Tool call)"
SDK_PRE_TOOL_USE_HOOK = "Claude Agent SDK (PreToolUse hook)"
BUDGET_RESERVATION = "Postgres (Budget reservation)"
APIFY_GOOGLE_MAPS = "Apify API (Google Maps)"
SAMPLE_GRADER = "Python (Sample grader)"
EXA_SEARCH = "Exa API (Search)"
EXA_CONTENTS = "Exa API (Contents)"
FIRECRAWL_RENDER = "Firecrawl API (Render)"
CONTACT_EXTRACTION = "Python (Contact extraction)"
DEDUPE = "Postgres (Dedupe)"
BLITZ_ENRICHMENT = "BlitzAPI (Enrichment)"
CLAUDE_LLM_CALL = "Claude API (LLM call)"
CITATION_CHECK = "Python (Citation check)"
SDK_RESEARCH_AGENT = "Claude Agent SDK (Research agent)"
STATE_MACHINE = "Postgres (State machine)"
CRM_UPLOAD = "Postgres (CRM upload)"

# Every actor the trail accepts, in pipeline order (the dashboard's filter lists them this way).
ACTORS = (
    HUMAN_APPROVAL,
    POLICY_APPROVAL,
    PLANNER,
    SDK_TOOL_CALL,
    SDK_PRE_TOOL_USE_HOOK,
    BUDGET_RESERVATION,
    APIFY_GOOGLE_MAPS,
    SAMPLE_GRADER,
    EXA_SEARCH,
    EXA_CONTENTS,
    FIRECRAWL_RENDER,
    CONTACT_EXTRACTION,
    DEDUPE,
    BLITZ_ENRICHMENT,
    CLAUDE_LLM_CALL,
    CITATION_CHECK,
    SDK_RESEARCH_AGENT,
    STATE_MACHINE,
    CRM_UPLOAD,
)


def enable_audit() -> None:
    os.environ[AUDIT_ENVIRONMENT_VARIABLE] = "1"


def audit_enabled() -> bool:
    return os.environ.get(AUDIT_ENVIRONMENT_VARIABLE) == "1"


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_plain(item) for item in value]
    if isinstance(value, str) and len(value) > MAX_TEXT:
        return value[:MAX_TEXT] + "…"
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)[:MAX_TEXT]


def audit(
    connection,
    actor: str,
    action: str,
    target: str,
    *,
    run_id: UUID | str | None = None,
    plan_id: int | None = None,
    **detail: Any,
) -> None:
    """Record one step. A no-op unless auditing is on, and for an actor outside `ACTORS`."""
    if not audit_enabled() or connection is None or actor not in ACTORS:
        return
    try:
        if plan_id is None and run_id is not None:
            row = connection.execute(
                "SELECT plan_id FROM runs WHERE run_id = %s", (str(run_id),)
            ).fetchone()
            plan_id = row[0] if row else None
        connection.execute(
            "INSERT INTO audit_events (run_id, plan_id, actor, action, target, detail) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (
                str(run_id) if run_id is not None else None,
                plan_id,
                actor,
                action[:MAX_TEXT],
                str(target)[:MAX_TEXT],
                Jsonb(_plain(detail)),
            ),
        )
    except Exception:  # noqa: BLE001 - the audit trail must never stop the pipeline
        pass


def attach_planning_events(connection, plan_id: int) -> None:
    """The planner's reads happen before its plan exists; file them under the new plan."""
    if not audit_enabled():
        return
    try:
        connection.execute(
            "UPDATE audit_events SET plan_id = %s WHERE plan_id IS NULL AND run_id IS NULL "
            "AND actor = %s",
            (plan_id, PLANNER),
        )
    except Exception:  # noqa: BLE001 - the audit trail must never stop the pipeline
        pass


def component_for_operation(operation: str) -> str:
    """The actor whose paid call a reservation key such as 'web:tier1:7:2:search' covers."""
    prefix = operation.split(":", 1)[0]
    if prefix == "web":
        return EXA_CONTENTS if operation.endswith(":fetch_page") else EXA_SEARCH
    if prefix == "sdk":
        return SDK_RESEARCH_AGENT if operation.startswith("sdk:research") else SDK_TOOL_CALL
    return {
        "maps": APIFY_GOOGLE_MAPS,
        "exa": EXA_SEARCH,
        "judge": CLAUDE_LLM_CALL,
        "enrich": BLITZ_ENRICHMENT,
    }.get(prefix, f"Pipeline ({prefix})")
