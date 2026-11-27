CREATE TABLE traces (
    trace_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES runs,
    operation text NOT NULL,
    outcome text NOT NULL,
    latency_ms integer NOT NULL DEFAULT 0,
    details jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now()
);
-- Reservation survives process death: an ambiguous paid call cannot be automatically repeated.
CREATE TABLE budget_reservations (
    reservation_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    plan_id integer NOT NULL REFERENCES scrape_plans,
    run_id uuid NOT NULL REFERENCES runs,
    operation text NOT NULL,
    ceiling_usd numeric(12,6) NOT NULL CHECK (ceiling_usd >= 0),
    status text NOT NULL DEFAULT 'started' CHECK (status IN ('started','succeeded','uncertain')),
    result jsonb,
    UNIQUE (plan_id, operation)
);
