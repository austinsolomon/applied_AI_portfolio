"""Job queue and worker pool: two workers never take the same job; a failing job retries with a
delay, then gives up at the attempt limit."""

import asyncio
from datetime import timedelta
from decimal import Decimal

import psycopg
import pytest

from leadgen.job_queue import (
    JOB_FAILED,
    JOB_QUEUED,
    JOB_RUNNING,
    JOB_SUCCEEDED,
    Job,
    JobNotRunning,
    JobSpec,
    RetryPolicy,
    claim_job,
    complete_job,
    enqueue_jobs,
    list_jobs,
    queue_counts,
    read_job,
    release_job,
    requeue_stale_jobs,
    run_worker_pool,
)
from leadgen.plans import PlannedCell, approve_plan, create_plan, start_run
from tests.crm_builders import add_market

KIND = "qualify"
FAST_RETRIES = RetryPolicy(base_delay_seconds=0.01, max_delay_seconds=0.05)


def running_run(connection: psycopg.Connection):
    market_id = add_market(connection)
    plan = create_plan(
        connection,
        [PlannedCell(market_id, "Pasadena, CA", "mobile massage", 25, Decimal("0.4"))],
    )
    approve_plan(connection, plan.plan_id, Decimal("5.00"), "operator")
    return start_run(connection, plan.plan_id), plan.cells[0]


def lead_jobs(count: int, max_attempts: int = 3) -> list[JobSpec]:
    return [
        JobSpec(f"raw_lead:{number}", {"raw_lead_id": number}, max_attempts=max_attempts)
        for number in range(1, count + 1)
    ]


# --- enqueue ----------------------------------------------------------------------------------


def test_enqueuing_the_same_work_again_adds_nothing(migrated_connection: psycopg.Connection):
    run, cell = running_run(migrated_connection)
    specs = [JobSpec("cell:1", {"city": "Pasadena, CA"}, cell_id=cell.cell_id)] + lead_jobs(2)

    assert enqueue_jobs(migrated_connection, run.run_id, KIND, specs) == 3
    assert enqueue_jobs(migrated_connection, run.run_id, KIND, specs) == 0
    # The same key under another kind is different work.
    assert enqueue_jobs(migrated_connection, run.run_id, "sample", specs[:1]) == 1

    jobs = list_jobs(migrated_connection, run.run_id)
    assert [(job.kind, job.job_key, job.status, job.attempts) for job in jobs] == [
        (KIND, "cell:1", JOB_QUEUED, 0),
        (KIND, "raw_lead:1", JOB_QUEUED, 0),
        (KIND, "raw_lead:2", JOB_QUEUED, 0),
        ("sample", "cell:1", JOB_QUEUED, 0),
    ]
    assert jobs[0].cell_id == cell.cell_id
    assert jobs[0].payload == {"city": "Pasadena, CA"}
    assert queue_counts(migrated_connection, run.run_id) == {
        JOB_QUEUED: 4,
        JOB_RUNNING: 0,
        JOB_SUCCEEDED: 0,
        JOB_FAILED: 0,
    }


# --- claim: SKIP LOCKED ------------------------------------------------------------------------


def test_a_claim_held_open_in_one_transaction_is_skipped_by_another_worker(
    migrated_connection: psycopg.Connection, empty_database_url: str
):
    """The real proof of SKIP LOCKED: worker A has claimed but not committed, and worker B,
    instead of blocking on A's row, takes the next one."""
    run, _ = running_run(migrated_connection)
    enqueue_jobs(migrated_connection, run.run_id, KIND, lead_jobs(2))

    with psycopg.connect(empty_database_url, autocommit=True) as second_worker_connection:
        with migrated_connection.transaction():
            first = claim_job(migrated_connection, "worker-a", run_id=run.run_id)
            assert first is not None
            second_worker_connection.execute("SET lock_timeout = '2s'")
            second = claim_job(second_worker_connection, "worker-b", run_id=run.run_id)
            assert second is not None
            assert second.job_id != first.job_id
            assert claim_job(second_worker_connection, "worker-b", run_id=run.run_id) is None

    first_after = read_job(migrated_connection, first.job_id)
    assert (first_after.status, first_after.attempts, first_after.locked_by) == (
        JOB_RUNNING,
        1,
        "worker-a",
    )


def test_claims_respect_run_kind_and_availability(migrated_connection: psycopg.Connection):
    run, _ = running_run(migrated_connection)
    enqueue_jobs(migrated_connection, run.run_id, "sample", [JobSpec("cell:1")])
    enqueue_jobs(migrated_connection, run.run_id, KIND, [JobSpec("raw_lead:1")])

    assert claim_job(migrated_connection, "w", kinds=["full"]) is None
    sample = claim_job(migrated_connection, "w", kinds=["sample", "full"])
    assert sample is not None and sample.kind == "sample"
    assert claim_job(migrated_connection, "w", kinds=["sample", "full"]) is None

    # A job put back with a delay is invisible until the delay has passed.
    qualify = claim_job(migrated_connection, "w", run_id=run.run_id)
    assert qualify is not None and qualify.kind == KIND
    released = release_job(
        migrated_connection, qualify, "timeout", RetryPolicy(base_delay_seconds=30)
    )
    assert released.status == JOB_QUEUED
    assert claim_job(migrated_connection, "w", run_id=run.run_id) is None


# --- retries and the attempt limit -------------------------------------------------------------


def test_a_failed_attempt_requeues_with_a_delay_then_fails_for_good(
    migrated_connection: psycopg.Connection,
):
    run, _ = running_run(migrated_connection)
    enqueue_jobs(migrated_connection, run.run_id, KIND, lead_jobs(1, max_attempts=2))
    slow_retries = RetryPolicy(base_delay_seconds=30, max_delay_seconds=60)

    first = claim_job(migrated_connection, "w", run_id=run.run_id)
    assert first is not None and first.attempts == 1
    retried = release_job(migrated_connection, first, "503 upstream", slow_retries)
    assert (retried.status, retried.last_error, retried.locked_by) == (
        JOB_QUEUED,
        "503 upstream",
        None,
    )
    assert retried.available_at - first.locked_at >= timedelta(seconds=15)
    assert retried.finished_at is None

    migrated_connection.execute(
        "UPDATE cell_jobs SET available_at = now() WHERE job_id = %s", (first.job_id,)
    )
    second = claim_job(migrated_connection, "w", run_id=run.run_id)
    assert second is not None and second.job_id == first.job_id and second.attempts == 2
    failed = release_job(migrated_connection, second, "503 again", slow_retries)
    assert (failed.status, failed.attempts, failed.last_error) == (JOB_FAILED, 2, "503 again")
    assert failed.finished_at is not None and failed.locked_by == "w"
    assert claim_job(migrated_connection, "w", run_id=run.run_id) is None


def test_only_a_running_job_can_be_completed_or_released(
    migrated_connection: psycopg.Connection,
):
    run, _ = running_run(migrated_connection)
    enqueue_jobs(migrated_connection, run.run_id, KIND, lead_jobs(1))
    queued = list_jobs(migrated_connection, run.run_id)[0]

    with pytest.raises(JobNotRunning):
        complete_job(migrated_connection, queued)
    with pytest.raises(JobNotRunning):
        release_job(migrated_connection, queued, "never ran")

    claimed = claim_job(migrated_connection, "w")
    assert claimed is not None
    done = complete_job(migrated_connection, claimed, {"verdict": "qualified"})
    assert (done.status, done.result) == (JOB_SUCCEEDED, {"verdict": "qualified"})
    with pytest.raises(JobNotRunning):
        complete_job(migrated_connection, claimed)


def test_retry_delays_grow_exponentially_with_jitter_and_a_cap():
    policy = RetryPolicy(base_delay_seconds=1.0, max_delay_seconds=10.0)
    lowest = [policy.delay_seconds(attempt, lambda: 0.0) for attempt in (1, 2, 3, 4, 5)]
    highest = [policy.delay_seconds(attempt, lambda: 1.0) for attempt in (1, 2, 3, 4, 5)]
    assert lowest == [0.5, 1.0, 2.0, 4.0, 5.0]
    assert highest == [1.0, 2.0, 4.0, 8.0, 10.0]
    jittered = {round(policy.delay_seconds(3), 6) for _ in range(20)}
    assert len(jittered) > 1 and all(2.0 <= delay <= 4.0 for delay in jittered)
    with pytest.raises(ValueError):
        policy.delay_seconds(0)


def test_a_silent_worker_gives_its_job_back(migrated_connection: psycopg.Connection):
    run, _ = running_run(migrated_connection)
    enqueue_jobs(migrated_connection, run.run_id, KIND, lead_jobs(2, max_attempts=1))
    stuck = claim_job(migrated_connection, "crashed-worker")
    fresh = claim_job(migrated_connection, "live-worker")
    assert stuck is not None and fresh is not None
    migrated_connection.execute(
        "UPDATE cell_jobs SET locked_at = now() - interval '1 hour' WHERE job_id = %s",
        (stuck.job_id,),
    )

    assert requeue_stale_jobs(migrated_connection, timedelta(minutes=10)) == 0
    # At its attempt limit a silent job fails instead of looping forever …
    assert read_job(migrated_connection, stuck.job_id).status == JOB_FAILED
    assert read_job(migrated_connection, fresh.job_id).status == JOB_RUNNING

    # … and below the limit it goes back in the queue, attempt counted.
    migrated_connection.execute(
        "UPDATE cell_jobs SET locked_at = now() - interval '1 hour', max_attempts = 3 "
        "WHERE job_id = %s",
        (fresh.job_id,),
    )
    assert requeue_stale_jobs(migrated_connection, timedelta(minutes=10)) == 1
    requeued = read_job(migrated_connection, fresh.job_id)
    assert (requeued.status, requeued.attempts, requeued.locked_by) == (JOB_QUEUED, 1, None)


# --- the worker pool ---------------------------------------------------------------------------


def test_workers_share_the_queue_without_overlap_and_within_the_concurrency_cap(
    migrated_connection: psycopg.Connection, empty_database_url: str
):
    run, _ = running_run(migrated_connection)
    enqueue_jobs(migrated_connection, run.run_id, KIND, lead_jobs(12))
    in_flight = 0
    peak_in_flight = 0
    handled: list[tuple[int, str]] = []

    async def record_and_wait(job: Job) -> dict:
        nonlocal in_flight, peak_in_flight
        in_flight += 1
        peak_in_flight = max(peak_in_flight, in_flight)
        handled.append((job.job_id, job.locked_by or ""))
        await asyncio.sleep(0.05)
        in_flight -= 1
        return {"raw_lead_id": job.payload["raw_lead_id"]}

    report = asyncio.run(
        run_worker_pool(
            empty_database_url,
            record_and_wait,
            run_id=run.run_id,
            concurrency=3,
            poll_interval_seconds=0.02,
            worker_name_prefix="pool",
        )
    )

    assert (report.succeeded, report.retried, report.failed) == (12, 0, 0)
    job_ids = [job_id for job_id, _ in handled]
    assert len(job_ids) == 12 and len(set(job_ids)) == 12
    assert {worker for _, worker in handled} == {"pool:0", "pool:1", "pool:2"}
    assert 1 < peak_in_flight <= 3
    jobs = list_jobs(migrated_connection, run.run_id)
    assert all(job.status == JOB_SUCCEEDED and job.attempts == 1 for job in jobs)
    assert [job.result for job in jobs] == [{"raw_lead_id": number} for number in range(1, 13)]


def test_the_pool_retries_a_flaky_job_and_gives_up_on_a_broken_one(
    migrated_connection: psycopg.Connection, empty_database_url: str
):
    run, _ = running_run(migrated_connection)
    enqueue_jobs(
        migrated_connection,
        run.run_id,
        KIND,
        [JobSpec("flaky", max_attempts=3), JobSpec("broken", max_attempts=2), JobSpec("fine")],
    )
    attempts_seen: dict[str, int] = {}

    async def flaky_then_fine(job: Job) -> dict | None:
        attempts_seen[job.job_key] = job.attempts
        if job.job_key == "broken":
            raise RuntimeError("endpoint returned 500")
        if job.job_key == "flaky" and job.attempts < 3:
            raise TimeoutError("read timed out")
        return None

    report = asyncio.run(
        run_worker_pool(
            empty_database_url,
            flaky_then_fine,
            run_id=run.run_id,
            concurrency=2,
            retry_policy=FAST_RETRIES,
            poll_interval_seconds=0.02,
        )
    )

    assert (report.succeeded, report.retried, report.failed) == (2, 3, 1)
    assert attempts_seen == {"flaky": 3, "broken": 2, "fine": 1}
    by_key = {job.job_key: job for job in list_jobs(migrated_connection, run.run_id)}
    assert (by_key["flaky"].status, by_key["flaky"].attempts) == (JOB_SUCCEEDED, 3)
    assert by_key["flaky"].last_error == "TimeoutError: read timed out"
    assert (by_key["broken"].status, by_key["broken"].attempts) == (JOB_FAILED, 2)
    assert by_key["broken"].last_error == "RuntimeError: endpoint returned 500"
    assert (by_key["fine"].status, by_key["fine"].attempts) == (JOB_SUCCEEDED, 1)
    assert queue_counts(migrated_connection, run.run_id) == {
        JOB_QUEUED: 0,
        JOB_RUNNING: 0,
        JOB_SUCCEEDED: 2,
        JOB_FAILED: 1,
    }


def test_cancelling_the_pool_gives_the_job_back(
    migrated_connection: psycopg.Connection, empty_database_url: str
):
    run, _ = running_run(migrated_connection)
    enqueue_jobs(migrated_connection, run.run_id, KIND, lead_jobs(1))
    started = asyncio.Event()

    async def hang(job: Job) -> None:
        started.set()
        await asyncio.sleep(60)

    async def start_then_cancel() -> None:
        pool = asyncio.create_task(
            run_worker_pool(empty_database_url, hang, run_id=run.run_id, concurrency=1)
        )
        await asyncio.wait_for(started.wait(), timeout=5)
        pool.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pool

    asyncio.run(start_then_cancel())
    job = list_jobs(migrated_connection, run.run_id)[0]
    assert (job.status, job.attempts, job.last_error) == (JOB_QUEUED, 1, "worker cancelled")


def test_a_pool_needs_at_least_one_worker(empty_database_url: str):
    async def never(job: Job) -> None:
        return None

    with pytest.raises(ValueError):
        asyncio.run(run_worker_pool(empty_database_url, never, concurrency=0))


def test_old_worker_cannot_complete_or_release_a_reclaimed_job(migrated_connection):
    run, _ = running_run(migrated_connection)
    enqueue_jobs(migrated_connection, run.run_id, KIND, lead_jobs(1))
    old = claim_job(migrated_connection, "old", run.run_id)
    migrated_connection.execute("UPDATE cell_jobs SET locked_at=now()-interval '1 hour'")
    requeue_stale_jobs(migrated_connection, timedelta(minutes=10))
    current = claim_job(migrated_connection, "new", run.run_id)
    assert current.attempts == old.attempts + 1
    with pytest.raises(JobNotRunning):
        complete_job(migrated_connection, old)
    with pytest.raises(JobNotRunning):
        release_job(migrated_connection, old, "late failure")
    assert complete_job(migrated_connection, current).status == JOB_SUCCEEDED
