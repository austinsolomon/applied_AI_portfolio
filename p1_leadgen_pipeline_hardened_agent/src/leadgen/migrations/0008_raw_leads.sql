ALTER TABLE cost_ledger DROP CONSTRAINT cost_ledger_service_check,
    ADD CONSTRAINT cost_ledger_service_check
    CHECK (service IN ('claude', 'blitz', 'firecrawl', 'apify', 'exa'));

CREATE TABLE raw_leads (
    raw_lead_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES runs,
    cell_id integer REFERENCES scrape_plan_cells,
    market_id integer NOT NULL REFERENCES markets,
    source text NOT NULL CHECK (source IN ('google_maps', 'exa')),
    source_key text NOT NULL CHECK (source_key <> ''),
    business_name text NOT NULL CHECK (btrim(business_name) <> ''),
    google_place_id text,
    phone text,
    email text,
    website text,
    latitude double precision,
    longitude double precision,
    categories jsonb NOT NULL DEFAULT '[]',
    seed_consensus integer NOT NULL DEFAULT 1,
    duplicate boolean NOT NULL DEFAULT false,
    qualification jsonb,
    enriched boolean NOT NULL DEFAULT false,
    researched boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, source, source_key)
);
CREATE INDEX raw_leads_run_idx ON raw_leads (run_id);
ALTER TABLE crm_leads ADD COLUMN run_id uuid;
