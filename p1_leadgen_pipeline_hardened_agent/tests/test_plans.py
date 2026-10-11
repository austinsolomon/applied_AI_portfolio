"""Plans, the one approval, and run state: a cell never runs twice, a business is seen once."""

from decimal import Decimal

import psycopg
import pytest

from leadgen.plans import (
    STAGE_FULL,
    STAGE_SAMPLE,
    AttemptNotInProgress,
    CellAlreadyDone,
    CellInProgress,
    EmptyPlan,
    LeadKeys,
    PlanAlreadyApproved,
    PlanCell,
    PlannedCell,
    PlanNotApproved,
    PlanNotFound,
    Run,
    RunInProgress,
    RunNotFound,
    ScrapePlan,
    approve_plan,
    attach_scraper_run,
    create_plan,
    finish_cell_attempt,
    finish_run,
    latest_plan,
    pending_cells,
    read_approval,
    read_attempts,
    read_plan,
    read_run,
    remember_lead,
    set_cell_status,
    start_cell_attempt,
    start_run,
)
from tests.crm_builders import add_lead, add_market


def planned_cell(market_id: int, city: str = "Pasadena, CA", keyword: str = "mobile massage"):
    return PlannedCell(
        market_id, city, keyword, expected_raw_leads=25, estimated_cost_usd=Decimal("0.4000")
    )


def three_cell_plan(connection: psycopg.Connection, market_name: str = "Los Angeles") -> ScrapePlan:
    market_id = add_market(connection, market_name)
    return create_plan(
        connection,
        [
            planned_cell(market_id, "Pasadena, CA", "mobile massage"),
            planned_cell(market_id, "Pasadena, CA", "in-home massage"),
            planned_cell(market_id, "Glendale, CA", "mobile massage"),
        ],
    )


def approved_running_plan(connection: psycopg.Connection) -> tuple[ScrapePlan, PlanCell, Run]:
    """A plan approved and started; returns the plan, its first cell and the run."""
    plan = three_cell_plan(connection)
    approve_plan(connection, plan.plan_id, Decimal("5.00"), "operator")
    run = start_run(connection, plan.plan_id)
    return read_plan(connection, plan.plan_id), plan.cells[0], run


# --- plans -------------------------------------------------------------------------------------


def test_a_plan_keeps_its_cells_in_sweep_order_and_totals_them(
    migrated_connection: psycopg.Connection,
) -> None:
    plan = three_cell_plan(migrated_connection)

    assert plan.status == "draft"
    assert [(cell.position, cell.city, cell.keyword) for cell in plan.cells] == [
        (1, "Pasadena, CA", "mobile massage"),
        (2, "Pasadena, CA", "in-home massage"),
        (3, "Glendale, CA", "mobile massage"),
    ]
    assert all(cell.status == "planned" and cell.apify_run_id is None for cell in plan.cells)
    assert plan.expected_raw_leads == 75
    assert plan.estimated_cost_usd == Decimal("1.2000")
    assert read_plan(migrated_connection, plan.plan_id) == plan


def test_an_empty_plan_is_refused(migrated_connection: psycopg.Connection) -> None:
    with pytest.raises(EmptyPlan):
        create_plan(migrated_connection, [])
    assert latest_plan(migrated_connection) is None


def test_the_same_cell_twice_in_one_plan_is_refused_and_nothing_is_stored(
    migrated_connection: psycopg.Connection,
) -> None:
    market_id = add_market(migrated_connection)

    with pytest.raises(psycopg.errors.UniqueViolation):
        create_plan(migrated_connection, [planned_cell(market_id), planned_cell(market_id)])

    assert migrated_connection.execute("SELECT count(*) FROM scrape_plans").fetchone() == (0,)


def test_latest_plan_is_the_newest(migrated_connection: psycopg.Connection) -> None:
    three_cell_plan(migrated_connection, "Phoenix")
    newest = three_cell_plan(migrated_connection, "Austin")

    latest = latest_plan(migrated_connection)

    assert latest is not None and latest.plan_id == newest.plan_id


def test_reading_an_unknown_plan_is_an_error(migrated_connection: psycopg.Connection) -> None:
    with pytest.raises(PlanNotFound):
        read_plan(migrated_connection, 404)


@pytest.mark.parametrize(
    "cell",
    [
        PlannedCell(0, "  ", "mobile massage", 10, Decimal("0.5")),
        PlannedCell(0, "Pasadena, CA", "", 10, Decimal("0.5")),
        PlannedCell(0, "Pasadena, CA", "mobile massage", -1, Decimal("0.5")),
        PlannedCell(0, "Pasadena, CA", "mobile massage", 10, Decimal("-0.5")),
    ],
)
def test_invalid_cell_values_are_rejected(
    migrated_connection: psycopg.Connection, cell: PlannedCell
) -> None:
    market_id = add_market(migrated_connection)

    with pytest.raises(psycopg.errors.CheckViolation):
        create_plan(
            migrated_connection,
            [
                PlannedCell(
                    market_id,
                    cell.city,
                    cell.keyword,
                    cell.expected_raw_leads,
                    cell.estimated_cost_usd,
                )
            ],
        )

    assert migrated_connection.execute("SELECT count(*) FROM scrape_plans").fetchone() == (0,)


def test_a_cell_status_outside_the_fixed_list_is_rejected(
    migrated_connection: psycopg.Connection,
) -> None:
    plan = three_cell_plan(migrated_connection)
    set_cell_status(migrated_connection, plan.cells[0].cell_id, "proceed")

    with pytest.raises(psycopg.errors.CheckViolation):
        set_cell_status(migrated_connection, plan.cells[1].cell_id, "maybe")

    statuses = [cell.status for cell in read_plan(migrated_connection, plan.plan_id).cells]
    assert statuses == ["proceed", "planned", "planned"]


def test_a_scraper_run_belongs_to_exactly_one_cell(
    migrated_connection: psycopg.Connection,
) -> None:
    plan = three_cell_plan(migrated_connection)
    attach_scraper_run(migrated_connection, plan.cells[0].cell_id, "apify_run_1")

    with pytest.raises(psycopg.errors.UniqueViolation):
        attach_scraper_run(migrated_connection, plan.cells[1].cell_id, "apify_run_1")

    assert read_plan(migrated_connection, plan.plan_id).cells[0].apify_run_id == "apify_run_1"


# --- approval ----------------------------------------------------------------------------------


def test_approving_a_plan_records_who_and_how_much_once(
    migrated_connection: psycopg.Connection,
) -> None:
    plan = three_cell_plan(migrated_connection)
    assert read_approval(migrated_connection, plan.plan_id) is None

    approval = approve_plan(migrated_connection, plan.plan_id, Decimal("5.00"), "operator")

    assert (approval.plan_id, approval.budget_usd, approval.approved_by) == (
        plan.plan_id,
        Decimal("5.00"),
        "operator",
    )
    assert read_approval(migrated_connection, plan.plan_id) == approval
    assert read_plan(migrated_connection, plan.plan_id).status == "approved"


def test_a_plan_is_approved_only_once(migrated_connection: psycopg.Connection) -> None:
    plan = three_cell_plan(migrated_connection)
    approve_plan(migrated_connection, plan.plan_id, Decimal("5.00"), "operator")

    with pytest.raises(PlanAlreadyApproved):
        approve_plan(migrated_connection, plan.plan_id, Decimal("50.00"), "someone else")

    approval = read_approval(migrated_connection, plan.plan_id)
    assert approval is not None and approval.budget_usd == Decimal("5.00")


def test_approving_an_unknown_plan_is_an_error(migrated_connection: psycopg.Connection) -> None:
    with pytest.raises(PlanNotFound):
        approve_plan(migrated_connection, 404, Decimal("5.00"), "operator")


@pytest.mark.parametrize(
    ("budget_usd", "approved_by"), [(Decimal("0"), "operator"), (Decimal("5.00"), "  ")]
)
def test_an_approval_needs_a_positive_budget_and_a_name(
    migrated_connection: psycopg.Connection, budget_usd: Decimal, approved_by: str
) -> None:
    plan = three_cell_plan(migrated_connection)

    with pytest.raises(psycopg.errors.CheckViolation):
        approve_plan(migrated_connection, plan.plan_id, budget_usd, approved_by)

    # The failed approval left the plan a draft, so it can still be approved properly.
    assert read_plan(migrated_connection, plan.plan_id).status == "draft"


# --- runs --------------------------------------------------------------------------------------


def test_only_an_approved_plan_runs(migrated_connection: psycopg.Connection) -> None:
    plan = three_cell_plan(migrated_connection)

    with pytest.raises(PlanNotApproved):
        start_run(migrated_connection, plan.plan_id)


def test_a_run_opens_running_and_closes_with_an_end_status(
    migrated_connection: psycopg.Connection,
) -> None:
    plan = three_cell_plan(migrated_connection)
    approve_plan(migrated_connection, plan.plan_id, Decimal("5.00"), "operator")

    run = start_run(migrated_connection, plan.plan_id)
    assert (run.plan_id, run.status, run.finished_at) == (plan.plan_id, "running", None)

    finished = finish_run(migrated_connection, run.run_id, "finished")
    assert finished.status == "finished" and finished.finished_at is not None
    assert read_run(migrated_connection, run.run_id) == finished


def test_a_plan_has_one_run_in_progress_at_a_time(
    migrated_connection: psycopg.Connection,
) -> None:
    plan = three_cell_plan(migrated_connection)
    approve_plan(migrated_connection, plan.plan_id, Decimal("5.00"), "operator")
    first_run = start_run(migrated_connection, plan.plan_id)

    with pytest.raises(RunInProgress):
        start_run(migrated_connection, plan.plan_id)

    # Once the first run ends, the plan can be run again (and only the unfinished cells go).
    finish_run(migrated_connection, first_run.run_id, "stopped")
    second_run = start_run(migrated_connection, plan.plan_id)
    assert second_run.run_id != first_run.run_id


def test_a_run_ends_once_and_only_with_an_end_status(
    migrated_connection: psycopg.Connection,
) -> None:
    plan = three_cell_plan(migrated_connection)
    approve_plan(migrated_connection, plan.plan_id, Decimal("5.00"), "operator")
    run = start_run(migrated_connection, plan.plan_id)

    with pytest.raises(ValueError):
        finish_run(migrated_connection, run.run_id, "running")
    finish_run(migrated_connection, run.run_id, "failed")
    with pytest.raises(RunNotFound):
        finish_run(migrated_connection, run.run_id, "finished")


# --- the run ledger ----------------------------------------------------------------------------


def test_a_cell_never_runs_twice_at_the_same_stage(
    migrated_connection: psycopg.Connection,
) -> None:
    plan, cell, run = approved_running_plan(migrated_connection)

    attempt = start_cell_attempt(migrated_connection, run.run_id, cell.cell_id, STAGE_SAMPLE)
    assert (attempt.attempt_number, attempt.status, attempt.finished_at) == (1, "started", None)
    finished = finish_cell_attempt(
        migrated_connection, attempt.attempt_id, succeeded=True, leads_found=12
    )
    assert (finished.status, finished.leads_found) == ("succeeded", 12)

    with pytest.raises(CellAlreadyDone):
        start_cell_attempt(migrated_connection, run.run_id, cell.cell_id, STAGE_SAMPLE)
    # The rule lives in the database, so no code path around the function can break it.
    with pytest.raises(psycopg.errors.UniqueViolation):
        migrated_connection.execute(
            "INSERT INTO run_ledger (run_id, cell_id, stage, attempt_number, status, finished_at) "
            "VALUES (%s, %s, 'sample', 2, 'succeeded', now())",
            (run.run_id, cell.cell_id),
        )
    # The full stage is a separate piece of work on the same cell.
    full_attempt = start_cell_attempt(migrated_connection, run.run_id, cell.cell_id, STAGE_FULL)
    assert (full_attempt.stage, full_attempt.attempt_number) == ("full", 1)


def test_a_failed_attempt_can_be_retried_and_is_numbered(
    migrated_connection: psycopg.Connection,
) -> None:
    plan, cell, run = approved_running_plan(migrated_connection)
    first = start_cell_attempt(migrated_connection, run.run_id, cell.cell_id, STAGE_SAMPLE)
    failed = finish_cell_attempt(
        migrated_connection, first.attempt_id, succeeded=False, error="actor timed out"
    )
    assert (failed.status, failed.error) == ("failed", "actor timed out")

    second = start_cell_attempt(migrated_connection, run.run_id, cell.cell_id, STAGE_SAMPLE)

    assert second.attempt_number == 2
    assert [
        (attempt.attempt_number, attempt.status)
        for attempt in read_attempts(migrated_connection, run.run_id)
    ] == [
        (1, "failed"),
        (2, "started"),
    ]


def test_two_attempts_on_a_cell_never_run_at_once(
    migrated_connection: psycopg.Connection, empty_database_url: str
) -> None:
    plan, cell, run = approved_running_plan(migrated_connection)
    start_cell_attempt(migrated_connection, run.run_id, cell.cell_id, STAGE_SAMPLE)

    # A second worker, on its own connection, tries the same cell.
    with psycopg.connect(empty_database_url, autocommit=True) as other_worker:
        with pytest.raises(CellInProgress):
            start_cell_attempt(other_worker, run.run_id, cell.cell_id, STAGE_SAMPLE)


def test_an_attempt_is_finished_once_and_a_failure_needs_a_reason(
    migrated_connection: psycopg.Connection,
) -> None:
    plan, cell, run = approved_running_plan(migrated_connection)
    attempt = start_cell_attempt(migrated_connection, run.run_id, cell.cell_id, STAGE_SAMPLE)

    with pytest.raises(ValueError):
        finish_cell_attempt(migrated_connection, attempt.attempt_id, succeeded=False)
    finish_cell_attempt(migrated_connection, attempt.attempt_id, succeeded=False, error="boom")
    with pytest.raises(AttemptNotInProgress):
        finish_cell_attempt(migrated_connection, attempt.attempt_id, succeeded=True)


def test_rerunning_a_plan_only_sees_the_unfinished_cells(
    migrated_connection: psycopg.Connection,
) -> None:
    plan, first_cell, run = approved_running_plan(migrated_connection)
    second_cell, third_cell = plan.cells[1], plan.cells[2]
    done = start_cell_attempt(migrated_connection, run.run_id, first_cell.cell_id, STAGE_SAMPLE)
    finish_cell_attempt(migrated_connection, done.attempt_id, succeeded=True, leads_found=9)
    failed = start_cell_attempt(migrated_connection, run.run_id, second_cell.cell_id, STAGE_SAMPLE)
    finish_cell_attempt(migrated_connection, failed.attempt_id, succeeded=False, error="timeout")
    finish_run(migrated_connection, run.run_id, "failed")

    start_run(migrated_connection, plan.plan_id)

    pending_ids = [
        cell.cell_id for cell in pending_cells(migrated_connection, plan.plan_id, STAGE_SAMPLE)
    ]
    assert pending_ids == [second_cell.cell_id, third_cell.cell_id]
    # Nothing has been scraped in full yet, so every cell is pending at that stage.
    assert len(pending_cells(migrated_connection, plan.plan_id, STAGE_FULL)) == 3


def test_an_unknown_stage_is_refused(migrated_connection: psycopg.Connection) -> None:
    plan, cell, run = approved_running_plan(migrated_connection)

    with pytest.raises(ValueError):
        start_cell_attempt(migrated_connection, run.run_id, cell.cell_id, "rehearsal")
    with pytest.raises(ValueError):
        pending_cells(migrated_connection, plan.plan_id, "rehearsal")


# --- seen leads --------------------------------------------------------------------------------


def test_a_business_is_new_once_whatever_its_phone_formatting(
    migrated_connection: psycopg.Connection,
) -> None:
    plan, cell, run = approved_running_plan(migrated_connection)

    assert remember_lead(migrated_connection, run.run_id, LeadKeys(phone="(310) 555-0142"))
    assert not remember_lead(migrated_connection, run.run_id, LeadKeys(phone="+1 310.555.0142"))
    assert remember_lead(migrated_connection, run.run_id, LeadKeys(google_place_id="place_1"))
    assert not remember_lead(
        migrated_connection, run.run_id, LeadKeys(google_place_id="place_1", phone="555-0199")
    )
    assert remember_lead(migrated_connection, run.run_id, LeadKeys(email="Hi@Cedar.example"))
    assert not remember_lead(migrated_connection, run.run_id, LeadKeys(email=" hi@cedar.example"))

    assert migrated_connection.execute("SELECT count(*) FROM seen_leads").fetchone() == (3,)


def test_a_domain_is_seen_unless_it_is_a_shared_host(
    migrated_connection: psycopg.Connection,
) -> None:
    plan, cell, run = approved_running_plan(migrated_connection)

    assert remember_lead(
        migrated_connection, run.run_id, LeadKeys(website="https://www.cedar.example/a")
    )
    assert not remember_lead(migrated_connection, run.run_id, LeadKeys(website="cedar.example/b"))

    shared_hosts = frozenset({"linktr.ee"})
    assert remember_lead(
        migrated_connection, run.run_id, LeadKeys(website="https://linktr.ee/one"), shared_hosts
    )
    assert remember_lead(
        migrated_connection, run.run_id, LeadKeys(website="https://linktr.ee/two"), shared_hosts
    )


def test_a_business_without_any_key_cannot_be_remembered(
    migrated_connection: psycopg.Connection,
) -> None:
    plan, cell, run = approved_running_plan(migrated_connection)

    with pytest.raises(psycopg.errors.CheckViolation):
        remember_lead(migrated_connection, run.run_id, LeadKeys())
    with pytest.raises(psycopg.errors.CheckViolation):
        remember_lead(migrated_connection, run.run_id, LeadKeys(website="   ", email="  "))


@pytest.mark.parametrize(
    "keys",
    [
        LeadKeys(
            phone="+1 (310) 555-0142",
            email="Hello@Cedar.Example ",
            website="https://www.Cedar.example:8080/x?y#z",
        ),
        LeadKeys(phone="310 555 0143", website="cedar.example/about"),
        LeadKeys(email="a@b.example", website="  "),
    ],
)
def test_seen_leads_normalize_keys_exactly_like_the_crm(
    migrated_connection: psycopg.Connection, keys: LeadKeys
) -> None:
    plan, cell, run = approved_running_plan(migrated_connection)
    market_id = plan.cells[0].market_id
    lead_id = add_lead(
        migrated_connection, market_id, phone=keys.phone, email=keys.email, website=keys.website
    )
    remember_lead(migrated_connection, run.run_id, keys)

    crm_keys = migrated_connection.execute(
        "SELECT phone_normalized, email_normalized, website_domain FROM crm_leads "
        "WHERE lead_id = %s",
        (lead_id,),
    ).fetchone()
    seen_keys = migrated_connection.execute(
        "SELECT phone_normalized, email_normalized, website_domain FROM seen_leads"
    ).fetchone()

    assert seen_keys == crm_keys
