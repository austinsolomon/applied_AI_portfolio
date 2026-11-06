-- Tier 1 evidence: read the homepage, then search the business's own site, instead of guessing
-- /services and /about pages (24 of 27 such pages had no text in the 2026-11-04 run).
UPDATE agent_columns
SET config = config || '{"pages": [""], "site_searches": 1}'::jsonb
WHERE column_name = 'qualification';
