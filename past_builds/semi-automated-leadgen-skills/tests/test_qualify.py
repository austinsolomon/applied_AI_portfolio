"""Stage 3 tests — the four rules, the buckets, and unknown fields going to
needs_review.

Every test but the last builds its own input records by hand, so this file
proves the qualify stage on its own, whatever the stages before it produce.
"""

from __future__ import annotations

import json

import pytest

from pipeline.qualify import HARD_RULES, ICP, main, qualify_leads
from pipeline.schema import (ONLINE_ORDERING_PLATFORMS, OWNER_ROLE_LADDER,
                             QUALIFIED_KEY, RULE_FIELDS, stable_lead_id)

CONFIRMED = {"field": "ordering_platform", "status": "confirmed"}
AWAITING_NONE = {"field": "ordering_platform", "status": "awaiting_check",
                 "proposed_value": "none"}
UNCLEAR = {"field": "ordering_platform", "status": "unclear", "proposed_value": None}


def bakery_lead(**overrides):
    """One enriched bakery that fits every rule; override a column to break one."""
    lead = {"id": "test-bakery-1", "name": "Practice Bakery",
            "website": "https://practice-bakery.example", "review_count": 212,
            "owner_name": "Pat Example", "owner_role": "Owner",
            "email": "pat@practice-bakery.example",
            "location_count": 1, "ordering_platform": "none",
            "enrichment": {"research": CONFIRMED}}
    lead.update(overrides)
    return lead


# The 23 distinct bakeries on the course's list, as Enrich hands them over:
# (row, name, review_count, location_count, owner_role, ordering_platform,
# research note). Rows 7 and 16 are the two repeated listings Source dropped.
BAKERY_LIST_AFTER_ENRICHMENT = [
    (1, "Sunrise Bakery", 212, 1, "Owner", "none", CONFIRMED),
    (2, "Riverbend Bread", 148, 1, "Owner", "none", CONFIRMED),
    (3, "Oak Street Bakes", 530, 2, "Owner", "pos_online_store", CONFIRMED),
    (4, "Flour & Hearth", 96, 1, "Co-owner", "none", CONFIRMED),
    # Apollo lists a baker here; Hunter, next in the waterfall, finds the owner.
    (5, "Morning Crumb", 51, 1, "Owner", "none", CONFIRMED),
    (6, "Bluebird Bakery", 64, 1, "Co-owner", "none", CONFIRMED),
    (8, "Golden Wheat Bakery Cafe", 640, 12, "Corporate Office", "none", CONFIRMED),
    (9, "Lantern Lane Pies", 310, 1, "Owner", "none", CONFIRMED),
    (10, "Velvet Whisk Patisserie", 780, 1, "Owner", "delivery_marketplace",
     {"status": "not_needed"}),
    (11, "Butter & Thyme Bakery", 350, 4, "Owner", "none", CONFIRMED),
    (12, "Sweet Thistle Bakeshop", 420, 2, "Owner", "none", CONFIRMED),
    (13, "Tiny Kiln Bakery", 9, 1, "Owner", "none", CONFIRMED),
    (14, "Fig & Feather Bakery", 120, 1, "Shift Lead", "none", CONFIRMED),
    (15, "Tin Roof Bakery", 58, 1, "General Manager", "pos_online_store", CONFIRMED),
    (17, "Poppyseed Bakehouse", 260, 1, "Owner", "website_builder_checkout",
     {"status": "not_needed"}),
    (18, "Rye & Rosemary Bakery", 88, 1, "Owner", None, AWAITING_NONE),
    (19, "Honeycomb Oven", 37, 1, "Owner", "none", CONFIRMED),
    (20, "Daily Dozen Donuts & Bakery", 1850, 40, "Corporate Office",
     "restaurant_ordering_app", {"status": "not_needed"}),
    (21, "Grand Avenue Cake Hall", 2340, 1, "Owner", "none", CONFIRMED),
    (22, "Little Anvil Bread Co.", 190, 1, "Owner", "restaurant_ordering_app", CONFIRMED),
    (23, "Dos Rios Panaderia", 175, 1, "Owner", None, UNCLEAR),
    (24, "Porchlight Pastry", 22, 0, "Owner", "none", CONFIRMED),
    (25, "Night Owl Bagels & Bakes", 205, 1, "Owner", "none", CONFIRMED),
]


def bakery_list_records():
    return [bakery_lead(id=stable_lead_id(f"fixture-place-{row:03d}"), name=name,
                        review_count=review_count, location_count=location_count,
                        owner_role=owner_role, ordering_platform=ordering_platform,
                        enrichment={"research": research})
            for (row, name, review_count, location_count, owner_role,
                 ordering_platform, research) in BAKERY_LIST_AFTER_ENRICHMENT]


def names_in(entries):
    return sorted(entry["name"] for entry in entries)


# ------------------------------------------------------------- the four rules --

@pytest.mark.parametrize("review_count,ok", [
    (19, False), (20, True), (212, True), (1000, True), (1001, False),
])
def test_review_band_is_inclusive_at_both_edges(review_count, ok):
    assert (ICP().check(bakery_lead(review_count=review_count)) == []) is ok


@pytest.mark.parametrize("location_count,ok", [
    (0, False), (1, True), (2, True), (3, True), (4, False), (12, False),
])
def test_location_band_is_inclusive_at_both_edges(location_count, ok):
    assert (ICP().check(bakery_lead(location_count=location_count)) == []) is ok


@pytest.mark.parametrize("owner_role,ok", [
    ("Counter Staff", False), ("Baker", False), ("Shift Lead", False),
    ("Manager", True), ("General Manager", True), ("Co-owner", True),
    ("Owner", True), ("Corporate Office", False),
])
def test_role_band_has_a_ceiling_as_well_as_a_floor(owner_role, ok):
    """Above the band is a rejection, not a bonus — selling to a chain's head
    office is a different sale, and the band's ceiling says so."""
    assert (ICP().check(bakery_lead(owner_role=owner_role)) == []) is ok


@pytest.mark.parametrize("ordering_platform", ONLINE_ORDERING_PLATFORMS)
def test_every_platform_on_the_list_is_a_rejection(ordering_platform):
    assert ICP().check(bakery_lead(ordering_platform=ordering_platform)) == [
        f"ordering_platform:{ordering_platform}:already-takes-online-orders"]


def test_only_none_passes_the_platform_rule():
    assert ICP().check(bakery_lead(ordering_platform="none")) == []
    assert ICP().check(bakery_lead(ordering_platform="carrier_pigeon")) == [
        "ordering_platform:unrecognized('carrier_pigeon')"]


def test_reason_strings_carry_the_value_and_the_edge():
    check = ICP().check
    assert check(bakery_lead(review_count=9)) == ["review_count:9<20"]
    assert check(bakery_lead(review_count=2340)) == ["review_count:2340>1000"]
    assert check(bakery_lead(location_count=0)) == ["location_count:0<1"]
    assert check(bakery_lead(location_count=12)) == ["location_count:12>3"]
    assert check(bakery_lead(owner_role="Shift Lead")) == ["owner_role:Shift Lead<Manager"]
    assert check(bakery_lead(owner_role="Corporate Office")) == [
        "owner_role:Corporate Office>Owner"]


def test_reasons_name_every_rule_that_was_broken_in_a_fixed_order():
    reasons = ICP().check(bakery_lead(
        review_count=1850, location_count=40, owner_role="Corporate Office",
        ordering_platform="restaurant_ordering_app"))
    assert reasons == [
        "review_count:1850>1000",
        "location_count:40>3",
        "owner_role:Corporate Office>Owner",
        "ordering_platform:restaurant_ordering_app:already-takes-online-orders",
    ]


def test_missing_data_is_a_rejection_not_a_pass():
    check = ICP().check
    assert check(bakery_lead(review_count=None)) == ["review_count:unknown"]
    assert check(bakery_lead(location_count=None)) == ["location_count:unknown"]
    assert check(bakery_lead(owner_role=None)) == ["owner_role:unknown"]
    assert check(bakery_lead(ordering_platform=None)) == ["ordering_platform:unknown"]
    assert check(bakery_lead(ordering_platform="")) == ["ordering_platform:unknown"]


def test_a_role_off_the_ladder_is_named_not_guessed():
    assert ICP().check(bakery_lead(owner_role="Head of Sprinkles")) == [
        "owner_role:unrecognized('Head of Sprinkles')"]


# ------------------------------------------------ unknown goes to needs_review --

@pytest.mark.parametrize("field", ["review_count", "location_count", "owner_role"])
def test_an_unknown_rule_field_goes_to_needs_review_with_its_name(field):
    result = qualify_leads([bakery_lead(**{field: None})])
    assert result["counts"]["needs_review"] == 1
    assert result["counts"]["qualified"] == result["counts"]["taken"] == 0
    assert result["review_records"][0]["reasons"] == [f"{field}:unknown"]
    assert result["records"] == []


def test_an_unknown_ordering_platform_names_why_it_is_unknown():
    awaiting = bakery_lead(ordering_platform=None, enrichment={"research": AWAITING_NONE})
    unclear = bakery_lead(id="b2", ordering_platform=None, enrichment={"research": UNCLEAR})
    skipped = bakery_lead(id="b3", ordering_platform=None,
                          enrichment={"research": {"status": "not_run"}})
    reasons = {entry["id"]: entry["reasons"]
               for entry in qualify_leads([awaiting, unclear, skipped])["review_records"]}
    assert reasons == {"test-bakery-1": ["ordering_platform:unknown:awaiting_check"],
                       "b2": ["ordering_platform:unknown:unclear"],
                       "b3": ["ordering_platform:unknown:not_run"]}


def test_an_unconfirmed_agent_answer_never_decides():
    """The answer waits in the research note; only the record's field counts."""
    lead = bakery_lead(ordering_platform=None, enrichment={"research": AWAITING_NONE})
    result = qualify_leads([lead])
    assert result["counts"]["qualified"] == 0 and result["counts"]["needs_review"] == 1


@pytest.mark.parametrize("field", RULE_FIELDS)
def test_a_present_but_explicitly_unverified_rule_field_needs_review(field):
    lead = bakery_lead(enrichment={"unverified": [field]})
    original = lead[field]
    assert ICP().check(lead) == [f"{field}:unknown"]
    result = qualify_leads([lead])
    assert result["counts"]["qualified"] == 0
    assert result["counts"]["needs_review"] == 1
    assert lead[field] == original


def test_qualify_calls_no_provider_and_no_agent():
    """Pure rules on the enriched record: nothing outside it is imported."""
    import inspect
    import pipeline.qualify as qualify_module
    source = inspect.getsource(qualify_module)
    assert "providers" not in source and "import requests" not in source
    assert not hasattr(ICP, "unverified_blockers")
    assert RULE_FIELDS == ("review_count", "location_count", "owner_role",
                           "ordering_platform")


# ----------------------------------------------------------- the four buckets --

def test_a_near_miss_is_audited_but_never_reaches_outreach():
    """The junk stays on the list so its cost lands downstream where it belongs."""
    result = qualify_leads([bakery_lead(),
                            bakery_lead(id="test-bakery-2", location_count=4)])
    assert result["counts"]["qualified"] == 1 and result["counts"]["chased"] == 1
    assert result["counts"]["delivered_to_outreach"] == 1
    assert len(result["records"]) == 1
    chased_entry = result["chased_records"][0]
    assert chased_entry["reasons"] == ["location_count:4>3"]
    assert chased_entry["qualified"] is False


def test_no_chase_mode_keeps_junk_off_the_list():
    result = qualify_leads([bakery_lead(),
                            bakery_lead(id="test-bakery-2", location_count=4)],
                           accept_out_of_zone=False)
    assert result["counts"]["chased"] == 0 and result["counts"]["taken"] == 1
    assert result["counts"]["delivered_to_outreach"] == 1


def test_hard_rules_are_the_platform_and_the_role():
    assert HARD_RULES == ("ordering_platform", "owner_role")


@pytest.mark.parametrize("overrides", [
    {"ordering_platform": "pos_online_store"},
    {"owner_role": "Shift Lead"},
    {"owner_role": "Corporate Office"},
    {"location_count": 12, "owner_role": "Corporate Office"},
])
def test_a_broken_hard_rule_is_taken_even_when_chasing_is_on(overrides):
    """Nothing to sell, or nobody to write to: never worth a message."""
    result = qualify_leads([bakery_lead(**overrides)], accept_out_of_zone=True)
    assert result["counts"]["taken"] == 1
    assert result["counts"]["chased"] == 0
    assert result["records"] == []
    assert result["taken_records"][0]["reasons"] != []


def test_every_rejection_carries_a_reason():
    result = qualify_leads(bakery_list_records())
    rejected = ([entry for entry in result["records"] if not entry["qualified"]]
                + result["taken_records"] + result["review_records"])
    assert rejected and all(entry["reasons"] for entry in rejected)
    assert all(entry["reasons"] == [] for entry in result["records"] if entry["qualified"])


def test_missed_counts_a_fit_waiting_on_your_check():
    """A needs_review lead whose waiting agent answer would qualify it."""
    lead = bakery_lead(ordering_platform=None, enrichment={"research": AWAITING_NONE})
    assert qualify_leads([lead])["counts"]["missed"] == 1


def test_an_unclear_or_failing_answer_is_not_missed():
    unclear = bakery_lead(ordering_platform=None, enrichment={"research": UNCLEAR})
    would_fail = bakery_lead(id="b2", ordering_platform=None, enrichment={"research": {
        **AWAITING_NONE, "proposed_value": "delivery_marketplace"}})
    result = qualify_leads([unclear, would_fail])
    assert result["counts"]["needs_review"] == 2
    assert result["counts"]["missed"] == 0


# ------------------------------------------------- the course's 23 bakeries --

def test_the_bakery_list_lands_in_the_designed_buckets():
    result = qualify_leads(bakery_list_records())
    assert result["counts"] == {"qualified": 9, "chased": 4, "taken": 8,
                                "missed": 1, "needs_review": 2,
                                "delivered_to_outreach": 9, "rejected": 12}
    qualified_entries = [entry for entry in result["records"] if entry["qualified"]]
    chased_entries = result["chased_records"]
    assert names_in(qualified_entries) == sorted([
        "Sunrise Bakery", "Riverbend Bread", "Flour & Hearth", "Morning Crumb",
        "Bluebird Bakery", "Lantern Lane Pies", "Sweet Thistle Bakeshop", "Honeycomb Oven",
        "Night Owl Bagels & Bakes"])
    assert names_in(chased_entries) == sorted([
        "Butter & Thyme Bakery", "Tiny Kiln Bakery", "Grand Avenue Cake Hall",
        "Porchlight Pastry"])
    assert names_in(result["taken_records"]) == sorted([
        "Oak Street Bakes", "Golden Wheat Bakery Cafe",
        "Velvet Whisk Patisserie", "Fig & Feather Bakery", "Tin Roof Bakery",
        "Poppyseed Bakehouse", "Daily Dozen Donuts & Bakery",
        "Little Anvil Bread Co."])
    assert names_in(result["review_records"]) == [
        "Dos Rios Panaderia", "Rye & Rosemary Bakery"]


def test_the_bakery_list_reasons_read_as_designed():
    result = qualify_leads(bakery_list_records())
    every_entry = result["records"] + result["rejected_records"] + result["review_records"]
    reasons_by_name = {entry["name"]: entry["reasons"] for entry in every_entry}
    assert reasons_by_name["Golden Wheat Bakery Cafe"] == [
        "location_count:12>3", "owner_role:Corporate Office>Owner"]
    assert reasons_by_name["Oak Street Bakes"] == [
        "ordering_platform:pos_online_store:already-takes-online-orders"]
    assert reasons_by_name["Morning Crumb"] == []
    assert reasons_by_name["Tin Roof Bakery"] == [
        "ordering_platform:pos_online_store:already-takes-online-orders"]
    assert reasons_by_name["Fig & Feather Bakery"] == ["owner_role:Shift Lead<Manager"]
    assert reasons_by_name["Butter & Thyme Bakery"] == ["location_count:4>3"]
    assert reasons_by_name["Tiny Kiln Bakery"] == ["review_count:9<20"]
    assert reasons_by_name["Grand Avenue Cake Hall"] == ["review_count:2340>1000"]
    assert reasons_by_name["Porchlight Pastry"] == ["location_count:0<1"]
    assert reasons_by_name["Rye & Rosemary Bakery"] == [
        "ordering_platform:unknown:awaiting_check"]
    assert reasons_by_name["Dos Rios Panaderia"] == ["ordering_platform:unknown:unclear"]
    assert len(reasons_by_name["Daily Dozen Donuts & Bakery"]) == 4


def test_no_chase_mode_on_the_bakery_list_moves_the_four_near_misses_to_taken():
    result = qualify_leads(bakery_list_records(), accept_out_of_zone=False)
    assert result["counts"]["qualified"] == 9
    assert result["counts"]["chased"] == 0
    assert result["counts"]["taken"] == 12
    assert result["counts"]["delivered_to_outreach"] == 9


def test_widening_the_location_band_flips_exactly_one_bakery():
    """Changing the criteria changes the verdicts; the bakeries did not change."""
    result = qualify_leads(bakery_list_records(), ICP(location_max=5))
    qualified_names = names_in(entry for entry in result["records"] if entry["qualified"])
    assert result["counts"]["qualified"] == 10 and result["counts"]["chased"] == 3
    assert "Butter & Thyme Bakery" in qualified_names
    assert "Golden Wheat Bakery Cafe" in names_in(result["taken_records"])
    assert "Fig & Feather Bakery" in names_in(result["taken_records"])
    assert "Oak Street Bakes" in names_in(result["taken_records"])
    assert result["icp"]["location_count"] == [1, 5]


def test_buckets_are_exhaustive_on_the_bakery_list():
    counts = qualify_leads(bakery_list_records())["counts"]
    assert (counts["qualified"] + counts["chased"] + counts["taken"]
            + counts["needs_review"]) == len(BAKERY_LIST_AFTER_ENRICHMENT) == 23


# ------------------------------------------------------- shape of the handoff --

def test_entry_shape_matches_the_games_handoff():
    entry = qualify_leads([bakery_lead()])["records"][0]
    for key in ("id", "name", "owner_name", "owner_role", "review_count",
                "location_count", "ordering_platform", "qualified", "junk",
                "value", "contact", "reasons", "at", "lead"):
        assert key in entry, f"missing {key} — delivery_handoff.js expects it"
    assert entry["contact"] == "pat@practice-bakery.example"


def test_payload_records_the_rule_that_was_applied():
    result = qualify_leads([bakery_lead()])
    assert result["key"] == QUALIFIED_KEY and result["stage"] == "qualify"
    assert result["icp"] == {
        "review_count": [20, 1000],
        "location_count": [1, 3],
        "owner_role": ["Manager", "Owner"],
        "ordering_platform": "none",
        "online_ordering_platforms": list(ONLINE_ORDERING_PLATFORMS),
    }


def test_icp_rejects_an_inverted_role_band():
    with pytest.raises(ValueError):
        ICP(role_min="Owner", role_max="Manager")


def test_icp_rejects_a_role_off_the_ladder():
    with pytest.raises(ValueError):
        ICP(role_min="Supreme Chancellor")


def test_icp_rejects_an_inverted_number_band():
    with pytest.raises(ValueError):
        ICP(location_min=5, location_max=3)


def test_role_ladder_runs_from_counter_staff_to_corporate_office():
    assert OWNER_ROLE_LADDER == ("Counter Staff", "Baker", "Shift Lead", "Manager",
                                 "General Manager", "Co-owner", "Owner",
                                 "Corporate Office")


# ------------------------------------------------------------ command line --

def test_command_line_reads_a_file_and_writes_the_four_buckets(tmp_path, capsys):
    enriched_path = tmp_path / "enriched.json"
    qualified_path = tmp_path / "qualified.json"
    enriched_path.write_text(json.dumps({"records": bakery_list_records()}))

    assert main(["--in", str(enriched_path), "--out", str(qualified_path)]) == 0
    written = json.loads(qualified_path.read_text())
    assert written["counts"]["qualified"] == 9 and written["counts"]["taken"] == 8
    assert "9 qualified; 12 rejected; 9 to outreach" in capsys.readouterr().out


def test_command_line_flags_tune_the_bands(tmp_path):
    enriched_path = tmp_path / "enriched.json"
    qualified_path = tmp_path / "qualified.json"
    enriched_path.write_text(json.dumps(bakery_list_records()))

    main(["--in", str(enriched_path), "--out", str(qualified_path),
          "--location-max", "5", "--no-chase"])
    written = json.loads(qualified_path.read_text())
    assert written["icp"]["location_count"] == [1, 5]
    assert written["counts"]["qualified"] == 10 and written["counts"]["chased"] == 0


# -------------------------------------------------------------- end to end --

def test_end_to_end_buckets_are_exhaustive(qualified, enriched):
    counts = qualified["counts"]
    assert (counts["qualified"] + counts["chased"] + counts["taken"]
            + counts["needs_review"] == enriched["counts"]["enriched"])


def test_end_to_end_the_two_needs_review_bakeries_are_rye_and_dos_rios(qualified):
    reasons = {entry["name"]: entry["reasons"] for entry in qualified["review_records"]}
    assert reasons == {
        "Rye & Rosemary Bakery": ["ordering_platform:unknown:awaiting_check"],
        "Dos Rios Panaderia": ["ordering_platform:unknown:unclear"]}
    assert qualified["counts"]["missed"] == 1
