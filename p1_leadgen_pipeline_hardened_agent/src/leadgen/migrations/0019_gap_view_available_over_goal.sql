-- Simpler demand model: every market's progress is its available leads against its goal.
-- Leads Available % = available ÷ goal; Leads Needed = goal − available, never below zero.
-- Assigned and converted leads no longer count toward a market's goal.
DROP VIEW gap_view;

CREATE VIEW gap_view AS
SELECT
    markets.market_id,
    markets.name AS market_name,
    markets.state,
    markets.lead_goal,
    count(crm_leads.lead_id) FILTER (WHERE crm_leads.status = 'available')::integer
        AS available_leads,
    greatest(
        markets.lead_goal
            - count(crm_leads.lead_id) FILTER (WHERE crm_leads.status = 'available'),
        0
    )::integer AS leads_needed,
    CASE
        WHEN markets.lead_goal = 0 THEN 100
        ELSE round(
            100.0 * count(crm_leads.lead_id) FILTER (WHERE crm_leads.status = 'available')
            / markets.lead_goal
        )::integer
    END AS leads_available_pct,
    count(crm_leads.lead_id) FILTER (WHERE crm_leads.status = 'available') >= markets.lead_goal
        AS goal_met
FROM markets
LEFT JOIN crm_leads USING (market_id)
GROUP BY markets.market_id;
