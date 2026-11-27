ALTER TABLE cost_ledger ADD COLUMN cost_basis text NOT NULL DEFAULT 'provider_reported'
    CHECK (cost_basis IN ('provider_reported','estimated','usage_reconciliation'));
