-- Contact enrichment: a cache of BlitzAPI lookups per website domain, and the cost ledger opened
-- to paid services other than Claude.

-- The ledger was written for Claude calls, which have a model and token counts. BlitzAPI has
-- neither: it bills in records per call. Every service keeps one row per call; a Claude row names
-- its model, any other row names its endpoint and the units the provider itself reported, so the
-- ledger can be reconciled against the provider's own usage figures.
ALTER TABLE cost_ledger
    DROP CONSTRAINT cost_ledger_service_check,
    ADD CONSTRAINT cost_ledger_service_check CHECK (service IN ('claude', 'blitz')),
    ALTER COLUMN model DROP NOT NULL,
    ADD COLUMN endpoint text,
    ADD COLUMN provider_units integer NOT NULL DEFAULT 0 CHECK (provider_units >= 0),
    ALTER COLUMN input_tokens SET DEFAULT 0,
    ALTER COLUMN cache_creation_input_tokens SET DEFAULT 0,
    ALTER COLUMN cache_read_input_tokens SET DEFAULT 0,
    ALTER COLUMN output_tokens SET DEFAULT 0,
    ADD CONSTRAINT cost_ledger_call_identity CHECK (
        (service = 'claude' AND model IS NOT NULL)
        OR (service <> 'claude' AND endpoint IS NOT NULL)
    );

-- One row per website domain looked up, found or not, so a domain is never paid for twice while
-- the row is fresh. The person columns show how far the lookup chain got.
CREATE TABLE contact_cache (
    domain               text        PRIMARY KEY CHECK (domain <> '' AND domain = lower(domain)),
    found                boolean     NOT NULL,
    company_linkedin_url text,
    company_name         text,
    person_linkedin_url  text,
    person_name          text,
    person_title         text,
    email                text,
    phone                text,
    records_used         integer     NOT NULL CHECK (records_used >= 0),
    looked_up_at         timestamptz NOT NULL DEFAULT now()
);
