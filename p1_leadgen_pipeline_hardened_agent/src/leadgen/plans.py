"""Scrape plans, the one human approval, and run state.

A plan is a list of cells (market × city × keyword) in sweep order, each with an expected yield
and a cost estimate. A human approves a plan once, with a dollar budget. A run executes an
approved plan; every attempt to work a cell is written to `run_ledger`, and the database itself
refuses a second success or a second in-flight attempt for the same cell and stage, so re-running
a plan never repeats work. `seen_leads` remembers every business any run has found, keyed like the
CRM, so the same business is never counted as new twice.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

import psycopg
from psycopg.rows import class_row

STAGE_SAMPLE = "sample"
STAGE_FULL = "full"
STAGES = (STAGE_SAMPLE, STAGE_FULL)

RUN_FINISHED = "finished"
RUN_FAILED = "failed"
RUN_STOPPED = "stopped"
RUN_END_STATUSES = (RUN_FINISHED, RUN_FAILED, RUN_STOPPED)


class PlanError(Exception):
    """A plan, approval or run state rule was broken."""


class EmptyPlan(PlanError):
    """A plan needs at least one cell."""


class PlanNotFound(PlanError):
    pass


class PlanAlreadyApproved(PlanError):
    """A plan is approved exactly once; a new budget needs a new plan."""


class PlanNotApproved(PlanError):
    """Only approved plans run."""


class RunInProgress(PlanError):
    """A plan has at most one run in progress."""


class RunNotFound(PlanError):
    pass


class CellAlreadyDone(PlanError):
    """The cell already has a succeeded attempt at this stage: it never runs twice."""


class CellInProgress(PlanError):
    """Another attempt on this cell and stage has started and not finished."""


class AttemptNotInProgress(PlanError):
    """Only an attempt with status `started` can be finished."""


@dataclass(frozen=True)
class PlannedCell:
    """One cell as the planner proposes it, before it is stored."""

    market_id: int
    city: str
    keyword: str
    expected_raw_leads: int
    estimated_cost_usd: Decimal


@dataclass(frozen=True)
class PlanCell:
    """One stored cell of a plan."""

    cell_id: int
    plan_id: int
    position: int
    market_id: int
    city: str
    keyword: str
    expected_raw_leads: int
    estimated_cost_usd: Decimal
    status: str
    apify_run_id: str | None


@dataclass(frozen=True)
class ScrapePlan:
    plan_id: int
    status: str
    created_at: datetime
    cells: tuple[PlanCell, ...]

    @property
    def estimated_cost_usd(self) -> Decimal:
        return sum((cell.estimated_cost_usd for cell in self.cells), Decimal(0))

    @property
    def expected_raw_leads(self) -> int:
        return sum(cell.expected_raw_leads for cell in self.cells)


@dataclass(frozen=True)
class Approval:
    approval_id: int
    plan_id: int
    budget_usd: Decimal
    approved_by: str
    approved_at: datetime


@dataclass(frozen=True)
class Run:
    run_id: UUID
    plan_id: int
    status: str
    started_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True)
class CellAttempt:
    attempt_id: int
    run_id: UUID
    cell_id: int
    stage: str
    attempt_number: int
    status: str
    started_at: datetime
    finished_at: datetime | None
    leads_found: int | None
    error: str | None


@dataclass(frozen=True)
class LeadKeys:
    """The identifying details of one sourced business. Any one of them is enough."""

    google_place_id: str | None = None
    phone: str | None = None
    email: str | None = None
    website: str | None = None


CELL_COLUMNS = (
    "cell_id, plan_id, position, market_id, city, keyword, expected_raw_leads, "
    "estimated_cost_usd, status, apify_run_id"
)
ATTEMPT_COLUMNS = (
    "attempt_id, run_id, cell_id, stage, attempt_number, status, started_at, finished_at, "
    "leads_found, error"
)
RUN_COLUMNS = "run_id, plan_id, status, started_at, finished_at"

# The same expression as the CRM's generated `website_domain` column, so a domain match here
# means a domain match there.
WEBSITE_DOMAIN_SQL = """
    nullif(
        regexp_replace(
            regexp_replace(lower(btrim(%(website)s)), '^[a-z][a-z0-9+.-]*://', ''),
            '^www\\.|[/:?#].*$', '', 'g'
        ),
        ''
    )
"""


# --- plans -------------------------------------------------------------------------------------


def create_plan(connection: psycopg.Connection, cells: Iterable[PlannedCell]) -> ScrapePlan:
    """Store a draft plan with its cells in sweep order, in one transaction."""
    planned_cells = list(cells)
    if not planned_cells:
        raise EmptyPlan("a plan needs at least one cell")
    with connection.transaction():
        plan_row = connection.execute(
            "INSERT INTO scrape_plans DEFAULT VALUES RETURNING plan_id"
        ).fetchone()
        assert plan_row is not None
        plan_id: int = plan_row[0]
        connection.cursor().executemany(
            """
            INSERT INTO scrape_plan_cells
                (plan_id, position, market_id, city, keyword, expected_raw_leads,
                 estimated_cost_usd)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (
                    plan_id,
                    position,
                    cell.market_id,
                    cell.city,
                    cell.keyword,
                    cell.expected_raw_leads,
                    cell.estimated_cost_usd,
                )
                for position, cell in enumerate(planned_cells, start=1)
            ],
        )
    return read_plan(connection, plan_id)


def read_plan(connection: psycopg.Connection, plan_id: int) -> ScrapePlan:
    plan_row = connection.execute(
        "SELECT plan_id, status, created_at FROM scrape_plans WHERE plan_id = %s", (plan_id,)
    ).fetchone()
    if plan_row is None:
        raise PlanNotFound(f"no plan with id {plan_id}")
    with connection.cursor(row_factory=class_row(PlanCell)) as cursor:
        cells = cursor.execute(
            f"SELECT {CELL_COLUMNS} FROM scrape_plan_cells WHERE plan_id = %s ORDER BY position",
            (plan_id,),
        ).fetchall()
    return ScrapePlan(plan_row[0], plan_row[1], plan_row[2], tuple(cells))


def latest_plan(connection: psycopg.Connection) -> ScrapePlan | None:
    """The most recently created plan, whatever its status."""
    row = connection.execute("SELECT max(plan_id) FROM scrape_plans").fetchone()
    if row is None or row[0] is None:
        return None
    return read_plan(connection, row[0])


def set_cell_status(connection: psycopg.Connection, cell_id: int, status: str) -> None:
    """Record a cell's sample verdict or completion; the database rejects unknown statuses."""
    connection.execute(
        "UPDATE scrape_plan_cells SET status = %s WHERE cell_id = %s", (status, cell_id)
    )


def attach_scraper_run(connection: psycopg.Connection, cell_id: int, apify_run_id: str) -> None:
    """Attribute a scraper run to the cell it was started for."""
    connection.execute(
        "UPDATE scrape_plan_cells SET apify_run_id = %s WHERE cell_id = %s",
        (apify_run_id, cell_id),
    )


# --- approval ----------------------------------------------------------------------------------


def approve_plan(
    connection: psycopg.Connection, plan_id: int, budget_usd: Decimal, approved_by: str
) -> Approval:
    """Record the one human decision: this plan may run, up to `budget_usd`."""
    with connection.transaction():
        plan_row = connection.execute(
            "SELECT status FROM scrape_plans WHERE plan_id = %s FOR UPDATE", (plan_id,)
        ).fetchone()
        if plan_row is None:
            raise PlanNotFound(f"no plan with id {plan_id}")
        if plan_row[0] != "draft":
            raise PlanAlreadyApproved(f"plan {plan_id} is {plan_row[0]}, not a draft")
        with connection.cursor(row_factory=class_row(Approval)) as cursor:
            approval = cursor.execute(
                """
                INSERT INTO approvals (plan_id, budget_usd, approved_by)
                VALUES (%s, %s, %s)
                RETURNING approval_id, plan_id, budget_usd, approved_by, approved_at
                """,
                (plan_id, budget_usd, approved_by),
            ).fetchone()
        connection.execute(
            "UPDATE scrape_plans SET status = 'approved' WHERE plan_id = %s", (plan_id,)
        )
    assert approval is not None
    return approval


def read_approval(connection: psycopg.Connection, plan_id: int) -> Approval | None:
    with connection.cursor(row_factory=class_row(Approval)) as cursor:
        return cursor.execute(
            "SELECT approval_id, plan_id, budget_usd, approved_by, approved_at FROM approvals "
            "WHERE plan_id = %s",
            (plan_id,),
        ).fetchone()


# --- runs --------------------------------------------------------------------------------------


def start_run(connection: psycopg.Connection, plan_id: int) -> Run:
    """Open a run of an approved plan. A plan has at most one run in progress."""
    plan_row = connection.execute(
        "SELECT status FROM scrape_plans WHERE plan_id = %s", (plan_id,)
    ).fetchone()
    if plan_row is None:
        raise PlanNotFound(f"no plan with id {plan_id}")
    if plan_row[0] != "approved":
        raise PlanNotApproved(f"plan {plan_id} is {plan_row[0]}; only approved plans run")
    try:
        with connection.cursor(row_factory=class_row(Run)) as cursor:
            run = cursor.execute(
                f"INSERT INTO runs (plan_id) VALUES (%s) RETURNING {RUN_COLUMNS}", (plan_id,)
            ).fetchone()
    except psycopg.errors.UniqueViolation as error:
        raise RunInProgress(f"plan {plan_id} already has a run in progress") from error
    assert run is not None
    return run


def finish_run(connection: psycopg.Connection, run_id: UUID, status: str) -> Run:
    """Close a run as finished, failed or stopped."""
    if status not in RUN_END_STATUSES:
        raise ValueError(f"a run ends as one of {RUN_END_STATUSES}, not {status!r}")
    with connection.cursor(row_factory=class_row(Run)) as cursor:
        run = cursor.execute(
            f"""
            UPDATE runs SET status = %s, finished_at = now()
            WHERE run_id = %s AND status = 'running'
            RETURNING {RUN_COLUMNS}
            """,
            (status, run_id),
        ).fetchone()
    if run is None:
        raise RunNotFound(f"no run {run_id} is in progress")
    return run


def read_run(connection: psycopg.Connection, run_id: UUID) -> Run:
    with connection.cursor(row_factory=class_row(Run)) as cursor:
        run = cursor.execute(
            f"SELECT {RUN_COLUMNS} FROM runs WHERE run_id = %s", (run_id,)
        ).fetchone()
    if run is None:
        raise RunNotFound(f"no run with id {run_id}")
    return run


# --- the run ledger ----------------------------------------------------------------------------


def start_cell_attempt(
    connection: psycopg.Connection, run_id: UUID, cell_id: int, stage: str
) -> CellAttempt:
    """Open an attempt on a cell at a stage, numbered after the attempts before it.

    Refused when the cell already succeeded at this stage (it never runs twice) or when another
    attempt is in flight. Both rules are unique indexes, so they hold across processes.
    """
    if stage not in STAGES:
        raise ValueError(f"stage is one of {STAGES}, not {stage!r}")
    try:
        with connection.cursor(row_factory=class_row(CellAttempt)) as cursor:
            attempt = cursor.execute(
                f"""
                INSERT INTO run_ledger (run_id, cell_id, stage, attempt_number)
                SELECT %(run_id)s, %(cell_id)s, %(stage)s, coalesce(max(attempt_number), 0) + 1
                FROM run_ledger
                WHERE cell_id = %(cell_id)s AND stage = %(stage)s
                RETURNING {ATTEMPT_COLUMNS}
                """,
                {"run_id": run_id, "cell_id": cell_id, "stage": stage},
            ).fetchone()
    except psycopg.errors.UniqueViolation as error:
        if _cell_succeeded(connection, cell_id, stage):
            raise CellAlreadyDone(f"cell {cell_id} already succeeded at {stage}") from error
        raise CellInProgress(f"cell {cell_id} has an attempt in flight at {stage}") from error
    assert attempt is not None
    return attempt


def _cell_succeeded(connection: psycopg.Connection, cell_id: int, stage: str) -> bool:
    row = connection.execute(
        "SELECT EXISTS (SELECT 1 FROM run_ledger WHERE cell_id = %s AND stage = %s "
        "AND status = 'succeeded')",
        (cell_id, stage),
    ).fetchone()
    return bool(row and row[0])


def finish_cell_attempt(
    connection: psycopg.Connection,
    attempt_id: int,
    *,
    succeeded: bool,
    leads_found: int | None = None,
    error: str | None = None,
) -> CellAttempt:
    """Close an attempt. A failure needs an error message; a success is final for the cell."""
    if not succeeded and not error:
        raise ValueError("a failed attempt needs an error message")
    with connection.cursor(row_factory=class_row(CellAttempt)) as cursor:
        attempt = cursor.execute(
            f"""
            UPDATE run_ledger
            SET status = %s, finished_at = now(), leads_found = %s, error = %s
            WHERE attempt_id = %s AND status = 'started'
            RETURNING {ATTEMPT_COLUMNS}
            """,
            ("succeeded" if succeeded else "failed", leads_found, error, attempt_id),
        ).fetchone()
    if attempt is None:
        raise AttemptNotInProgress(f"attempt {attempt_id} is not in progress")
    return attempt


def read_attempts(connection: psycopg.Connection, run_id: UUID) -> list[CellAttempt]:
    """Every attempt a run made, oldest first."""
    with connection.cursor(row_factory=class_row(CellAttempt)) as cursor:
        return cursor.execute(
            f"SELECT {ATTEMPT_COLUMNS} FROM run_ledger WHERE run_id = %s ORDER BY attempt_id",
            (run_id,),
        ).fetchall()


def pending_cells(connection: psycopg.Connection, plan_id: int, stage: str) -> list[PlanCell]:
    """The plan's cells with no succeeded attempt at `stage`, in sweep order.

    This is what makes re-running a plan free of repeat work: a second run only sees the cells
    the first one did not finish. Callers still choose cells by status (for example, only
    `proceed` cells for the full stage).
    """
    if stage not in STAGES:
        raise ValueError(f"stage is one of {STAGES}, not {stage!r}")
    with connection.cursor(row_factory=class_row(PlanCell)) as cursor:
        return cursor.execute(
            f"""
            SELECT {CELL_COLUMNS}
            FROM scrape_plan_cells AS cell
            WHERE plan_id = %(plan_id)s
              AND NOT EXISTS (
                  SELECT 1 FROM run_ledger
                  WHERE run_ledger.cell_id = cell.cell_id
                    AND run_ledger.stage = %(stage)s
                    AND run_ledger.status = 'succeeded'
              )
            ORDER BY position
            """,
            {"plan_id": plan_id, "stage": stage},
        ).fetchall()


# --- seen leads --------------------------------------------------------------------------------


def remember_lead(
    connection: psycopg.Connection,
    run_id: UUID,
    keys: LeadKeys,
    shared_hosts: frozenset[str] = frozenset(),
) -> bool:
    """Record a sourced business; return True when no earlier run had seen it.

    Seen means a match on Google place ID, normalized phone or normalized email (unique in
    `seen_leads` as in the CRM), or on website domain unless the domain is a shared host such as
    a link-in-bio site, where many businesses share one domain.
    """
    if keys.website is not None:
        domain_row = connection.execute(
            f"""
            SELECT domain, EXISTS (SELECT 1 FROM seen_leads WHERE website_domain = domain)
            FROM (SELECT {WEBSITE_DOMAIN_SQL} AS domain) AS normalized
            """,
            {"website": keys.website},
        ).fetchone()
        assert domain_row is not None
        domain, domain_seen = domain_row
        if domain_seen and domain not in shared_hosts:
            return False
    # Without a conflict target, DO NOTHING covers every unique index on the table.
    inserted = connection.execute(
        """
        INSERT INTO seen_leads (first_seen_run_id, google_place_id, phone, email, website)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
        """,
        (run_id, keys.google_place_id, keys.phone, keys.email, keys.website),
    ).rowcount
    return inserted == 1
