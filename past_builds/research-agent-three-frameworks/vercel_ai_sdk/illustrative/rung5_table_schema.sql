-- ILLUSTRATIVE - not run
--
-- Rung 5 — the table: rows, columns, cells. This is the research grid, as three database tables.
-- Not run here because this repo ships no database; it is real SQL and would run against any
-- Postgres (Neon, on Vercel).
--
-- The thing to notice: a COLUMN row is the agent's whole config panel — which model, which prompt,
-- which rubric, which output fields. Change a row of this table and you have changed the agent,
-- with no deploy. And a CELL stores more than the answer: the research text, the step trace, the
-- tokens used and any error. Storing the research text is what lets rung 3 re-judge for free.

-- one row per imported lead (here, one bakery)
create table rows    (row_id bigserial primary key, table_id bigint, lead_fields jsonb);

-- one row per AI column: this IS the agent's config panel
create table columns (column_id bigserial primary key, table_id bigint, column_name text,
                      model_id text,             -- 'anthropic/claude-sonnet-5'
                      prompt_template text,      -- 'Bakery: {{companyName}} ({{websiteDomain}}).
                                                 --  Does this bakery already take online orders? …'
                      qualification_rubric text,
                      output_json_schema jsonb,  -- JSON Schema for the output fields
                      run_if_condition text);    -- e.g. 'website is not null'

-- one row per (row, column): the cell
create table cells   (row_id bigint, column_id bigint,
                      run_status text,           -- pending | running | done | failed
                      research_report text, verdict_fields jsonb, agent_step_trace jsonb,
                      tokens_used int, error_message text,
                      primary key (row_id, column_id));
