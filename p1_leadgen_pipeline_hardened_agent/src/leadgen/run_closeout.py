"""Close out a run so the next run starts from a ready pipeline.

A paid call that raised can leave its reservation `uncertain` while the stage that made it goes
on (a lead is judged without one evidence page, for example). Once the call has returned, nothing
is still running at the provider, so the only unknown is the charge. Close-out books that charge
at the reservation's ceiling (the most the call was allowed to cost) and settles the reservation.

An Apify actor run is the exception: it can keep running and charging after the call that
started it failed. Its reservation is never settled by estimate; an operator confirms the actor
stopped and reconciles it (docs/operations.md).
"""

from decimal import Decimal

from psycopg.types.json import Jsonb

from leadgen.audit import BUDGET_RESERVATION, audit, component_for_operation
from leadgen.budget import record_cost, trace

# Reservation key prefix -> the cost_ledger service it bills. `web:` depends on the backend.
SERVICE_FOR_PREFIX = {"exa": "exa", "judge": "claude", "sdk": "claude", "enrich": "blitz"}
STILL_BILLABLE_PREFIXES = ("maps:",)
UNFINISHED_JOB_STATUSES = ("queued", "running")


class RunNotReady(Exception):
    """The run left work or paid calls open; the next run must not start on top of them."""


def settles_by_estimate(operation: str) -> bool:
    """Whether close-out may book this call at its ceiling (everything but an Apify actor)."""
    prefix = operation.split(":", 1)[0]
    return not operation.startswith(STILL_BILLABLE_PREFIXES) and (
        prefix == "web" or prefix in SERVICE_FOR_PREFIX
    )


def web_service_for(settings) -> str:
    """The service a `web:` reservation bills: Exa when configured, otherwise Firecrawl."""
    exa_key = getattr(settings, "exa_api_key", None)
    return "exa" if exa_key and exa_key.get_secret_value().strip() else "firecrawl"


def settle_unknown_outcomes(
    connection, run_id, *, web_service: str, claude_model: str = "unknown"
) -> list[str]:
    """Book each finished-but-unknown paid call of the run at its ceiling. Returns the keys."""
    rows = connection.execute(
        "SELECT reservation_id, operation, ceiling_usd FROM budget_reservations "
        "WHERE run_id=%s AND status='uncertain' ORDER BY reservation_id",
        (run_id,),
    ).fetchall()
    settled = []
    for reservation_id, operation, ceiling in rows:
        if not settles_by_estimate(operation):
            continue
        prefix = operation.split(":", 1)[0]
        service = web_service if prefix == "web" else SERVICE_FOR_PREFIX[prefix]
        note = (
            f"The call ended without a known outcome; booked at its {ceiling} USD ceiling "
            "as an estimate."
        )
        with connection.transaction():
            updated = connection.execute(
                "UPDATE budget_reservations SET status='succeeded', result=%s "
                "WHERE reservation_id=%s AND status='uncertain' RETURNING reservation_id",
                (Jsonb({"closeout": note}), reservation_id),
            ).fetchone()
            if updated is None:
                continue
            record_cost(
                connection,
                run_id,
                service,
                Decimal(ceiling),
                endpoint="unknown-outcome",
                model=claude_model if service == "claude" else None,
                request_id=operation,
                cost_basis="estimated",
            )
            trace(connection, run_id, operation, "settled:ceiling", {"ceiling_usd": str(ceiling)})
        audit(
            connection,
            BUDGET_RESERVATION,
            f"settles {operation} at its {ceiling} USD ceiling (outcome unknown)",
            component_for_operation(operation),
            run_id=run_id,
            ceiling_usd=ceiling,
        )
        settled.append(operation)
    return settled


def readiness_problems(connection, run_id, stages) -> list[str]:
    """Everything the run left open. An empty list means the next run can start."""
    problems = []
    plan_id = connection.execute("SELECT plan_id FROM runs WHERE run_id=%s", (run_id,)).fetchone()[
        0
    ]
    open_calls = connection.execute(
        "SELECT operation, status FROM budget_reservations "
        "WHERE plan_id=%s AND status IN ('started','uncertain') ORDER BY reservation_id",
        (plan_id,),
    ).fetchall()
    if open_calls:
        listed = ", ".join(f"{operation} ({status})" for operation, status in open_calls)
        problems.append(f"paid calls still open: {listed}")
    jobs = connection.execute(
        "SELECT status, count(*) FROM cell_jobs WHERE run_id=%s AND status = ANY(%s) "
        "GROUP BY status ORDER BY status",
        (run_id, list(UNFINISHED_JOB_STATUSES)),
    ).fetchall()
    if jobs:
        problems.append("jobs not finished: " + ", ".join(f"{n} {s}" for s, n in jobs))
    done = {
        row[0]
        for row in connection.execute(
            "SELECT stage FROM pipeline_stages WHERE run_id=%s", (run_id,)
        ).fetchall()
    }
    missing = [stage for stage in stages if stage not in done]
    if missing:
        problems.append("stages not finished: " + ", ".join(missing))
    return problems


def close_out_run(
    connection, run_id, stages, *, web_service: str, claude_model: str = "unknown"
) -> list[str]:
    """Settle what can be settled, then refuse to call the run finished if anything is open."""
    settled = settle_unknown_outcomes(
        connection, run_id, web_service=web_service, claude_model=claude_model
    )
    problems = readiness_problems(connection, run_id, stages)
    if problems:
        raise RunNotReady("Run is not complete: " + "; ".join(problems))
    return settled


def settle_ended_runs(connection, *, web_service: str, claude_model: str = "unknown") -> list[str]:
    """Settle unknown outcomes left by runs that already ended, so they cannot block the next."""
    ended = connection.execute(
        "SELECT DISTINCT r.run_id FROM runs r JOIN budget_reservations b USING(run_id) "
        "WHERE r.status <> 'running' AND b.status='uncertain'"
    ).fetchall()
    settled = []
    for (run_id,) in ended:
        settled += settle_unknown_outcomes(
            connection, run_id, web_service=web_service, claude_model=claude_model
        )
    return settled
