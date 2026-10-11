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

-- The cost ledger gains the Firecrawl service. Only the allowed-service check is replaced (0003
-- created it and 0006 last redefined it, both under this name); the other ledger constraints,
-- such as the one that ties every non-Claude row to an endpoint, stay in place.
ALTER TABLE cost_ledger
    DROP CONSTRAINT cost_ledger_service_check,
    ADD CONSTRAINT cost_ledger_service_check
        CHECK (service IN ('claude', 'blitz', 'apify', 'exa', 'firecrawl'));
