-- ILLUSTRATIVE - not run. Rung 5 (rows, columns, cells), rung 6 (cell_jobs), rung 7 (page_cache).
-- No database is created, migrated or connected to anywhere in this repo. See docs/rung_status.md.

-- one row per imported lead: one bakery
create table rows    (row_id bigserial primary key, table_id bigint, lead_fields jsonb);

-- one row per AI column: the web-research agent's config panel
create table columns (column_id bigserial primary key, table_id bigint, column_name text,
                      model_id text,             -- 'claude-sonnet-5'
                      prompt_template text,      -- 'Bakery: {{companyName}}, {{cityName}} {{stateCode}} ({{websiteDomain}}). Does this bakery already take online orders? ...'
                      qualification_rubric text,
                      output_json_schema jsonb,  -- passed straight to outputFormat (rung 3)
                      run_if_condition text);    -- e.g. 'websiteDomain is not null'

-- one row per (row, column): the cell
create table cells   (row_id bigint, column_id bigint,
                      run_status text,           -- pending | running | done | failed
                      research_report text, verdict_fields jsonb, agent_step_trace jsonb,
                      cost_usd numeric(10,4), agent_session_id text, error_message text,
                      primary key (row_id, column_id));

-- rung 6 - the queue: one row per cell waiting to run
create table cell_jobs (job_id bigserial primary key, row_id bigint, column_id bigint,
                        attempt_count int default 0, run_after timestamptz default now(),
                        claimed_at timestamptz);

-- rung 7 - the page cache: the same page is not bought twice in a week
create table page_cache (page_url text primary key, page_markdown text, fetched_at timestamptz default now());
