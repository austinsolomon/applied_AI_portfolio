-- One row per paid call, so every run's spend is known per call, per run and in total.

CREATE TABLE cost_ledger (
    cost_entry_id               bigint        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id                      uuid          NOT NULL,
    service                     text          NOT NULL CHECK (service IN ('claude')),
    model                       text          NOT NULL,
    call_number                 integer       NOT NULL CHECK (call_number >= 1),
    input_tokens                integer       NOT NULL CHECK (input_tokens >= 0),
    cache_creation_input_tokens integer       NOT NULL CHECK (cache_creation_input_tokens >= 0),
    cache_read_input_tokens     integer       NOT NULL CHECK (cache_read_input_tokens >= 0),
    output_tokens               integer       NOT NULL CHECK (output_tokens >= 0),
    cost_usd                    numeric(12, 6) NOT NULL CHECK (cost_usd >= 0),
    request_id                  text,
    created_at                  timestamptz   NOT NULL DEFAULT now(),
    UNIQUE (run_id, call_number)
);

CREATE INDEX cost_ledger_run_idx ON cost_ledger (run_id);
