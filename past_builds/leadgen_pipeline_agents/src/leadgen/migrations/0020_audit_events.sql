-- Opt-in, verbose audit trail for understanding one execution end to end. Each row reads
-- "actor → action → target": which component did what to which other component. Written only
-- when a command runs with --audit (LEADGEN_AUDIT=1); normal runs add nothing here.
CREATE TABLE audit_events (
    audit_event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    run_id uuid,
    plan_id integer,
    actor text NOT NULL CHECK (btrim(actor) <> ''),
    action text NOT NULL CHECK (btrim(action) <> ''),
    target text NOT NULL CHECK (btrim(target) <> ''),
    detail jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX audit_events_run_idx ON audit_events (run_id, audit_event_id);
CREATE INDEX audit_events_plan_idx ON audit_events (plan_id, audit_event_id);
