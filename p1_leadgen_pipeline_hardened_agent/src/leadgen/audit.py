"""Verbose audit trail: every step of one execution as "actor → action → target".

Off by default. `leadgen plan --audit` and `leadgen run --audit` set LEADGEN_AUDIT=1, and then
each component writes one row per thing it does: the CLI creating a plan, the Agent SDK calling
a tool, the budget guard reserving money, a worker claiming a job, Exa answering a search, a stage
writing its checkpoint. The dashboard's Audit log tab reads the rows as they arrive.

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
    """Record one step. A no-op unless auditing is on."""
    if not audit_enabled() or connection is None:
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
                target,
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
            "AND actor = 'Planner (leadgen plan)'",
            (plan_id,),
        )
    except Exception:  # noqa: BLE001 - the audit trail must never stop the pipeline
        pass


def component_for_operation(operation: str) -> str:
    """The pipeline component behind a reservation key such as 'web:tier1:7:2:search'."""
    prefix = operation.split(":", 1)[0]
    return {
        "maps": "Maps stage",
        "exa": "Exa stage",
        "web": "Qualify/research web tools",
        "judge": "Claude judge",
        "sdk": "Agent SDK session",
        "enrich": "Enrich worker",
    }.get(prefix, f"Pipeline ({prefix})")


def audit_sdk_message(connection, run_id, agent: str, message) -> None:
    """One row per Agent SDK message: what the model said, which tool it called, what came back,
    and the session's final usage."""
    if not audit_enabled():
        return
    from claude_agent_sdk import (
        AssistantMessage,
        ResultMessage,
        SystemMessage,
        TextBlock,
        ThinkingBlock,
        ToolResultBlock,
        ToolUseBlock,
        UserMessage,
    )

    model_label = f"Claude ({agent})"
    if isinstance(message, SystemMessage):
        audit(
            connection,
            "Agent SDK",
            f"starts the {agent} session ({message.subtype})",
            model_label,
            run_id=run_id,
        )
    elif isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, ToolUseBlock):
                audit(
                    connection,
                    model_label,
                    f"calls tool {block.name} with {block.input}",
                    "leadgen MCP tools (via Agent SDK)",
                    run_id=run_id,
                    tool_use_id=block.id,
                    model=message.model,
                )
            elif isinstance(block, TextBlock) and block.text.strip():
                audit(connection, model_label, f"says: {block.text}", "Agent SDK", run_id=run_id)
            elif isinstance(block, ThinkingBlock):
                audit(
                    connection, model_label, "thinks (hidden reasoning)", "Agent SDK", run_id=run_id
                )
    elif isinstance(message, UserMessage) and isinstance(message.content, list):
        for block in message.content:
            if isinstance(block, ToolResultBlock):
                audit(
                    connection,
                    "leadgen MCP tools (via Agent SDK)",
                    ("returns an error: " if block.is_error else "returns: ") + str(block.content),
                    model_label,
                    run_id=run_id,
                    tool_use_id=block.tool_use_id,
                )
    elif isinstance(message, ResultMessage):
        audit(
            connection,
            "Agent SDK",
            f"ends the {agent} session ({message.subtype}, {message.num_turns} turns, "
            f"${message.total_cost_usd})",
            "Runner (leadgen run)",
            run_id=run_id,
            is_error=message.is_error,
            duration_ms=message.duration_ms,
        )
