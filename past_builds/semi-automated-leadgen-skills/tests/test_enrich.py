"""Stage 2 tests — the contact waterfall (Apollo, then Hunter for the gaps), the
research agent for ordering_platform, and your check before an agent answer
enters the record.

The raw leads here are built by hand from the listing rows, on purpose. This
file tests Enrich; it must not fail because Source did.
"""

from __future__ import annotations

import json

import pytest

from pipeline.enrich import (enrich_leads, enrich_one, enrichment_counts,
                             fields_still_missing, look_up_contacts, main)
from pipeline.providers.bakery_enrichment_fixture import BAKERY_ENRICHMENT_ROWS
from pipeline.providers.enrichment import (APOLLO_FIELDS, DEFAULT_AGENT_ANSWERS,
                                           HUNTER_FIELDS, MockApolloProvider,
                                           MockHunterProvider,
                                           RecordedResearchAgent,
                                           get_contact_data_provider,
                                           get_contact_data_providers,
                                           ordering_platform_from_answer)
from pipeline.review_research import DEFAULT_CONFIRMATIONS, load_confirmations
from pipeline.schema import (CONTACT_FIELDS, ENRICHED_FIELDS,
                             ONLINE_ORDERING_PLATFORMS, OWNER_ROLE_LADDER,
                             is_raw_lead, make_lead, stable_lead_id)

# The course's contact waterfall, offline: Apollo first, then Hunter.
WATERFALL = [MockApolloProvider(), MockHunterProvider()]


class SpyProvider:
    """A contact provider that records which fields it was asked for."""

    live = False
    fields_it_can_find = HUNTER_FIELDS
    cost_per_hit = 0.010
    cost_per_miss = 0.003

    def __init__(self, name="spy_provider", answers=None):
        self.name = name
        self.answers = answers or {}
        self.asked: list[list[str]] = []

    def lookup(self, lead, fields):
        self.asked.append(list(fields))
        return {field: self.answers[field] for field in fields if field in self.answers}


class SpyAgent:
    """A research agent that records which bakeries it was asked about."""

    live = False
    name = "spy_agent"
    cost_per_run = 0.09

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.asked: list[str] = []

    def research_ordering_platform(self, lead):
        self.asked.append(lead["name"])
        return self.answers.get(lead["id"])


def raw_lead_for(fixture_row: dict) -> dict:
    """One hand-built raw lead: the shape Source hands over."""
    return make_lead(stable_lead_id(fixture_row["place_id"]),
                     fixture_row["bakery_name"],
                     category="Bakery", city="Chicago", state="Illinois",
                     place_id=fixture_row["place_id"])


@pytest.fixture
def bakery_leads() -> list[dict]:
    """The 23 leads Source hands over: 25 rows minus the 2 repeats."""
    return [raw_lead_for(row) for row in BAKERY_ENRICHMENT_ROWS
            if "repeat_of_listing_row" not in row]


@pytest.fixture
def enriched_bakeries(bakery_leads) -> dict:
    return enrich_leads(bakery_leads)


@pytest.fixture
def enriched_by_name(enriched_bakeries) -> dict[str, dict]:
    return {record["name"]: record for record in enriched_bakeries["records"]}


def by_name(result):
    return {record["name"]: record for record in result["records"]}


# ------------------------------------------------------------ the shape of Enrich

def test_enrich_fields_are_four_contact_fields_plus_ordering_platform():
    assert CONTACT_FIELDS == ("owner_name", "owner_role", "email", "location_count")
    assert ENRICHED_FIELDS == CONTACT_FIELDS + ("ordering_platform",)
    assert APOLLO_FIELDS == ENRICHED_FIELDS
    assert HUNTER_FIELDS == ("owner_name", "owner_role", "email")
    assert "instagram" not in ENRICHED_FIELDS


def test_the_waterfall_is_apollo_then_hunter_cheapest_first():
    apollo, hunter = get_contact_data_providers()
    assert isinstance(apollo, MockApolloProvider)
    assert isinstance(hunter, MockHunterProvider)
    assert (apollo.name, apollo.cost_per_hit, apollo.cost_per_miss) == (
        "apollo(mock)", 0.004, 0.001)
    assert (hunter.name, hunter.cost_per_hit, hunter.cost_per_miss) == (
        "hunter(mock)", 0.010, 0.003)
    assert hunter.cost_per_hit > apollo.cost_per_hit
    assert hunter.cost_per_miss > apollo.cost_per_miss


def test_each_live_key_switches_only_its_own_provider(monkeypatch):
    monkeypatch.setenv("APOLLO_API_KEY", "test-key")
    apollo, hunter = get_contact_data_providers()
    assert apollo.live is True and apollo.name == "apollo"
    assert hunter.live is False
    monkeypatch.delenv("APOLLO_API_KEY")
    monkeypatch.setenv("HUNTER_API_KEY", "test-key")
    apollo, hunter = get_contact_data_providers()
    assert apollo.live is False
    assert hunter.live is True and hunter.name == "hunter"
    assert get_contact_data_provider().live is False


def test_apollo_is_asked_for_all_five_fields_once(bakery_leads):
    columns, ledger = look_up_contacts(bakery_leads[0], WATERFALL)
    assert [line["field"] for line in ledger] == list(APOLLO_FIELDS)
    assert {line["provider"] for line in ledger} == {"apollo(mock)"}


def test_hunter_is_asked_only_for_what_apollo_left_open(bakery_leads):
    tin_roof = next(lead for lead in bakery_leads if lead["name"] == "Tin Roof Bakery")
    spy = SpyProvider()
    look_up_contacts(tin_roof, [MockApolloProvider(), spy])
    assert spy.asked == [["email"]]


def test_hunter_is_never_asked_when_apollo_answered_everything_it_can(bakery_leads):
    spy = SpyProvider()
    for lead in bakery_leads:
        if lead["name"] not in ("Tin Roof Bakery", "Morning Crumb", "Fig & Feather Bakery"):
            look_up_contacts(lead, [MockApolloProvider(), spy])
    assert spy.asked == []


def test_hunter_is_never_asked_for_location_count_or_ordering_platform():
    stranger = make_lead(stable_lead_id("some-other-place"), "Unlisted Bakery",
                         place_id="some-other-place")
    spy = SpyProvider()
    look_up_contacts(stranger, [MockApolloProvider(), spy])
    assert spy.asked == [["owner_name", "owner_role", "email"]]


def test_a_contact_on_staff_counts_as_no_owner_yet():
    columns = {"owner_name": "Wes Adeyemi", "owner_role": "Baker",
               "email": "wes@x.example", "location_count": 1, "ordering_platform": None}
    assert fields_still_missing(columns) == [
        "owner_name", "owner_role", "email", "ordering_platform"]
    columns["owner_role"] = "General Manager"
    assert fields_still_missing(columns) == ["ordering_platform"]


def test_hunter_finds_tin_roofs_email(bakery_leads):
    tin_roof = next(lead for lead in bakery_leads if lead["name"] == "Tin Roof Bakery")
    columns, ledger = look_up_contacts(tin_roof, WATERFALL)
    assert columns["email"] == "luis@tin-roof-bakery.example"
    assert columns["owner_name"] == "Luis Navarro"
    assert [(line["field"], line["provider"], line["hit"], line["cost"])
            for line in ledger if line["provider"] == "hunter(mock)"] == [
        ("email", "hunter(mock)", True, 0.010)]


def test_hunter_replaces_the_baker_with_morning_crumbs_owner(bakery_leads):
    morning_crumb = next(lead for lead in bakery_leads if lead["name"] == "Morning Crumb")
    columns, ledger = look_up_contacts(morning_crumb, WATERFALL)
    assert (columns["owner_name"], columns["owner_role"], columns["email"]) == (
        "Nora Lindqvist", "Owner", "nora.morning-crumb@mail.example")
    assert [line["field"] for line in ledger if line["provider"] == "hunter(mock)"] == [
        "owner_name", "owner_role", "email"]


def test_a_new_person_never_keeps_the_last_persons_email(bakery_leads):
    morning_crumb = next(lead for lead in bakery_leads if lead["name"] == "Morning Crumb")
    finds_a_name_only = SpyProvider(answers={"owner_name": "Someone Else"})
    columns, ledger = look_up_contacts(morning_crumb,
                                       [MockApolloProvider(), finds_a_name_only])
    assert columns["owner_name"] == "Someone Else"
    assert columns["owner_role"] is None and columns["email"] is None


def test_when_hunter_misses_the_staff_contact_stays(bakery_leads):
    fig = next(lead for lead in bakery_leads if lead["name"] == "Fig & Feather Bakery")
    columns, ledger = look_up_contacts(fig, WATERFALL)
    assert (columns["owner_name"], columns["owner_role"]) == ("Jamie Ortiz", "Shift Lead")
    assert [(line["field"], line["hit"], line["cost"])
            for line in ledger if line["provider"] == "hunter(mock)"] == [
        ("owner_name", False, 0.003), ("owner_role", False, 0.003),
        ("email", False, 0.003)]


def test_misses_still_cost_money():
    stranger = make_lead(stable_lead_id("some-other-place"), "Unlisted Bakery",
                         place_id="some-other-place")
    record = enrich_one(stranger, WATERFALL, None, {})
    assert record["enrichment"]["filled"] == []
    # Apollo: 5 misses x $0.001. Hunter: 3 misses x $0.003.
    assert record["enrichment"]["cost"] == 0.014
    assert record["enrichment"]["research"]["status"] == "not_run"


# ------------------------------------------------------- the research agent step

def test_apollo_answers_ordering_platform_for_only_three(enriched_bakeries):
    from_apollo = sorted(record["name"] for record in enriched_bakeries["records"]
                         if record["enrichment"]["research"]["status"] == "not_needed")
    assert from_apollo == ["Daily Dozen Donuts & Bakery", "Poppyseed Bakehouse",
                           "Velvet Whisk Patisserie"]


def test_the_agent_is_asked_only_where_apollo_had_no_answer(bakery_leads):
    agent = SpyAgent()
    enrich_leads(bakery_leads, research_agent=agent, confirmations=[])
    assert len(agent.asked) == 20
    assert "Velvet Whisk Patisserie" not in agent.asked


def test_the_agent_fills_ordering_platform_only(bakery_leads):
    """Even when it returns an answer, the agent never touches another field."""
    lead = next(lead for lead in bakery_leads if lead["name"] == "Morning Crumb")
    record = enrich_one(lead, WATERFALL, RecordedResearchAgent(), {})
    agent_lines = [line for line in record["enrichment"]["ledger"]
                   if line["provider"] == "research_agent(recorded)"]
    assert [line["field"] for line in agent_lines] == ["ordering_platform"]


def test_no_agent_leaves_ordering_platform_empty(bakery_leads):
    agent = SpyAgent()
    result = enrich_leads(bakery_leads, use_agent=False, research_agent=agent)
    assert agent.asked == []
    assert result["counts"]["complete"] == 3
    assert result["counts"]["researched"] == 0
    assert result["cost"]["by_provider"] == {"apollo(mock)": 0.397,
                                             "hunter(mock)": 0.049}


@pytest.mark.parametrize("verdict,kind,expected", [
    (True, None, "none"),
    (False, "pos_online_store", "pos_online_store"),
    (False, None, None),
])
def test_an_agent_verdict_maps_to_ordering_platform(verdict, kind, expected):
    answer = {"verdict": {"supportsNoOnlineOrdering": verdict}, "platform_kind": kind}
    assert ordering_platform_from_answer(answer) == expected


def test_recorded_answers_use_repository_2s_verdict_shape():
    payload = json.loads(DEFAULT_AGENT_ANSWERS.read_text())
    assert payload["field"] == "ordering_platform"
    assert len(payload["answers"]) == 20
    for answer in payload["answers"]:
        assert set(answer["verdict"]) == {"supportsNoOnlineOrdering", "researchReason",
                                          "sourceUrls"}
        assert answer["evidence"]["source_url"] in answer["verdict"]["sourceUrls"]
        assert answer["evidence"]["quote"].strip()
        assert ".example" in answer["evidence"]["source_url"]
        if answer["platform_kind"] is not None:
            assert answer["verdict"]["supportsNoOnlineOrdering"] is False
            assert answer["platform_kind"] in ONLINE_ORDERING_PLATFORMS


# ------------------------------------------------------------------ your check

def test_an_unconfirmed_answer_does_not_enter_the_record(bakery_leads):
    result = by_name(enrich_leads(bakery_leads, confirmations=[]))
    sunrise = result["Sunrise Bakery"]
    assert sunrise["ordering_platform"] is None
    assert sunrise["enrichment"]["research"]["status"] == "awaiting_check"
    assert sunrise["enrichment"]["research"]["proposed_value"] == "none"


def test_a_confirmed_answer_enters_the_record_with_who_and_source(enriched_by_name):
    sunrise = enriched_by_name["Sunrise Bakery"]
    research = sunrise["enrichment"]["research"]
    assert sunrise["ordering_platform"] == "none"
    assert research["status"] == "confirmed"
    assert research["confirmed_by"] == "course fixture (checked in the lesson)"
    assert research["source_url"] == "https://sunrise.example/menu"
    assert research["quote"]


def test_the_course_confirmations_cover_18_answers_not_rye_or_dos_rios():
    names = {entry["bakery_name"] for entry in load_confirmations(DEFAULT_CONFIRMATIONS)}
    assert len(names) == 18
    assert "Rye & Rosemary Bakery" not in names and "Dos Rios Panaderia" not in names


def test_a_confirmation_that_does_not_match_the_answer_is_not_applied(bakery_leads):
    lead = next(lead for lead in bakery_leads if lead["name"] == "Sunrise Bakery")
    wrong = {"lead_id": lead["id"], "field": "ordering_platform",
             "value": "pos_online_store", "source_url": "https://sunrise.example/menu",
             "quote": "x", "reviewed_by": "Someone"}
    record = enrich_one(lead, WATERFALL, RecordedResearchAgent(),
                        {lead["id"]: wrong})
    assert record["ordering_platform"] is None
    assert record["enrichment"]["research"]["status"] == "awaiting_check"


# ---------------------------------------------------- the 23 bakeries, enriched

def test_designed_counts_on_the_bakery_list(enriched_bakeries):
    assert enriched_bakeries["counts"] == {
        "enriched": 23, "complete": 21, "ordering_from_apollo": 3,
        "researched": 20, "confirmed": 18, "awaiting_check": 1, "unclear": 1}
    assert enriched_bakeries["live"] is False
    assert enriched_bakeries["key"] == "claude3d.enriched.v1"


def test_designed_cost_on_the_bakery_list(enriched_bakeries):
    cost = enriched_bakeries["cost"]
    assert cost["total"] == 2.246
    assert cost["per_lead"] == 0.097652
    # Hunter: 4 fields found (Tin Roof's email, Morning Crumb's owner) at
    # $0.010, 3 missed (Fig & Feather's owner) at $0.003.
    assert cost["by_provider"] == {"apollo(mock)": 0.397,
                                   "research_agent(recorded)": 1.8,
                                   "hunter(mock)": 0.049}


def test_rye_awaits_your_check_and_dos_rios_is_unclear(enriched_by_name):
    rye = enriched_by_name["Rye & Rosemary Bakery"]
    assert rye["ordering_platform"] is None
    assert rye["enrichment"]["research"]["status"] == "awaiting_check"
    assert rye["enrichment"]["research"]["source_url"] == "https://rye-and-rosemary.example/order"
    dos_rios = enriched_by_name["Dos Rios Panaderia"]
    assert dos_rios["ordering_platform"] is None
    assert dos_rios["enrichment"]["research"]["status"] == "unclear"
    assert dos_rios["enrichment"]["research"]["proposed_value"] is None


def test_the_two_incomplete_records(enriched_bakeries, enriched_by_name):
    incomplete = sorted(record["name"] for record in enriched_bakeries["records"]
                        if not record["enrichment"]["complete"])
    assert incomplete == ["Dos Rios Panaderia", "Rye & Rosemary Bakery"]
    tin_roof = enriched_by_name["Tin Roof Bakery"]
    assert tin_roof["email"] == "luis@tin-roof-bakery.example"
    assert tin_roof["ordering_platform"] == "pos_online_store"
    assert tin_roof["enrichment"]["complete"] is True


def test_sunrise_bakery_ledger(enriched_by_name):
    sunrise = enriched_by_name["Sunrise Bakery"]
    assert [sunrise[field] for field in ENRICHED_FIELDS] == [
        "Rosa Delgado", "Owner", "rosa@sunrise.example", 1, "none"]
    assert [(line["field"], line["provider"], line["hit"], line["cost"])
            for line in sunrise["enrichment"]["ledger"]] == [
        ("owner_name", "apollo(mock)", True, 0.004),
        ("owner_role", "apollo(mock)", True, 0.004),
        ("email", "apollo(mock)", True, 0.004),
        ("location_count", "apollo(mock)", True, 0.004),
        ("ordering_platform", "apollo(mock)", False, 0.001),
        ("ordering_platform", "research_agent(recorded)", True, 0.09),
    ]
    assert sunrise["enrichment"]["cost"] == 0.107


def test_no_record_carries_an_instagram_column_or_unverified_list(enriched_bakeries):
    for record in enriched_bakeries["records"]:
        assert "instagram" not in record
        assert "unverified" not in record["enrichment"]


def test_ledger_totals_match_the_sum_of_its_lines(enriched_bakeries):
    for record in enriched_bakeries["records"]:
        lines = record["enrichment"]["ledger"]
        assert record["enrichment"]["cost"] == round(sum(line["cost"] for line in lines), 6)
    grand = round(sum(record["enrichment"]["cost"]
                      for record in enriched_bakeries["records"]), 4)
    assert grand == enriched_bakeries["cost"]["total"]


def test_complete_flag_means_every_column_filled(enriched_bakeries):
    for record in enriched_bakeries["records"]:
        filled = all(record.get(field) not in (None, "") for field in ENRICHED_FIELDS)
        assert record["enrichment"]["complete"] is filled


def test_derived_answers_follow_the_record(enriched_by_name):
    assert enriched_by_name["Golden Wheat Bakery Cafe"]["is_chain"] is True
    assert enriched_by_name["Porchlight Pastry"]["location_count"] == 0
    assert enriched_by_name["Porchlight Pastry"]["is_chain"] is False
    assert enriched_by_name["Oak Street Bakes"]["takes_online_orders"] is True
    assert enriched_by_name["Rye & Rosemary Bakery"]["takes_online_orders"] is None


def test_listing_columns_pass_through_untouched(bakery_leads, enriched_by_name):
    for lead in bakery_leads:
        record = enriched_by_name[lead["name"]]
        for listing_field in ("name", "address", "phone", "website", "rating", "review_count"):
            assert record[listing_field] == lead[listing_field]


def test_a_listing_handed_over_twice_is_billed_once(bakery_leads):
    twice = enrich_leads(bakery_leads + bakery_leads[:5])
    assert twice["counts"]["enriched"] == 23
    assert twice["cost"]["total"] == 2.246


def test_a_lead_is_found_by_place_id_when_its_id_is_numbered_differently():
    lead = make_lead("my-own-numbering-0001", "Bluebird Bakery",
                     place_id="fixture-place-006")
    record = enrich_one(lead, WATERFALL, None, {})
    assert record["owner_name"] == "Hannah Kowalski"


# ------------------------------------------------------------ the fixture itself

def test_fixture_has_25_rows_and_23_bakeries():
    assert [row["listing_row"] for row in BAKERY_ENRICHMENT_ROWS] == list(range(1, 26))
    repeats = {row["listing_row"]: row["repeat_of_listing_row"]
               for row in BAKERY_ENRICHMENT_ROWS if "repeat_of_listing_row" in row}
    assert repeats == {7: 1, 16: 5}
    for row in BAKERY_ENRICHMENT_ROWS:
        assert row["lead_id"] == stable_lead_id(row["place_id"])


def test_fixture_values_obey_the_schema():
    for row in BAKERY_ENRICHMENT_ROWS:
        values = row["apollo"]
        assert tuple(values) == ENRICHED_FIELDS
        if values["owner_role"] is not None:
            assert values["owner_role"] in OWNER_ROLE_LADDER
        if values["ordering_platform"] is not None:
            assert values["ordering_platform"] in ("none",) + ONLINE_ORDERING_PLATFORMS
        if values["email"] is not None:
            assert values["email"].endswith(".example")
        hunter_values = row.get("hunter", {})
        assert set(hunter_values) <= set(HUNTER_FIELDS)
        if "owner_role" in hunter_values:
            assert hunter_values["owner_role"] in OWNER_ROLE_LADDER
        if "email" in hunter_values:
            assert hunter_values["email"].endswith(".example")


def test_hunter_knows_only_the_two_apollo_gaps():
    """Morning Crumb (and its repeated listing) and Tin Roof Bakery."""
    names = sorted(row["bakery_name"] for row in BAKERY_ENRICHMENT_ROWS if "hunter" in row)
    assert names == ["Morning Crumb", "Morning Crumb Bakery & Cafe", "Tin Roof Bakery"]


# ------------------------------------------------------------------ the command

def test_command_line_writes_the_handoff_and_never_double_bills(bakery_leads, tmp_path, capsys):
    leads_file = tmp_path / "leads.json"
    enriched_file = tmp_path / "enriched.json"
    leads_file.write_text(json.dumps({"records": bakery_leads}))

    assert main(["--in", str(leads_file), "--out", str(enriched_file)]) == 0
    first = json.loads(enriched_file.read_text())
    assert first["counts"]["complete"] == 21
    assert ("23 enriched (21 complete, 1 agent answer(s) awaiting your check) $2.2460"
            in capsys.readouterr().out)

    assert not any(is_raw_lead(record) for record in first["records"])
    assert main(["--in", str(enriched_file), "--out", str(enriched_file)]) == 0
    second = json.loads(enriched_file.read_text())
    assert second["counts"]["enriched"] == 0 and second["cost"]["total"] == 0
    assert len(second["records"]) == 23


def test_command_line_refuses_a_missing_confirmations_file(bakery_leads, tmp_path, capsys):
    """A typo in --confirmations must not silently drop every checked answer."""
    leads_file = tmp_path / "leads.json"
    enriched_file = tmp_path / "enriched.json"
    leads_file.write_text(json.dumps({"records": bakery_leads}))
    enriched_file.write_text("previous run\n")

    with pytest.raises(SystemExit) as refusal:
        main(["--in", str(leads_file), "--out", str(enriched_file),
              "--confirmations", str(tmp_path / "my_checks.json")])

    assert refusal.value.code == 2
    assert "--confirmations file not found" in capsys.readouterr().err
    assert enriched_file.read_text() == "previous run\n"


def test_command_line_no_agent_flag(bakery_leads, tmp_path):
    leads_file = tmp_path / "leads.json"
    enriched_file = tmp_path / "enriched.json"
    leads_file.write_text(json.dumps({"records": bakery_leads}))
    main(["--in", str(leads_file), "--out", str(enriched_file), "--no-agent"])
    written = json.loads(enriched_file.read_text())
    assert enrichment_counts(written["records"])["researched"] == 0
