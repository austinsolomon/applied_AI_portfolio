"""Read models for the dashboard: one run's stage-by-stage state, run history, and source yield.

Read-only. Every number comes from the tables the pipeline writes (runs, pipeline_stages,
cell_jobs, budget_reservations, cost_ledger, traces, raw_leads, crm_leads), so the dashboard shows
exactly what the runner recorded. No contact details or page evidence are returned.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from leadgen.orchestrator import STAGES

STAGE_LABELS = {
    "maps": "Google Maps",
    "exa": "Exa search",
    "adapt": "Adapt sourcing (agent)",
    "extract": "Extract contacts",
    "dedupe": "Dedupe",
    "enrich": "Enrich contacts",
    "qualify": "Qualify",
    "crm": "Write to CRM",
}
STAGE_DONE = "done"
STAGE_RUNNING = "running"
STAGE_FAILED = "failed"
STAGE_BLOCKED = "blocked"
STAGE_WAITING = "waiting"
# A finished run that predates a stage (e.g. runs before the sourcing agent existed).
STAGE_SKIPPED = "skipped"


@dataclass(frozen=True)
class StageState:
    stage: str
    label: str
    status: str
    result: dict[str, Any] | None
    completed_at: datetime | None


@dataclass(frozen=True)
class RunDetail:
    run_id: UUID
    plan_id: int
    market: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    budget_usd: Decimal | None
    spend_usd: Decimal
    held_usd: Decimal
    stages: list[StageState]
    jobs: dict[str, dict[str, int]] = field(default_factory=dict)
    spend_by_service: dict[str, Decimal] = field(default_factory=dict)
    needs_reconciliation: list[str] = field(default_factory=list)
    recent_events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def needs_a_human(self) -> bool:
        return bool(self.needs_reconciliation)


def stage_states(status: str, completed: dict[str, tuple], unresolved: bool) -> list[StageState]:
    """Done stages, then the stage the run is on (running, failed, or blocked when a paid call
    awaits reconciliation), then the stages still waiting."""
    states, current_found = [], False
    for stage in STAGES:
        if stage in completed:
            result, completed_at = completed[stage]
            states.append(StageState(stage, STAGE_LABELS[stage], STAGE_DONE, result, completed_at))
            continue
        if status == "finished":
            state = STAGE_SKIPPED
        elif current_found:
            state = STAGE_WAITING
        elif status == "running":
            state = STAGE_RUNNING
        else:
            state = STAGE_BLOCKED if unresolved else STAGE_FAILED
        current_found = True
        states.append(StageState(stage, STAGE_LABELS[stage], state, None, None))
    return states


def list_runs(connection: psycopg.Connection, limit: int = 50) -> list[tuple[UUID, str]]:
    """(run_id, short label) for a run picker, newest first."""
    rows = connection.execute(
        "SELECT run_id, plan_id, status, started_at FROM runs ORDER BY started_at DESC LIMIT %s",
        (limit,),
    ).fetchall()
    return [
        (run_id, f"{started:%Y-%m-%d %H:%M} · plan {plan_id} · {status} · {str(run_id)[:8]}")
        for run_id, plan_id, status, started in rows
    ]


def _market_label(connection: psycopg.Connection, plan_id: int) -> str:
    rows = connection.execute(
        "SELECT DISTINCT m.name || ', ' || m.state FROM scrape_plan_cells c "
        "JOIN markets m USING (market_id) WHERE c.plan_id = %s ORDER BY 1",
        (plan_id,),
    ).fetchall()
    return " / ".join(row[0] for row in rows) or "—"


def read_run_detail(connection: psycopg.Connection, run_id: UUID) -> RunDetail | None:
    run = connection.execute(
        "SELECT r.run_id, r.plan_id, r.status, r.started_at, r.finished_at, a.budget_usd "
        "FROM runs r LEFT JOIN approvals a USING (plan_id) WHERE r.run_id = %s",
        (run_id,),
    ).fetchone()
    if run is None:
        return None
    run_id, plan_id, status, started_at, finished_at, budget = run
    completed = {
        stage: (result, completed_at)
        for stage, result, completed_at in connection.execute(
            "SELECT stage, result, completed_at FROM pipeline_stages WHERE run_id = %s",
            (run_id,),
        ).fetchall()
    }
    unresolved = [
        operation
        for (operation,) in connection.execute(
            "SELECT operation FROM budget_reservations WHERE plan_id = %s "
            "AND status IN ('started','uncertain') ORDER BY reservation_id",
            (plan_id,),
        ).fetchall()
    ]
    held = connection.execute(
        "SELECT coalesce(sum(ceiling_usd), 0) FROM budget_reservations WHERE plan_id = %s "
        "AND status IN ('started','uncertain')",
        (plan_id,),
    ).fetchone()[0]
    jobs: dict[str, dict[str, int]] = {}
    for kind, job_status, count in connection.execute(
        "SELECT kind, status, count(*) FROM cell_jobs WHERE run_id = %s GROUP BY 1, 2",
        (run_id,),
    ).fetchall():
        jobs.setdefault(kind, {})[job_status] = count
    spend_by_service = dict(
        connection.execute(
            "SELECT service, sum(cost_usd) FROM cost_ledger WHERE run_id = %s "
            "GROUP BY 1 ORDER BY 2 DESC",
            (run_id,),
        ).fetchall()
    )
    with connection.cursor(row_factory=dict_row) as cursor:
        events = cursor.execute(
            "SELECT created_at, operation, outcome, latency_ms FROM traces WHERE run_id = %s "
            "ORDER BY trace_id DESC LIMIT 20",
            (run_id,),
        ).fetchall()
    return RunDetail(
        run_id=run_id,
        plan_id=plan_id,
        market=_market_label(connection, plan_id),
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        budget_usd=budget,
        spend_usd=sum(spend_by_service.values(), Decimal(0)),
        held_usd=held,
        stages=stage_states(status, completed, bool(unresolved)),
        jobs=jobs,
        spend_by_service=spend_by_service,
        needs_reconciliation=unresolved,
        recent_events=events,
    )


# Dashboard aggregates come from views (migration 0022), the only relations the hosted
# dashboard's database role may read.
DASHBOARD_VIEWS = (
    "gap_view",
    "run_history_view",
    "source_yield_view",
    "consensus_yield_view",
    "spend_by_service_view",
)
# The private operator dashboard (every tab, as on the operator's machine) reads every table and
# writes only eval labels.
OPERATOR_WRITABLE_TABLES = ("eval_labels",)


def _rows(connection: psycopg.Connection, query: str, parameters=()) -> list[dict[str, Any]]:
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(query, parameters).fetchall()


def _add_rate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for row in rows:
        row["qualified_rate"] = row["qualified"] / row["judged"] if row["judged"] else None
    return rows


def read_run_history(connection: psycopg.Connection, limit: int = 50) -> list[dict[str, Any]]:
    """One summary row per run, newest first, with the market and cost per qualified lead."""
    rows = _rows(
        connection, "SELECT * FROM run_history_view ORDER BY started_at DESC LIMIT %s", (limit,)
    )
    for row in rows:
        row["market"] = row["market"] or ""
        row["cost_per_qualified_usd"] = (
            row["spend_usd"] / row["qualified"] if row["qualified"] else None
        )
    return rows


def read_source_yield(connection: psycopg.Connection) -> list[dict[str, Any]]:
    """Per source across all runs: sourced, new, judged, qualified, and qualified ÷ judged."""
    return _add_rate(_rows(connection, "SELECT * FROM source_yield_view ORDER BY source"))


def read_consensus_yield(connection: psycopg.Connection) -> list[dict[str, Any]]:
    """Exa leads by how many query phrasings found them, with qualified ÷ judged for each."""
    return _add_rate(
        _rows(connection, "SELECT * FROM consensus_yield_view ORDER BY query_consensus")
    )


def read_spend_by_service(connection: psycopg.Connection) -> list[dict[str, Any]]:
    """All-time spend per service, split into provider-reported and estimated amounts."""
    return _rows(connection, "SELECT * FROM spend_by_service_view ORDER BY total_usd DESC")


def read_audit_events(connection: psycopg.Connection, plan_id: int) -> list[dict[str, Any]]:
    """Every audit row for one plan: planning and approval, then each run of it, in order."""
    with connection.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            "SELECT audit_event_id, recorded_at, run_id, actor, action, target, detail "
            "FROM audit_events WHERE plan_id = %s ORDER BY audit_event_id",
            (plan_id,),
        ).fetchall()


def audited_plans(connection: psycopg.Connection) -> list[tuple[int, str]]:
    """(plan_id, label) for plans that have an audit trail, newest first."""
    rows = connection.execute(
        "SELECT plan_id, min(recorded_at), count(*) FROM audit_events "
        "WHERE plan_id IS NOT NULL GROUP BY plan_id ORDER BY plan_id DESC"
    ).fetchall()
    return [
        (plan_id, f"plan {plan_id} · started {first:%Y-%m-%d %H:%M} · {count} events")
        for plan_id, first, count in rows
    ]
