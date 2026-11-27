-- ILLUSTRATIVE - not run
--
-- Rung 5, "The table: rows, columns, cells". A simplified schema, printed to show the shape a
-- research grid stores: one row per bakery, one column per research question, one cell per
-- answer. Tenant keys, foreign keys and indexes are left out for readability and
-- would be required in a real application. No database in this repo runs it.

create table rows (
  row_id bigserial primary key, table_id bigint, lead_fields jsonb
);
create table columns (
  column_id bigserial primary key, table_id bigint, column_name text,
  model_id text, prompt_template text, qualification_rubric text,
  output_json_schema jsonb, run_if_condition jsonb, config_revision bigint
);
create table cells (
  row_id bigint, column_id bigint, run_revision bigint,
  run_status text, research_report text, verdict_fields jsonb,
  agent_step_trace jsonb, usage_records jsonb, error_message text,
  primary key (row_id, column_id)
);
