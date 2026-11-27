CREATE TABLE pipeline_stages (
    run_id uuid NOT NULL REFERENCES runs,
    stage text NOT NULL,
    result jsonb NOT NULL,
    completed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id,stage)
);
