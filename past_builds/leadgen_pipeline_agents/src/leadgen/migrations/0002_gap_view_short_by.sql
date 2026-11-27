-- Add "short by" to the demand gap: how many new qualified leads a market still has to find
-- after counting the available leads already in the CRM. Never below zero.
-- CREATE OR REPLACE VIEW keeps the existing columns in place and appends the new one.

CREATE OR REPLACE VIEW gap_view AS
WITH lead_counts AS (
    SELECT
        markets.market_id,
        count(*) FILTER (WHERE crm_leads.status IN ('assigned', 'converted')) AS assigned_leads,
        count(*) FILTER (WHERE crm_leads.status = 'available') AS available_leads
    FROM markets
    LEFT JOIN crm_leads USING (market_id)
    GROUP BY markets.market_id
),
remaining AS (
    SELECT
        markets.market_id,
        markets.name AS market_name,
        markets.state,
        markets.lead_goal,
        lead_counts.assigned_leads::integer AS assigned_leads,
        lead_counts.available_leads::integer AS available_leads,
        greatest(markets.lead_goal - lead_counts.assigned_leads, 0)::integer AS remaining_goal
    FROM markets
    JOIN lead_counts USING (market_id)
)
SELECT
    market_id,
    market_name,
    state,
    lead_goal,
    assigned_leads,
    available_leads,
    remaining_goal,
    CASE
        WHEN remaining_goal = 0 THEN NULL
        ELSE round(100.0 * available_leads / remaining_goal)::integer
    END AS leads_available_pct,
    remaining_goal = 0 AS goal_met,
    greatest(remaining_goal - available_leads, 0) AS short_by_leads
FROM remaining;
