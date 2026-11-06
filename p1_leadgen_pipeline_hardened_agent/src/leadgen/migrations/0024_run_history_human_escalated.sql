-- Review is now its own stage: a lead the judge marked "review" is researched and ends Qualified,
-- Rejected or Human escalated (its stored verdict stays "review"). Run history shows Human
-- escalated, and Not judged so New always equals Qualified + Rejected + Human escalated + Not
-- judged. The existing columns keep their order, so this replaces the view in place and the
-- dashboard role's grant stays.
CREATE OR REPLACE VIEW run_history_view AS
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
    (SELECT coalesce(sum(cost_usd), 0) FROM cost_ledger k WHERE k.run_id = r.run_id) AS spend_usd,
    count(l.raw_lead_id) FILTER (
        WHERE NOT l.duplicate AND l.qualification->>'verdict' = 'review'
    ) AS human_escalated,
    count(l.raw_lead_id) FILTER (WHERE NOT l.duplicate AND l.qualification IS NULL) AS not_judged
FROM runs r
LEFT JOIN approvals a USING (plan_id)
LEFT JOIN raw_leads l ON l.run_id = r.run_id
GROUP BY r.run_id, a.budget_usd;
