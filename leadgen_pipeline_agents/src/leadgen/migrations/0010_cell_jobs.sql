-- The job queue: every unit of work a run fans out to its worker pool, claimed with
-- `SELECT … FOR UPDATE SKIP LOCKED` so two workers never take the same job.

CREATE TABLE cell_jobs (
    job_id       bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id       uuid        NOT NULL REFERENCES runs,
    -- The plan cell the work belongs to, when it belongs to one (lead-level jobs may not).
    cell_id      integer     REFERENCES scrape_plan_cells,
    -- What a worker does with the job: 'sample' or 'full' scrape of a cell, 'qualify' a lead, …
    kind         text        NOT NULL CHECK (btrim(kind) <> ''),
    -- Names the unit of work inside the run and kind (a cell id, a raw lead id), so enqueuing the
    -- same work again is a no-op instead of a second job.
    job_key      text        NOT NULL CHECK (btrim(job_key) <> ''),
    payload      jsonb       NOT NULL DEFAULT '{}'::jsonb,
    -- queued: waiting (possibly for a retry delay) · running: claimed by a worker
    -- succeeded: done · failed: gave up after max_attempts
    status       text        NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    attempts     integer     NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts integer     NOT NULL DEFAULT 3 CHECK (max_attempts >= 1),
    -- A retry is a job put back in the queue with a jittered delay: not claimable before this.
    available_at timestamptz NOT NULL DEFAULT now(),
    locked_by    text        CHECK (locked_by IS NULL OR btrim(locked_by) <> ''),
    locked_at    timestamptz,
    last_error   text,
    result       jsonb,
    created_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    UNIQUE (run_id, kind, job_key),
    CHECK (status <> 'running' OR (locked_by IS NOT NULL AND locked_at IS NOT NULL)),
    CHECK ((status IN ('succeeded', 'failed')) = (finished_at IS NOT NULL)),
    CHECK (status <> 'failed' OR last_error IS NOT NULL),
    CHECK (attempts <= max_attempts)
);

-- The claim query scans only queued jobs, oldest-available first.
CREATE INDEX cell_jobs_claimable_idx ON cell_jobs (available_at, job_id) WHERE status = 'queued';
CREATE INDEX cell_jobs_run_idx ON cell_jobs (run_id, status);
