-- Scrape plans, the one human approval, and run state: nothing runs twice.

-- A plan is the coverage matrix the planner proposes: one cell per market × city × keyword.
CREATE TABLE scrape_plans (
    plan_id    integer     GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- draft: proposed, waiting for the human · approved: may run · cancelled: never runs
    status     text        NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'approved', 'cancelled')),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE scrape_plan_cells (
    cell_id            integer        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    plan_id            integer        NOT NULL REFERENCES scrape_plans ON DELETE CASCADE,
    -- Sweep order inside the plan: the strongest city × keyword corner first.
    position           integer        NOT NULL CHECK (position >= 1),
    market_id          integer        NOT NULL REFERENCES markets,
    -- "Pasadena, CA": the label the market expansion produced, kept as text so the plan
    -- stays readable on its own.
    city               text           NOT NULL CHECK (btrim(city) <> ''),
    keyword            text           NOT NULL CHECK (btrim(keyword) <> ''),
    expected_raw_leads integer        NOT NULL CHECK (expected_raw_leads >= 0),
    estimated_cost_usd numeric(12, 4) NOT NULL CHECK (estimated_cost_usd >= 0),
    -- planned: not sampled yet · proceed / reconfigure / abort: the sample verdict
    -- needs_replan: reconfiguring did not fit the budget · scraped: the full run finished
    status             text           NOT NULL DEFAULT 'planned'
        CHECK (status IN ('planned', 'proceed', 'reconfigure', 'abort', 'needs_replan', 'scraped')),
    -- The scraper run that produced this cell's results, so results are never attributed to
    -- the wrong cell. One scraper run belongs to exactly one cell.
    apify_run_id       text           UNIQUE CHECK (apify_run_id IS NULL OR btrim(apify_run_id) <> ''),
    UNIQUE (plan_id, position),
    UNIQUE (plan_id, market_id, city, keyword)
);

CREATE INDEX scrape_plan_cells_market_idx ON scrape_plan_cells (market_id, city, keyword);

-- The only human decision: this plan may run, up to this many dollars. A plan is approved once.
CREATE TABLE approvals (
    approval_id integer        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    plan_id     integer        NOT NULL UNIQUE REFERENCES scrape_plans,
    budget_usd  numeric(12, 2) NOT NULL CHECK (budget_usd > 0),
    approved_by text           NOT NULL CHECK (btrim(approved_by) <> ''),
    approved_at timestamptz    NOT NULL DEFAULT now()
);

-- One unattended execution of an approved plan. `cost_ledger.run_id` carries the same id.
CREATE TABLE runs (
    run_id      uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id     integer     NOT NULL REFERENCES scrape_plans,
    -- running: in progress · finished: every cell handled · failed: stopped by an error
    -- stopped: halted on purpose, for example by the budget guard
    status      text        NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'finished', 'failed', 'stopped')),
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    CHECK ((status = 'running') = (finished_at IS NULL))
);

-- A plan has at most one run in progress.
CREATE UNIQUE INDEX runs_one_in_progress_per_plan ON runs (plan_id) WHERE status = 'running';

-- Every attempt to work a cell at a stage, whatever happened to it.
CREATE TABLE run_ledger (
    attempt_id     bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id         uuid        NOT NULL REFERENCES runs,
    cell_id        integer     NOT NULL REFERENCES scrape_plan_cells,
    -- sample: the small graded scrape · full: the complete scrape of a passing cell
    stage          text        NOT NULL CHECK (stage IN ('sample', 'full')),
    attempt_number integer     NOT NULL CHECK (attempt_number >= 1),
    status         text        NOT NULL DEFAULT 'started'
        CHECK (status IN ('started', 'succeeded', 'failed')),
    started_at     timestamptz NOT NULL DEFAULT now(),
    finished_at    timestamptz,
    leads_found    integer     CHECK (leads_found >= 0),
    error          text,
    UNIQUE (cell_id, stage, attempt_number),
    CHECK ((status = 'started') = (finished_at IS NULL)),
    CHECK (status <> 'failed' OR error IS NOT NULL)
);

-- A cell never runs twice: only failed attempts may share a cell and stage, so a cell can have
-- one attempt in flight, or one success, and nothing more after it succeeded.
CREATE UNIQUE INDEX run_ledger_one_open_or_done_per_cell_stage
    ON run_ledger (cell_id, stage) WHERE status <> 'failed';
CREATE INDEX run_ledger_run_idx ON run_ledger (run_id);

-- Every business any run has seen, so the same business is never counted as new twice.
-- Keys are normalized by the same expressions as `crm_leads`, so both tables agree.
CREATE TABLE seen_leads (
    seen_lead_id     bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    first_seen_run_id uuid       NOT NULL REFERENCES runs,
    google_place_id  text        CHECK (google_place_id IS NULL OR btrim(google_place_id) <> ''),
    phone            text,
    email            text,
    website          text,
    first_seen_at    timestamptz NOT NULL DEFAULT now(),

    phone_normalized text GENERATED ALWAYS AS (
        nullif(right(regexp_replace(phone, '\D', '', 'g'), 10), '')
    ) STORED,
    email_normalized text GENERATED ALWAYS AS (nullif(lower(btrim(email)), '')) STORED,
    website_domain   text GENERATED ALWAYS AS (
        nullif(
            regexp_replace(
                regexp_replace(lower(btrim(website)), '^[a-z][a-z0-9+.-]*://', ''),
                '^www\.|[/:?#].*$', '', 'g'
            ),
            ''
        )
    ) STORED,

    -- A business with no key at all cannot be recognized again, so it is never recorded.
    CHECK (
        google_place_id IS NOT NULL OR phone_normalized IS NOT NULL
        OR email_normalized IS NOT NULL OR website_domain IS NOT NULL
    )
);

CREATE UNIQUE INDEX seen_leads_google_place_key ON seen_leads (google_place_id);
CREATE UNIQUE INDEX seen_leads_phone_key ON seen_leads (phone_normalized);
CREATE UNIQUE INDEX seen_leads_email_key ON seen_leads (email_normalized);
-- As in the CRM, a domain is a match key but not unique: chain locations share one.
CREATE INDEX seen_leads_website_domain_idx ON seen_leads (website_domain);
