"""The Enrich-side check of a research-agent answer, and the meetings step.

Flow under test: Enrich leaves Rye & Rosemary's agent answer waiting for your
check -> you confirm it (review_research) -> Qualify reads the record once and
Rye is the 10th qualified bakery. Dos Rios stays unresolved.
"""

import json
from copy import deepcopy

import pytest

from pipeline.meetings import attach_meetings
from pipeline.qualify import qualify_leads
from pipeline.review_research import confirm_research_answer, main as review_main


def rye_confirmation(record, reviewed_by="Learner"):
    research = record["enrichment"]["research"]
    return {"lead_id": record["id"], "field": "ordering_platform",
            "value": research["proposed_value"], "source_url": research["source_url"],
            "quote": research["quote"], "reviewed_by": reviewed_by}


def record_named(enriched, name):
    return next(record for record in enriched["records"] if record["name"] == name)


def test_confirming_rye_writes_the_field_and_keeps_who_and_source(enriched):
    rye = record_named(enriched, "Rye & Rosemary Bakery")
    confirmed = confirm_research_answer(rye, rye_confirmation(rye))
    assert confirmed["id"] == rye["id"] and confirmed["source"] == rye["source"]
    assert confirmed["ordering_platform"] == "none"
    assert confirmed["takes_online_orders"] is False
    research = confirmed["enrichment"]["research"]
    assert research["status"] == "confirmed"
    assert research["confirmed_by"] == "Learner"
    assert research["source_url"] == "https://rye-and-rosemary.example/order"
    assert "emailing our order form" in research["quote"]
    assert confirmed["enrichment"]["complete"] is True
    assert rye["ordering_platform"] is None, "the original record is not changed"


def test_after_the_check_qualify_runs_once_and_rye_is_the_tenth(enriched):
    before = qualify_leads(enriched["records"])
    assert before["counts"]["qualified"] == 9 and before["counts"]["needs_review"] == 2
    records = [confirm_research_answer(record, rye_confirmation(record))
               if record["name"] == "Rye & Rosemary Bakery" else record
               for record in enriched["records"]]
    after = qualify_leads(records)
    assert after["counts"]["qualified"] == 10
    assert [entry["name"] for entry in after["review_records"]] == ["Dos Rios Panaderia"]


def test_dos_rios_cannot_be_confirmed(enriched):
    dos_rios = record_named(enriched, "Dos Rios Panaderia")
    confirmation = {"lead_id": dos_rios["id"], "field": "ordering_platform",
                    "value": "delivery_marketplace",
                    "source_url": dos_rios["enrichment"]["research"]["source_url"],
                    "quote": "x", "reviewed_by": "Learner"}
    with pytest.raises(ValueError, match="could not tell"):
        confirm_research_answer(dos_rios, confirmation)


@pytest.mark.parametrize("patch", [
    {"reviewed_by": ""}, {"lead_id": "different-bakery"}, {"value": None},
    {"value": "pos_online_store"}, {"quote": ""}, {"source_url": ""},
    {"source_url": "https://rye-and-rosemary.example/"},
    {"field": "owner_role"},
])
def test_a_bad_confirmation_changes_nothing(enriched, patch):
    rye = record_named(enriched, "Rye & Rosemary Bakery")
    with pytest.raises(ValueError):
        confirm_research_answer(rye, {**rye_confirmation(rye), **patch})


def test_an_already_confirmed_answer_has_nothing_waiting(enriched):
    sunrise = record_named(enriched, "Sunrise Bakery")
    with pytest.raises(ValueError):
        confirm_research_answer(sunrise, rye_confirmation(sunrise))


def test_command_line_shows_then_confirms(enriched, tmp_path, capsys):
    path = tmp_path / "enriched.json"
    path.write_text(json.dumps(enriched))

    assert review_main(["--in", str(path), "--lead", "Rye & Rosemary Bakery"]) == 0
    shown = capsys.readouterr().out
    assert "https://rye-and-rosemary.example/order" in shown
    assert json.loads(path.read_text()) == json.loads(json.dumps(enriched)), \
        "showing an answer changes nothing"

    with pytest.raises(SystemExit):
        review_main(["--in", str(path), "--lead", "Rye & Rosemary Bakery", "--confirm"])

    assert review_main(["--in", str(path), "--lead", "Rye & Rosemary Bakery",
                        "--reviewed-by", "Learner", "--confirm"]) == 0
    written = json.loads(path.read_text())
    assert written["counts"]["confirmed"] == 19 and written["counts"]["awaiting_check"] == 0
    assert qualify_leads(written["records"])["counts"]["qualified"] == 10


def test_reply_and_booking_are_distinct_and_fixture_never_enters_live_results():
    result = {'lead_id': 'apify-maps-72a168d9e318', 'sent': True, 'delivered': True, 'replied': True}
    payload = {'records': [result], 'counts': {'replied': 1}, 'live': False}
    assert attach_meetings(payload)['counts']['meetings'] == 1
    assert attach_meetings(payload)['counts']['customers'] == 0
    assert attach_meetings({**payload, 'live': True})['meetings'] == []
    assert attach_meetings({**payload, 'records': [{**result, 'replied': False}]})['meetings'] == []
    assert attach_meetings(deepcopy(payload), [])['meetings'] == []


def test_confirmation_clears_only_the_reviewed_field_marker(enriched):
    from copy import deepcopy
    from pipeline.qualify import qualify_leads
    rye = deepcopy(next(record for record in enriched['records'] if record['name'] == 'Rye & Rosemary Bakery'))
    rye['enrichment']['unverified'] = ['ordering_platform']
    checked = confirm_research_answer(rye, rye_confirmation(rye))
    assert checked['enrichment']['unverified'] == []
    assert qualify_leads([checked])['counts']['qualified'] == 1
    assert rye['enrichment']['unverified'] == ['ordering_platform']
    rye['enrichment']['unverified'].append('owner_role')
    checked = confirm_research_answer(rye, rye_confirmation(rye))
    assert checked['enrichment']['unverified'] == ['owner_role']
    assert qualify_leads([checked])['counts']['needs_review'] == 1
