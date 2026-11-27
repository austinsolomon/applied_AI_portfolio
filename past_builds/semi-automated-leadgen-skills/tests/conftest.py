"""Shared fixtures.

THE SUITE MUST PASS WITH NO KEYS AND NO NETWORK. `no_keys` is autouse, so even a
developer who has APIFY_TOKEN exported in their shell gets a fully mocked run —
otherwise the tests would behave differently on the maintainer's machine than on
a buyer's, which is the worst property a teaching repo can have.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

KEY_VARS = ("APIFY_TOKEN", "APOLLO_API_KEY", "HUNTER_API_KEY",
            "ANTHROPIC_API_KEY", "EMAIL_SENDER_API_KEY",
            "INSTANTLY_API_KEY", "RESEND_API_KEY")


@pytest.fixture(autouse=True)
def no_keys(monkeypatch):
    for var in KEY_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def apify_bakeries() -> list[dict]:
    """The 25 Google Maps rows the offline actor returns (23 distinct bakeries)."""
    return json.loads((FIXTURES / "apify_bakeries_25.json").read_text())


@pytest.fixture
def raw_leads(apify_bakeries):
    from pipeline.source_bakeries import source_bakeries
    return source_bakeries(fixture=apify_bakeries, limit=25)["records"]


@pytest.fixture
def enriched(raw_leads):
    from pipeline.enrich import enrich_leads
    return enrich_leads(raw_leads)


@pytest.fixture
def qualified(enriched):
    from pipeline.qualify import qualify_leads
    return qualify_leads(enriched["records"])


@pytest.fixture
def lead_by_bakery_name(enriched) -> dict[str, dict]:
    """Enriched records keyed by bakery name, e.g. lead_by_bakery_name["Sunrise Bakery"]."""
    return {record["name"]: record for record in enriched["records"]}
