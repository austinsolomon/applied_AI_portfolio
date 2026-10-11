-- Markets, the CRM, and the demand gap per market.

CREATE TABLE markets (
    market_id         integer          GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name              text             NOT NULL CHECK (btrim(name) <> ''),
    state             char(2)          NOT NULL CHECK (state ~ '^[A-Z]{2}$'),
    center_latitude   double precision NOT NULL CHECK (center_latitude BETWEEN -90 AND 90),
    center_longitude  double precision NOT NULL CHECK (center_longitude BETWEEN -180 AND 180),
    service_radius_km numeric(6, 1)    NOT NULL DEFAULT 50 CHECK (service_radius_km > 0),
    lead_goal         integer          NOT NULL CHECK (lead_goal >= 0),
    created_at        timestamptz      NOT NULL DEFAULT now(),
    UNIQUE (name, state)
);

CREATE TABLE crm_leads (
    lead_id              bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    market_id            integer     NOT NULL REFERENCES markets,
    business_name        text        NOT NULL CHECK (btrim(business_name) <> ''),
    -- available: not yet given to a rep · assigned: a rep is working it · converted: won
    -- closed: worked and lost · do_not_contact: never contact again
    status               text        NOT NULL DEFAULT 'available'
        CHECK (status IN ('available', 'assigned', 'converted', 'closed', 'do_not_contact')),
    phone                text,
    email                text,
    website              text,
    google_place_id      text,
    source               text        NOT NULL CHECK (source IN ('google_maps', 'exa', 'demo_seed')),
    qualification_reason text,
    evidence_quote       text,
    evidence_url         text,
    created_at           timestamptz NOT NULL DEFAULT now(),

    -- Dedupe keys, normalized by the database so every writer produces identical values.
    -- US phone numbers: the last 10 digits, whatever the formatting.
    phone_normalized text GENERATED ALWAYS AS (
        nullif(right(regexp_replace(phone, '\D', '', 'g'), 10), '')
    ) STORED,
    email_normalized text GENERATED ALWAYS AS (nullif(lower(btrim(email)), '')) STORED,
    -- Bare domain: no scheme, no "www.", no port, path, query or fragment.
    website_domain   text GENERATED ALWAYS AS (
        nullif(
            regexp_replace(
                regexp_replace(lower(btrim(website)), '^[a-z][a-z0-9+.-]*://', ''),
                '^www\.|[/:?#].*$', '', 'g'
            ),
            ''
        )
    ) STORED,

    CHECK (phone IS NULL OR length(phone_normalized) = 10),
    CHECK (email IS NULL OR email_normalized ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'),
    -- Every CRM lead can be contacted.
    CHECK (phone_normalized IS NOT NULL OR email_normalized IS NOT NULL)
);

-- One business, one row: these keys identify a single business.
CREATE UNIQUE INDEX crm_leads_phone_key ON crm_leads (phone_normalized);
CREATE UNIQUE INDEX crm_leads_email_key ON crm_leads (email_normalized);
CREATE UNIQUE INDEX crm_leads_google_place_key ON crm_leads (google_place_id);
-- A domain is a match key but not unique: chain locations and link-in-bio hosts share domains.
CREATE INDEX crm_leads_website_domain_idx ON crm_leads (website_domain);
CREATE INDEX crm_leads_market_status_idx ON crm_leads (market_id, status);

-- Leads Available % = available ÷ (goal − assigned), per market.
-- Assigned counts leads a rep is working or has converted. When nothing remains of the goal,
-- the percentage is NULL and goal_met is true.
CREATE VIEW gap_view AS
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
    remaining_goal = 0 AS goal_met
FROM remaining;
