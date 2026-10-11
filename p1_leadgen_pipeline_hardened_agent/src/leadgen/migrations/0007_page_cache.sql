-- Fetched web pages, one row per URL, so the same page is never fetched twice within the
-- freshness window (the code decides freshness from fetched_at; the table only stores it).

CREATE TABLE page_cache (
    page_cache_id bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    url           text        NOT NULL UNIQUE CHECK (url ~ '^https?://'),
    final_url     text        NOT NULL,
    title         text,
    text          text        NOT NULL,
    status_code   integer     NOT NULL CHECK (status_code BETWEEN 100 AND 599),
    backend       text        NOT NULL CHECK (backend IN ('firecrawl', 'free')),
    fetched_at    timestamptz NOT NULL DEFAULT now()
);

-- The cost ledger gains the Firecrawl service. The allowed-service check is rebuilt from the full
-- list of planned services rather than extended in place, so this migration applies cleanly
-- whichever earlier migration last redefined that constraint and whatever it named it.
DO $$
DECLARE
    service_check_name text;
BEGIN
    FOR service_check_name IN
        SELECT conname
        FROM pg_constraint
        WHERE conrelid = 'cost_ledger'::regclass
          AND contype = 'c'
          AND pg_get_constraintdef(oid) ILIKE '%service%'
    LOOP
        EXECUTE format('ALTER TABLE cost_ledger DROP CONSTRAINT %I', service_check_name);
    END LOOP;
END $$;

ALTER TABLE cost_ledger
    ADD CONSTRAINT cost_ledger_service_check
    CHECK (service IN ('claude', 'blitz', 'apify', 'exa', 'firecrawl'));
