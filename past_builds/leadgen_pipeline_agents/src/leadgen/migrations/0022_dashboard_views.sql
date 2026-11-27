-- Aggregate views behind the dashboard's run history and source tabs. The hosted dashboard logs
-- in as a role that may read only these views and gap_view: run and source totals, never a
-- business's phone, email or page text. A view runs with its owner's rights, so that role needs
-- no access to the tables underneath.

CREATE VIEW run_history_view AS
SELECT
    r.run_id,
    r.plan_id,
    r.status,
    r.started_at,
    r.finished_at,
    a.budget_usd,
    (SELECT string_agg(DISTINCT m.name || ', ' || m.state, '; ')
     FROM scrape_plan_cells c JOIN markets m USING (market_id)
     WHERE c.plan_id = r.plan_id) AS market,
    count(l.raw_lead_id) FILTER (WHERE l.source = 'google_maps') AS maps_sourced,
    count(l.raw_lead_id) FILTER (WHERE l.source = 'exa') AS exa_sourced,
    count(l.raw_lead_id) FILTER (WHERE NOT l.duplicate) AS new_leads,
    count(l.raw_lead_id) FILTER (WHERE l.qualification->>'verdict' = 'qualified') AS qualified,
    count(l.raw_lead_id) FILTER (WHERE l.qualification->>'verdict' = 'review') AS review,
    count(l.raw_lead_id) FILTER (WHERE l.qualification->>'verdict' = 'rejected') AS rejected,
    (SELECT count(*) FROM crm_leads c WHERE c.run_id = r.run_id) AS crm_inserted,
    (SELECT coalesce(sum(cost_usd), 0) FROM cost_ledger k WHERE k.run_id = r.run_id) AS spend_usd
FROM runs r
LEFT JOIN approvals a USING (plan_id)
LEFT JOIN raw_leads l ON l.run_id = r.run_id
GROUP BY r.run_id, a.budget_usd;

CREATE VIEW source_yield_view AS
SELECT source,
       count(*) AS sourced,
       count(*) FILTER (WHERE NOT duplicate) AS new_leads,
       count(qualification) AS judged,
       count(*) FILTER (WHERE qualification->>'verdict' = 'qualified') AS qualified
FROM raw_leads GROUP BY source;

CREATE VIEW consensus_yield_view AS
SELECT query_consensus,
       count(*) AS leads,
       count(qualification) AS judged,
       count(*) FILTER (WHERE qualification->>'verdict' = 'qualified') AS qualified
FROM raw_leads WHERE source = 'exa' GROUP BY query_consensus;

CREATE VIEW spend_by_service_view AS
SELECT service,
       count(*) AS calls,
       sum(cost_usd) FILTER (WHERE cost_basis <> 'estimated') AS reported_usd,
       sum(cost_usd) FILTER (WHERE cost_basis = 'estimated') AS estimated_usd,
       sum(cost_usd) AS total_usd
FROM cost_ledger GROUP BY service;
