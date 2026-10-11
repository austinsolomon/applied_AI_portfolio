"""Job queue and worker pool: a run fans its work out to N workers, and nothing is worked twice.

Jobs live in Postgres (`cell_jobs`), one row per unit of work in a run. A worker claims the oldest
available job with `UPDATE … WHERE job_id = (SELECT … FOR UPDATE SKIP LOCKED LIMIT 1)`: one
statement, so two workers can never hold the same job, and a worker never waits for a row another
worker is claiming. A handler that raises puts the job back in the queue with a jittered
exponential delay until `max_attempts` is reached, then the job is marked failed with its last
error. The pool itself is `asyncio`: `concurrency` workers, each on its own connection, so the
number of in-flight calls against a third-party endpoint is capped at exactly that number.
"""

import asyncio
import logging
import os
import random
import socket
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import class_row
from psycopg.types.json import Jsonb

logger = logging.getLogger(__name__)

JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_SUCCEEDED = "succeeded"
JOB_FAILED = "failed"
JOB_STATUSES = (JOB_QUEUED, JOB_RUNNING, JOB_SUCCEEDED, JOB_FAILED)

DEFAULT_MAX_ATTEMPTS = 3


class JobQueueError(Exception):
    """A job queue rule was broken."""


class JobNotRunning(JobQueueError):
    """Only a running job can be completed or released; this one is not (or does not exist)."""


@dataclass(frozen=True)
class JobSpec:
    """One unit of work to enqueue. `job_key` names it inside its run and kind."""

    job_key: str
    payload: dict[str, Any] = field(default_factory=dict)
    cell_id: int | None = None
    max_attempts: int = DEFAULT_MAX_ATTEMPTS


@dataclass(frozen=True)
class Job:
    job_id: int
    run_id: UUID
    cell_id: int | None
    kind: str
    job_key: str
    payload: dict[str, Any]
    status: str
    attempts: int
    max_attempts: int
    available_at: datetime
    locked_by: str | None
    locked_at: datetime | None
    last_error: str | None
    result: dict[str, Any] | None
    created_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True)
class RetryPolicy:
    """Exponential backoff with jitter: retries from one failed batch spread out instead of
    hitting the endpoint together again."""

    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 60.0

    def delay_seconds(
        self, attempt: int, random_fraction: Callable[[], float] = random.random
    ) -> float:
        """Delay before the retry that follows failed attempt number `attempt` (1-based)."""
        if attempt < 1:
            raise ValueError("attempt numbers start at 1")
        exponential = min(self.max_delay_seconds, self.base_delay_seconds * 2 ** (attempt - 1))
        # Anywhere between half and the whole exponential delay.
        return exponential * (0.5 + 0.5 * random_fraction())


DEFAULT_RETRY_POLICY = RetryPolicy()


# --- SQL shared by the sync functions and the async workers ---------------------------------

CLAIM_JOB_SQL = """
UPDATE cell_jobs
SET status = 'running', attempts = attempts + 1, locked_by = %(worker_name)s, locked_at = now()
WHERE job_id = (
    SELECT job_id
    FROM cell_jobs
    WHERE status = 'queued'
      AND available_at <= now()
      AND (%(run_id)s::uuid IS NULL OR run_id = %(run_id)s::uuid)
      AND (%(kinds)s::text[] IS NULL OR kind = ANY (%(kinds)s::text[]))
    ORDER BY available_at, job_id
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
RETURNING *
"""

COMPLETE_JOB_SQL = """
UPDATE cell_jobs
SET status = 'succeeded', finished_at = now(), result = %(result)s
WHERE job_id = %(job_id)s AND status = 'running'
  AND locked_by = %(locked_by)s AND attempts = %(attempts)s
RETURNING *
"""

# A failed attempt either goes back in the queue after a delay or, at the attempt limit, fails
# for good. The row remembers who held it last so a failure can be traced to a worker.
RELEASE_JOB_SQL = """
UPDATE cell_jobs
SET status       = CASE WHEN attempts >= max_attempts THEN 'failed' ELSE 'queued' END,
    finished_at  = CASE WHEN attempts >= max_attempts THEN now() ELSE NULL END,
    available_at = CASE WHEN attempts >= max_attempts THEN available_at
                        ELSE now() + %(delay_seconds)s * interval '1 second' END,
    locked_by    = CASE WHEN attempts >= max_attempts THEN locked_by ELSE NULL END,
    locked_at    = CASE WHEN attempts >= max_attempts THEN locked_at ELSE NULL END,
    last_error   = %(error)s
WHERE job_id = %(job_id)s AND status = 'running'
  AND locked_by = %(locked_by)s AND attempts = %(attempts)s
RETURNING *
"""

PENDING_COUNT_SQL = """
SELECT count(*)
FROM cell_jobs
WHERE status IN ('queued', 'running')
  AND (%(run_id)s::uuid IS NULL OR run_id = %(run_id)s::uuid)
  AND (%(kinds)s::text[] IS NULL OR kind = ANY (%(kinds)s::text[]))
"""


def _claim_params(worker_name: str, run_id: UUID | None, kinds: Iterable[str] | None) -> dict:
    return {
        "worker_name": worker_name,
        "run_id": run_id,
        "kinds": None if kinds is None else list(kinds),
    }


def _fail_or_retry_params(job: Job, error: str, retry_policy: RetryPolicy) -> dict:
    return {
        "job_id": job.job_id,
        "locked_by": job.locked_by,
        "attempts": job.attempts,
        "error": error,
        # A job that is not running is refused by the SQL; the delay only has to be computable.
        "delay_seconds": retry_policy.delay_seconds(max(job.attempts, 1)),
    }


# --- synchronous API (orchestrator, tests, one-off scripts) ---------------------------------


def enqueue_jobs(
    connection: psycopg.Connection, run_id: UUID, kind: str, specs: Iterable[JobSpec]
) -> int:
    """Add jobs to a run's queue. A job already queued under its (run, kind, key) is skipped,
    so enqueuing the same work again never doubles it. Returns how many were new."""
    inserted = 0
    with connection.transaction():
        for spec in specs:
            inserted += connection.execute(
                """
                INSERT INTO cell_jobs (run_id, cell_id, kind, job_key, payload, max_attempts)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id, kind, job_key) DO NOTHING
                """,
                (run_id, spec.cell_id, kind, spec.job_key, Jsonb(spec.payload), spec.max_attempts),
            ).rowcount
    return inserted


def claim_job(
    connection: psycopg.Connection,
    worker_name: str,
    run_id: UUID | None = None,
    kinds: Iterable[str] | None = None,
) -> Job | None:
    """Take the oldest available job for this worker, or None when nothing is claimable."""
    with connection.cursor(row_factory=class_row(Job)) as cursor:
        return cursor.execute(CLAIM_JOB_SQL, _claim_params(worker_name, run_id, kinds)).fetchone()


def complete_job(
    connection: psycopg.Connection, job: Job, result: dict[str, Any] | None = None
) -> Job:
    """Mark a running job succeeded, keeping whatever the handler returned."""
    with connection.cursor(row_factory=class_row(Job)) as cursor:
        completed = cursor.execute(
            COMPLETE_JOB_SQL,
            {
                "job_id": job.job_id,
                "result": _jsonb_or_none(result),
                "locked_by": job.locked_by,
                "attempts": job.attempts,
            },
        ).fetchone()
    if completed is None:
        raise JobNotRunning(f"job {job.job_id} is not owned by this attempt")
    return completed


def release_job(
    connection: psycopg.Connection,
    job: Job,
    error: str,
    retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
) -> Job:
    """Give a running job back after a failed attempt: queued again after a jittered delay, or
    failed for good once `max_attempts` is used up."""
    with connection.cursor(row_factory=class_row(Job)) as cursor:
        released = cursor.execute(
            RELEASE_JOB_SQL, _fail_or_retry_params(job, error, retry_policy)
        ).fetchone()
    if released is None:
        raise JobNotRunning(f"job {job.job_id} is not running")
    return released


def requeue_stale_jobs(connection: psycopg.Connection, stale_after: timedelta) -> int:
    """Put jobs back in the queue whose worker went silent (a crashed process never releases).

    The attempt it was on still counts, so a job that keeps killing its worker still fails after
    `max_attempts` instead of looping forever."""
    with connection.transaction():
        connection.execute(
            """
            UPDATE cell_jobs
            SET status = 'failed', finished_at = now(), last_error = 'worker went silent'
            WHERE status = 'running' AND locked_at < now() - %(stale_after)s
              AND attempts >= max_attempts
            """,
            {"stale_after": stale_after},
        )
        return connection.execute(
            """
            UPDATE cell_jobs
            SET status = 'queued', locked_by = NULL, locked_at = NULL,
                last_error = 'worker went silent'
            WHERE status = 'running' AND locked_at < now() - %(stale_after)s
            """,
            {"stale_after": stale_after},
        ).rowcount


def read_job(connection: psycopg.Connection, job_id: int) -> Job:
    with connection.cursor(row_factory=class_row(Job)) as cursor:
        job = cursor.execute("SELECT * FROM cell_jobs WHERE job_id = %s", (job_id,)).fetchone()
    if job is None:
        raise JobQueueError(f"job {job_id} does not exist")
    return job


def list_jobs(connection: psycopg.Connection, run_id: UUID) -> list[Job]:
    with connection.cursor(row_factory=class_row(Job)) as cursor:
        return cursor.execute(
            "SELECT * FROM cell_jobs WHERE run_id = %s ORDER BY job_id", (run_id,)
        ).fetchall()


def queue_counts(connection: psycopg.Connection, run_id: UUID) -> dict[str, int]:
    """How many of the run's jobs sit in each status (every status present, zeros included)."""
    counts = dict.fromkeys(JOB_STATUSES, 0)
    counts.update(
        connection.execute(
            "SELECT status, count(*) FROM cell_jobs WHERE run_id = %s GROUP BY status", (run_id,)
        ).fetchall()
    )
    return counts


def _jsonb_or_none(value: dict[str, Any] | None) -> Jsonb | None:
    return None if value is None else Jsonb(value)


# --- the asyncio worker pool ------------------------------------------------------------------

JobHandler = Callable[[Job], Awaitable[dict[str, Any] | None]]


@dataclass
class WorkerPoolReport:
    """What happened while the pool ran: attempts that succeeded, were put back, or failed for
    good."""

    succeeded: int = 0
    retried: int = 0
    failed: int = 0

    @property
    def attempts(self) -> int:
        return self.succeeded + self.retried + self.failed


def default_worker_name_prefix() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


async def run_worker_pool(
    database_url: str,
    handler: JobHandler,
    *,
    run_id: UUID | None = None,
    kinds: Iterable[str] | None = None,
    concurrency: int = 4,
    retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    poll_interval_seconds: float = 0.5,
    stop_when_drained: bool = True,
    worker_name_prefix: str | None = None,
) -> WorkerPoolReport:
    """Run `concurrency` workers until the matching jobs are all succeeded or failed.

    Each worker claims a job, awaits `handler(job)`, and records the outcome: the handler's return
    value as the result, or its exception as the error that sends the job back to the queue (or
    fails it at the attempt limit). With `stop_when_drained` false, the workers keep polling for
    new jobs until cancelled. Cancelling the pool releases any job a worker was on, without
    charging it an extra attempt beyond the one already counted.
    """
    if concurrency < 1:
        raise ValueError("a pool needs at least one worker")
    kinds = None if kinds is None else list(kinds)
    prefix = worker_name_prefix or default_worker_name_prefix()
    report = WorkerPoolReport()
    workers = [
        _worker(
            database_url,
            f"{prefix}:{index}",
            handler,
            run_id,
            kinds,
            retry_policy,
            poll_interval_seconds,
            stop_when_drained,
            report,
        )
        for index in range(concurrency)
    ]
    await asyncio.gather(*workers)
    return report


async def _worker(
    database_url: str,
    worker_name: str,
    handler: JobHandler,
    run_id: UUID | None,
    kinds: list[str] | None,
    retry_policy: RetryPolicy,
    poll_interval_seconds: float,
    stop_when_drained: bool,
    report: WorkerPoolReport,
) -> None:
    async with await psycopg.AsyncConnection.connect(database_url, autocommit=True) as connection:
        claim_params = _claim_params(worker_name, run_id, kinds)
        while True:
            async with connection.cursor(row_factory=class_row(Job)) as cursor:
                await cursor.execute(CLAIM_JOB_SQL, claim_params)
                job = await cursor.fetchone()
            if job is None:
                if stop_when_drained and await _pending_count(connection, run_id, kinds) == 0:
                    return
                await asyncio.sleep(poll_interval_seconds)
                continue
            await _work_one(connection, job, handler, retry_policy, report)


async def _work_one(
    connection: psycopg.AsyncConnection,
    job: Job,
    handler: JobHandler,
    retry_policy: RetryPolicy,
    report: WorkerPoolReport,
) -> None:
    try:
        result = await handler(job)
    except asyncio.CancelledError:
        await _release(connection, job, "worker cancelled", retry_policy)
        raise
    except Exception as failure:
        error = f"{type(failure).__name__}: {failure}"
        released = await _release(connection, job, error, retry_policy)
        if released.status == JOB_FAILED:
            report.failed += 1
            logger.warning(
                "job %s failed for good after %s attempts: %s", job.job_id, job.attempts, error
            )
        else:
            report.retried += 1
            logger.info("job %s attempt %s failed, retrying: %s", job.job_id, job.attempts, error)
        return
    async with connection.cursor(row_factory=class_row(Job)) as cursor:
        await cursor.execute(
            COMPLETE_JOB_SQL,
            {
                "job_id": job.job_id,
                "result": _jsonb_or_none(result),
                "locked_by": job.locked_by,
                "attempts": job.attempts,
            },
        )
        completed = await cursor.fetchone()
    if completed is None:
        raise JobNotRunning(f"job {job.job_id} is not running")
    report.succeeded += 1


async def _release(
    connection: psycopg.AsyncConnection, job: Job, error: str, retry_policy: RetryPolicy
) -> Job:
    async with connection.cursor(row_factory=class_row(Job)) as cursor:
        await cursor.execute(RELEASE_JOB_SQL, _fail_or_retry_params(job, error, retry_policy))
        released = await cursor.fetchone()
    if released is None:
        raise JobNotRunning(f"job {job.job_id} is not running")
    return released


async def _pending_count(
    connection: psycopg.AsyncConnection, run_id: UUID | None, kinds: list[str] | None
) -> int:
    cursor = await connection.execute(PENDING_COUNT_SQL, {"run_id": run_id, "kinds": kinds})
    row = await cursor.fetchone()
    return 0 if row is None else int(row[0])
