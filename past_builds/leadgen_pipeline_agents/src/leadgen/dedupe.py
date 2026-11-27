"""Dedupe incoming leads against the CRM before anything is spent on them.

An incoming lead is a duplicate when any of its keys matches a lead already in `crm_leads`:
Google place ID, phone, email or website domain. Postgres computes the incoming keys with the
same expressions that generate the CRM's key columns (`0001_crm_core.sql`), so a phone in any
format, an email in any case and a website with or without scheme, "www." or path all match.

The domain key is skipped when the domain belongs to a shared or aggregator host from
`config/category.yaml`: two businesses with Instagram pages share instagram.com without being the
same business.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import psycopg

from leadgen.category_config import CategoryConfig, is_on_host

# Copied verbatim from the generated columns in 0001_crm_core.sql. A test checks they still match
# the migration, so the CRM and the dedupe can never normalize differently.
PHONE_NORMALIZED_SQL = r"nullif(right(regexp_replace(phone, '\D', '', 'g'), 10), '')"
EMAIL_NORMALIZED_SQL = "nullif(lower(btrim(email)), '')"
WEBSITE_DOMAIN_SQL = r"""nullif(
    regexp_replace(
        regexp_replace(lower(btrim(website)), '^[a-z][a-z0-9+.-]*://', ''),
        '^www\.|[/:?#].*$', '', 'g'
    ),
    ''
)"""

# Incoming leads arrive as four parallel arrays and come back in the same order, keyed by
# position, so one round trip handles a whole batch. NULL keys never match: `NULL = NULL` is
# unknown, so EXISTS is false.
DEDUPE_QUERY = f"""
WITH incoming AS (
    SELECT position, nullif(btrim(google_place_id), '') AS google_place_id, phone, email, website
    FROM unnest(
        %(place_ids)s::text[], %(phones)s::text[], %(emails)s::text[], %(websites)s::text[]
    ) WITH ORDINALITY AS raw_lead (google_place_id, phone, email, website, position)
),
normalized AS (
    SELECT
        position,
        google_place_id,
        {PHONE_NORMALIZED_SQL} AS phone_normalized,
        {EMAIL_NORMALIZED_SQL} AS email_normalized,
        {WEBSITE_DOMAIN_SQL} AS website_domain
    FROM incoming
)
SELECT
    google_place_id,
    phone_normalized,
    email_normalized,
    website_domain,
    EXISTS (SELECT 1 FROM crm_leads WHERE crm_leads.google_place_id = normalized.google_place_id)
        AS place_in_crm,
    EXISTS (SELECT 1 FROM crm_leads WHERE crm_leads.phone_normalized = normalized.phone_normalized)
        AS phone_in_crm,
    EXISTS (SELECT 1 FROM crm_leads WHERE crm_leads.email_normalized = normalized.email_normalized)
        AS email_in_crm,
    EXISTS (SELECT 1 FROM crm_leads WHERE crm_leads.website_domain = normalized.website_domain)
        AS domain_in_crm
FROM normalized
ORDER BY position
"""


@dataclass(frozen=True)
class IncomingLead:
    """A sourced business as the scrapers deliver it, before any normalization."""

    business_name: str
    google_place_id: str | None = None
    phone: str | None = None
    email: str | None = None
    website: str | None = None


@dataclass(frozen=True)
class NormalizedKeys:
    """The lead's dedupe keys as the CRM would store them. None when the lead has no such key."""

    google_place_id: str | None
    phone: str | None
    email: str | None
    website_domain: str | None


@dataclass(frozen=True)
class DedupeDecision:
    """One incoming lead, its keys, and which of them matched a CRM lead."""

    lead: IncomingLead
    keys: NormalizedKeys
    matched_on: tuple[str, ...]

    @property
    def is_duplicate(self) -> bool:
        return bool(self.matched_on)


def dedupe_against_crm(
    connection: psycopg.Connection, leads: Sequence[IncomingLead], category: CategoryConfig
) -> list[DedupeDecision]:
    """Decide for every lead, in the given order, whether the CRM already holds it.

    `matched_on` names the keys that matched (`google_place_id`, `phone`, `email`,
    `website_domain`); an empty tuple means the lead is new.
    """
    if not leads:
        return []
    rows = connection.execute(
        DEDUPE_QUERY,
        {
            "place_ids": [lead.google_place_id for lead in leads],
            "phones": [lead.phone for lead in leads],
            "emails": [lead.email for lead in leads],
            "websites": [lead.website for lead in leads],
        },
    ).fetchall()
    excluded_hosts = category.hosts_never_identifying_a_business

    decisions = []
    for lead, row in zip(leads, rows, strict=True):
        place_id, phone, email, domain, place_match, phone_match, email_match, domain_match = row
        keys = NormalizedKeys(place_id, phone, email, domain)
        domain_identifies_business = domain is not None and not is_on_host(domain, excluded_hosts)
        matched_on = tuple(
            key_name
            for key_name, matched in (
                ("google_place_id", place_match),
                ("phone", phone_match),
                ("email", email_match),
                ("website_domain", domain_match and domain_identifies_business),
            )
            if matched
        )
        decisions.append(DedupeDecision(lead, keys, matched_on))
    return decisions


def new_leads(decisions: Sequence[DedupeDecision]) -> list[IncomingLead]:
    """The leads that go on to enrichment: everything the CRM does not already hold."""
    return [decision.lead for decision in decisions if not decision.is_duplicate]
