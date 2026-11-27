"""Stage 1 tests — the async shape, the schema, and the messy-listing handling."""

from __future__ import annotations

import csv
import json
import re

import pytest

from pipeline.providers.apify import (ApifyActorProvider, MockApifyActorProvider,
                                      ProviderError, get_apify_provider)
from pipeline.schema import (ENRICHED_FIELDS, RAW_FIELDS, is_raw_lead,
                             stable_lead_id)
from pipeline.source_bakeries import (CSV_FIELDS, DEFAULT_ACTOR, actor_input,
                                      lead_id_for_listing, main,
                                      normalise_places, source_bakeries,
                                      write_csv)

RESERVED_DOMAIN = re.compile(r"\.(example|test)(/|$)|//(www\.)?example\.(com|org|net)(/|$)")
FIXTURE_PHONE = re.compile(r"^\+1 312-555-01\d\d$")


def google_maps_row(row_number: int, title: str, **overrides) -> dict:
    """One hand-built actor row in the Google Maps shape, for tests that must
    not depend on the bundled fixture."""
    row = {
        "placeId": f"fixture-place-{row_number:03d}",
        "title": title,
        "categoryName": "Bakery",
        "address": f"{row_number + 9} Example Street, Chicago, IL 60614",
        "city": "Chicago",
        "state": "Illinois",
        "postalCode": "60614",
        "phone": f"+1 312-555-01{row_number:02d}",
        "website": f"https://bakery-{row_number:03d}.example",
        "url": f"https://maps.example/place/fixture-place-{row_number:03d}",
        "totalScore": 4.5,
        "reviewsCount": 100 + row_number,
    }
    row.update(overrides)
    return row


def normalise(rows: list[dict]) -> list[dict]:
    return normalise_places(rows, actor=DEFAULT_ACTOR, provider_name="apify(mock)")


# ------------------------------------------------------------ provider choice --
def test_mock_selected_when_token_unset():
    provider = get_apify_provider(fixture=[])
    assert not provider.live and "mock" in provider.name


def test_live_selected_when_token_set(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "apify_api_xxx")
    provider = get_apify_provider()
    assert provider.live and isinstance(provider, ApifyActorProvider)


# ------------------------------------------------------------ the async shape --
def test_run_is_async_start_poll_fetch(apify_bakeries):
    """The four beats must happen in order, even against the mock.

    This is the whole point of stage 1: a learner who writes code against a
    one-shot mock ships something that breaks the day a token appears.
    """
    provider = MockApifyActorProvider(apify_bakeries, polls_before_done=2)
    source_bakeries(provider=provider, limit=25)
    kinds = [call[0] for call in provider.calls]
    assert kinds[0] == "start"
    assert provider.calls[0][1] == "compass/crawler-google-places"
    assert kinds.count("poll") == 3          # 2 RUNNING then 1 SUCCEEDED
    assert kinds[-1] == "items"
    assert [call[1] for call in provider.calls if call[0] == "poll"][-1] == "SUCCEEDED"


def test_poll_raises_on_non_succeeded_terminal_state():
    """FAILED/ABORTED/TIMED-OUT are terminal too — a loop that only watches for
    SUCCEEDED spins forever on them."""
    class Failing(ApifyActorProvider):
        def _request(self, method, path, body=None):
            return {"data": {"status": "ABORTED"}}

    with pytest.raises(ProviderError, match="ABORTED"):
        Failing("tok", sleep=lambda _seconds: None).poll_run("run-1")


def test_actor_input_caps_the_run():
    body = actor_input(query="bakeries", location="Chicago, Illinois", limit=25)
    assert body == {
        "searchStringsArray": ["bakeries"],
        "locationQuery": "Chicago, Illinois",
        "maxCrawledPlacesPerSearch": 25,
        "language": "en",
        "skipClosedPlaces": True,
    }


# ------------------------------------------------- raw leads, and only raw leads --
def test_normalise_produces_raw_leads_only():
    leads = normalise([google_maps_row(1, "Sunrise Bakery"),
                       google_maps_row(2, "Riverbend Bread")])
    assert len(leads) == 2
    for lead in leads:
        assert is_raw_lead(lead), f"{lead['id']} is not a clean raw lead"
        assert tuple(lead) == RAW_FIELDS
        assert not any(bought_column in lead for bought_column in ENRICHED_FIELDS)
        assert "qualified" not in lead


def test_listing_fields_are_copied_not_guessed():
    [lead] = normalise([google_maps_row(1, "Sunrise Bakery", totalScore=4.7,
                                        reviewsCount=212)])
    assert lead["id"] == stable_lead_id("fixture-place-001") == "apify-maps-72a168d9e318"
    assert lead["name"] == "Sunrise Bakery"
    assert lead["category"] == "Bakery"
    assert lead["address"] == "10 Example Street, Chicago, IL 60614"
    assert (lead["city"], lead["state"], lead["postal_code"]) == ("Chicago", "Illinois", "60614")
    assert lead["google_maps_url"] == "https://maps.example/place/fixture-place-001"
    assert lead["rating"] == 4.7 and lead["review_count"] == 212
    assert lead["source"]["place_id"] == "fixture-place-001"


def test_blank_phone_and_website_stay_blank():
    """Blank means "not found in this result", so sourcing must not fill it in."""
    [lead] = normalise([google_maps_row(13, "Tiny Kiln Bakery", phone="", website="")])
    assert lead["phone"] == "" and lead["website"] == ""
    [lead] = normalise([google_maps_row(13, "Tiny Kiln Bakery", phone=None,
                                        website=None, totalScore=None)])
    assert lead["phone"] == "" and lead["website"] == "" and lead["rating"] is None


def test_rows_without_a_business_name_are_dropped():
    leads = normalise([google_maps_row(1, "Sunrise Bakery"),
                       google_maps_row(2, ""), google_maps_row(3, "   ")])
    assert [lead["name"] for lead in leads] == ["Sunrise Bakery"]


def test_listing_without_a_place_id_still_gets_a_stable_id():
    row = google_maps_row(4, "Flour & Hearth", placeId="")
    first_id = lead_id_for_listing(row, 3)
    assert first_id.startswith("apify-maps-") and len(first_id) == len("apify-maps-") + 12
    assert first_id == lead_id_for_listing(dict(row), 3)


# ------------------------------------------------------------------ duplicates --
def test_same_place_listed_twice_is_one_lead_and_first_listing_wins():
    leads = normalise([
        google_maps_row(1, "Sunrise Bakery"),
        google_maps_row(2, "Riverbend Bread"),
        google_maps_row(1, "Sunrise Bakery", phone="+1 312-555-0107"),
    ])
    assert [lead["name"] for lead in leads] == ["Sunrise Bakery", "Riverbend Bread"]
    assert leads[0]["phone"] == "+1 312-555-0101"


def test_same_phone_and_address_under_a_longer_name_is_one_lead():
    shared = {"phone": "+1 312-555-0105",
              "address": "14 Example Street, Chicago, IL 60615"}
    leads = normalise([
        google_maps_row(5, "Morning Crumb", **shared),
        google_maps_row(16, "Morning Crumb Bakery & Cafe", **shared),
    ])
    assert [lead["name"] for lead in leads] == ["Morning Crumb"]


def test_same_phone_alone_or_blank_phone_is_not_a_duplicate():
    same_phone_other_street = normalise([
        google_maps_row(5, "Morning Crumb", phone="+1 312-555-0105"),
        google_maps_row(6, "Bluebird Bakery", phone="+1 312-555-0105"),
    ])
    assert len(same_phone_other_street) == 2
    blank_phone_same_street = normalise([
        google_maps_row(9, "Lantern Lane Pies", phone="", address="18 Example Street"),
        google_maps_row(13, "Tiny Kiln Bakery", phone="", address="18 Example Street"),
    ])
    assert len(blank_phone_same_street) == 2


def test_ids_are_unique(raw_leads):
    ids = [lead["id"] for lead in raw_leads]
    assert len(ids) == len(set(ids))


# ------------------------------------------------ the bundled 25-bakery fixture --
def test_bakery_fixture_is_25_rows_holding_23_bakeries(apify_bakeries):
    payload = source_bakeries(fixture=apify_bakeries, limit=25)
    assert payload["key"] == "claude3d.leads.v1"
    assert payload["schema"] == "claude3d.raw-business-leads.v1"
    assert payload["stage"] == "source"
    assert payload["actor"] == "compass/crawler-google-places"
    assert payload["query"] == {"text": "bakeries", "location": "Chicago, Illinois",
                                "limit": 25}
    assert payload["counts"] == {"dataset_items": 25, "leads": 23}
    assert not payload["live"]
    names = [record["name"] for record in payload["records"]]
    assert names.count("Sunrise Bakery") == 1
    assert "Morning Crumb" in names and "Morning Crumb Bakery & Cafe" not in names
    assert all(is_raw_lead(record) for record in payload["records"])


def test_bakery_fixture_has_the_designed_gaps(raw_leads):
    """Five bakeries list no phone and six list no website (the seventh blank
    website row in the fixture is the duplicate that gets dropped)."""
    assert sum(1 for lead in raw_leads if not lead["phone"]) == 5
    assert sum(1 for lead in raw_leads if not lead["website"]) == 6
    assert all(isinstance(lead["review_count"], int) for lead in raw_leads)


def test_source_payload_speaks_the_games_schema(raw_leads):
    source = raw_leads[0]["source"]
    assert set(source) == {"level", "provider", "actor", "place_id", "collectedAt"}
    assert source["level"] == "apify_trawler"
    assert source["provider"] == "apify(mock)"
    assert source["collectedAt"].endswith("Z")


def test_fixture_contact_details_are_fabricated_on_reserved_ranges(apify_bakeries):
    """Nothing in the bundled listings can reach a real business."""
    for row in apify_bakeries:
        assert RESERVED_DOMAIN.search(row["url"]), row["url"]
        if row["website"]:
            assert RESERVED_DOMAIN.search(row["website"]), row["website"]
        if row["phone"]:
            assert FIXTURE_PHONE.match(row["phone"]), row["phone"]
        assert "Example Street" in row["address"]


def test_limit_is_honoured(apify_bakeries):
    assert len(source_bakeries(fixture=apify_bakeries, limit=5)["records"]) == 5


# ------------------------------------------------------------- files on disk --
def test_csv_is_the_same_records(tmp_path):
    records = normalise([google_maps_row(1, "Sunrise Bakery"),
                         google_maps_row(4, "Flour & Hearth", website="")])
    csv_path = tmp_path / "raw_bakery_leads.csv"
    write_csv(records, csv_path)
    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert tuple(rows[0]) == CSV_FIELDS
    assert [row["name"] for row in rows] == ["Sunrise Bakery", "Flour & Hearth"]
    assert rows[1]["website"] == ""
    assert rows[0]["source_actor"] == "compass/crawler-google-places"
    assert rows[0]["collected_at"].endswith("Z")


def test_offline_command_writes_csv_and_handoff(tmp_path, capsys, monkeypatch):
    """`python3 -m pipeline.source_bakeries` with no key: 25 rows in, 23 leads out."""
    # Run from an empty folder so a developer's own .env (which may hold a real
    # APIFY_TOKEN) is never loaded; the bundled fixture is still found.
    monkeypatch.chdir(tmp_path)
    csv_path = tmp_path / "raw_bakery_leads.csv"
    handoff_path = tmp_path / "leads.json"
    assert main(["--out", str(csv_path), "--handoff", str(handoff_path)]) == 0
    printed = capsys.readouterr().out
    assert "23 raw bakery leads" in printed and "REHEARSAL FIXTURE" in printed
    assert len(csv_path.read_text(encoding="utf-8").splitlines()) == 24
    handoff = json.loads(handoff_path.read_text())
    assert handoff["counts"] == {"dataset_items": 25, "leads": 23}
    assert handoff["records"][0]["id"] == "apify-maps-72a168d9e318"
