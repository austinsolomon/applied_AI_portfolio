"""Durable, serialized plan reservations, including spend from every retry of a plan."""

from contextlib import contextmanager
from decimal import Decimal
from time import monotonic

from psycopg.types.json import Jsonb


class BudgetBlocked(Exception):
    """The approved plan cannot authorize this operation."""


def plan_spend(connection, plan_id):
    return connection.execute(
        "SELECT coalesce(sum(cost_usd),0) FROM cost_ledger JOIN runs USING(run_id) "
        "WHERE plan_id=%s",
        (plan_id,),
    ).fetchone()[0]


def reserve(connection, run_id, operation, ceiling, *, cell_id=None):
    ceiling = Decimal(str(ceiling))
    if not ceiling.is_finite() or ceiling < 0:
        raise BudgetBlocked("Invalid cost ceiling")
    with connection.transaction():
        approval = connection.execute(
            "SELECT a.plan_id,a.budget_usd FROM approvals a JOIN runs r USING(plan_id) "
            "JOIN scrape_plans p USING(plan_id) WHERE r.run_id=%s AND r.status='running' "
            "AND p.status='approved' FOR UPDATE OF a",
            (run_id,),
        ).fetchone()
        if approval is None:
            raise BudgetBlocked("No active run with an approved plan")
        plan_id, budget = approval
        if (
            cell_id is not None
            and not connection.execute(
                "SELECT 1 FROM scrape_plan_cells WHERE cell_id=%s AND plan_id=%s",
                (cell_id, plan_id),
            ).fetchone()
        ):
            raise BudgetBlocked("Cell is outside the approved plan")
        existing = connection.execute(
            "SELECT status FROM budget_reservations WHERE plan_id=%s AND operation=%s",
            (plan_id, operation),
        ).fetchone()
        if existing and existing[0] != "rejected":
            raise BudgetBlocked(f"Operation {operation} already {existing[0]}; will not repeat")
        held = connection.execute(
            "SELECT coalesce(sum(ceiling_usd),0) FROM budget_reservations "
            "WHERE plan_id=%s AND status IN ('started','uncertain')",
            (plan_id,),
        ).fetchone()[0]
        if operation.startswith(("judge:", "sdk:")):
            model_spend = connection.execute(
                "SELECT coalesce(sum(cost_usd),0) FROM cost_ledger JOIN runs USING(run_id) "
                "WHERE plan_id=%s AND service='claude'",
                (plan_id,),
            ).fetchone()[0]
            model_held = connection.execute(
                "SELECT coalesce(sum(ceiling_usd),0) FROM budget_reservations "
                "WHERE plan_id=%s AND status IN ('started','uncertain') "
                "AND (operation LIKE 'judge:%%' OR operation LIKE 'sdk:%%')",
                (plan_id,),
            ).fetchone()[0]
            if model_spend + model_held + ceiling > Decimal("0.75"):
                raise BudgetBlocked("Anthropic allowance exhausted (0.75 USD per plan)")
        if plan_spend(connection, plan_id) + held + ceiling > budget:
            raise BudgetBlocked("Operation would exceed the approved plan budget")
        return connection.execute(
            "INSERT INTO budget_reservations(plan_id,run_id,operation,ceiling_usd) "
            "VALUES (%s,%s,%s,%s) ON CONFLICT(plan_id,operation) DO UPDATE "
            "SET status='started',ceiling_usd=EXCLUDED.ceiling_usd,run_id=EXCLUDED.run_id "
            "RETURNING reservation_id",
            (plan_id, run_id, operation, ceiling),
        ).fetchone()[0]


@contextmanager
def paid_operation(connection, run_id, operation, ceiling, *, cell_id=None):
    reservation = reserve(connection, run_id, operation, ceiling, cell_id=cell_id)
    started = monotonic()
    outcome = "uncertain"
    try:
        yield
        outcome = "succeeded"
    finally:
        with connection.transaction():
            connection.execute(
                "UPDATE budget_reservations SET status=%s WHERE reservation_id=%s",
                (outcome, reservation),
            )
            connection.execute(
                "INSERT INTO traces(run_id,operation,outcome,latency_ms) VALUES (%s,%s,%s,%s)",
                (run_id, operation, outcome, int((monotonic() - started) * 1000)),
            )


def record_cost(
    connection,
    run_id,
    service,
    cost,
    *,
    endpoint=None,
    model=None,
    units=0,
    request_id=None,
    input_tokens=0,
    output_tokens=0,
    cost_basis="provider_reported",
):
    with connection.transaction():
        # The run row serializes call numbers across workers without holding a lock over HTTP.
        connection.execute("SELECT 1 FROM runs WHERE run_id=%s FOR UPDATE", (run_id,))
        connection.execute(
            "INSERT INTO cost_ledger(run_id,service,call_number,cost_usd,endpoint,model,"
            "provider_units,request_id,input_tokens,output_tokens,cost_basis) "
            "SELECT %s,%s,coalesce(max(call_number),0)+1,%s,%s,%s,%s,%s,%s,%s,%s "
            "FROM cost_ledger WHERE run_id=%s",
            (
                run_id,
                service,
                cost,
                endpoint,
                model,
                units,
                request_id,
                input_tokens,
                output_tokens,
                cost_basis,
                run_id,
            ),
        )


def trace(connection, run_id, operation, outcome, details=None):
    connection.execute(
        "INSERT INTO traces(run_id,operation,outcome,details) VALUES (%s,%s,%s,%s)",
        (run_id, operation, outcome, Jsonb(details or {})),
    )


def reconcile_operation(connection, reservation_id, outcome, note):
    """Operator-only reconciliation after checking the provider's run/usage records."""
    if outcome not in {"succeeded", "rejected"} or not note.strip():
        raise ValueError("Reconciliation needs a known outcome and a provider evidence note")
    with connection.transaction():
        row = connection.execute(
            "UPDATE budget_reservations SET status=%s,result=%s "
            "WHERE reservation_id=%s AND status='uncertain' RETURNING run_id,operation",
            (outcome, Jsonb({"reconciliation": note}), reservation_id),
        ).fetchone()
        if row is None:
            raise ValueError("Reservation is not uncertain")
        trace(connection, row[0], row[1], f"reconciled:{outcome}", {"note": note})
