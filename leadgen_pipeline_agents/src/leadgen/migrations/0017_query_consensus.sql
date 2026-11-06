-- Exa sourcing now runs several plain-language queries instead of seed lookalike searches, so the
-- count is how many queries surfaced a business.
ALTER TABLE raw_leads RENAME COLUMN seed_consensus TO query_consensus;
