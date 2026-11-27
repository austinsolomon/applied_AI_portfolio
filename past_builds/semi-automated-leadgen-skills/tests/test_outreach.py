"""Stage 4 tests — the lint fires before anything is sent, the allocator learns,
and 'sent' is never reported as 'delivered'.

THE TWELVE BAKERIES BELOW ARE BUILT BY HAND, in the order the qualify stage
hands them over: nine qualified in listing order, then four chased. So this
file tests the outreach stage alone and keeps passing while an earlier stage
is being changed."""

from __future__ import annotations

import json

import pytest

from pipeline.outreach import (allocate, fit_to_channel, lint, main,
                               plan_sequence, run_outreach)
from pipeline.providers.bakery_outreach_fixture import (
    BAKERY_OUTREACH_ROWS, BAKERY_RUN_CHANNELS, MESSAGE_TEMPLATES, OFFER,
    BakeryFixtureSendProvider, BakeryTemplateCopyProvider)
from pipeline.providers.copy import (CHANNEL_MODEL, ClaudeCopyProvider,
                                     get_copy_provider, get_send_provider)
from pipeline.schema import (HELD_RECORD_FIELDS, OUTREACH_KEY,
                             OUTREACH_PLACEHOLDERS, SEND_RESULT_FIELDS,
                             stable_lead_id)

# (listing row, bakery name, owner name, review count, outcome at qualify,
#  email). Every email here is one Apollo found, except Morning Crumb's: Apollo
#  had only a baker there, and Hunter found the owner.
DELIVERY_LIST = (
    (1, "Sunrise Bakery", "Rosa Delgado", 212, "qualified",
     "rosa@sunrise.example"),
    (2, "Riverbend Bread", "Tomas Whitaker", 148, "qualified",
     "tomas@riverbend-bread.example"),
    (4, "Flour & Hearth", "Anjali Nair", 96, "qualified",
     "anjali.flour-and-hearth@mail.example"),
    (5, "Morning Crumb", "Nora Lindqvist", 51, "qualified",
     "nora.morning-crumb@mail.example"),
    (6, "Bluebird Bakery", "Hannah Kowalski", 64, "qualified",
     "hannah@bluebird-bakery.example"),
    (9, "Lantern Lane Pies", "Gus Hartley", 310, "qualified",
     "gus@lantern-lane-pies.example"),
    (12, "Sweet Thistle Bakeshop", "Imani Brooks", 420, "qualified",
     "imani@sweet-thistle.example"),
    (19, "Honeycomb Oven", "Mei-Lin Chau", 37, "qualified",
     "mei-lin.honeycomb-oven@mail.example"),
    (25, "Night Owl Bagels & Bakes", "Carmen Ibarra", 205, "qualified",
     "carmen@night-owl-bagels.example"),
    (11, "Butter & Thyme Bakery", "Felipe Arroyo", 350, "chased",
     "felipe@butter-and-thyme.example"),
    (13, "Tiny Kiln Bakery", "Noelle Park", 9, "chased",
     "noelle.tiny-kiln-bakery@mail.example"),
    (21, "Grand Avenue Cake Hall", "Beatrice Langford", 2340, "chased",
     "beatrice@grand-avenue-cake-hall.example"),
    (24, "Porchlight Pastry", "Dev Malhotra", 22, "chased",
     "dev.porchlight-pastry@mail.example"),
)


def lead_id_for_listing_row(listing_row: int) -> str:
    return stable_lead_id(f"fixture-place-{listing_row:03d}")


def build_delivery_entry(listing_row, bakery_name, owner_name, review_count, outcome,
                         email):
    """One entry shaped like the qualify stage's output: a summary plus the lead."""
    lead = {"id": lead_id_for_listing_row(listing_row), "name": bakery_name,
            "owner_name": owner_name, "review_count": review_count,
            "email": email, "owner_role": "Owner", "location_count": 1, "ordering_platform": "none",
            "city": "Chicago", "state": "IL"}
    return {"id": lead["id"], "name": bakery_name, "owner_name": owner_name,
            "review_count": review_count, "qualified": outcome == "qualified",
            "junk": outcome == "chased", "lead": lead}


@pytest.fixture
def delivery_records() -> list[dict]:
    return [build_delivery_entry(*row) for row in DELIVERY_LIST if row[4] == "qualified"]


@pytest.fixture
def outreach_result(delivery_records) -> dict:
    return run_outreach(delivery_records)


def send_result_for(outreach_result, listing_row):
    lead_id = lead_id_for_listing_row(listing_row)
    return next(row for row in outreach_result["records"] if row["lead_id"] == lead_id)


def sent_message_for(outreach_result, listing_row):
    lead_id = lead_id_for_listing_row(listing_row)
    return next(row["message"] for row in outreach_result["sent_messages"]
                if row["lead_id"] == lead_id)


def held_record_for(outreach_result, bakery_name):
    return next(row for row in outreach_result["held_records"]
                if row["lead"]["name"] == bakery_name)


# -------------------------------------------------------------- the providers --
def test_offline_providers_selected_without_keys():
    assert not get_copy_provider().live
    assert not get_send_provider().live


def test_live_copy_provider_selected_with_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
    provider = get_copy_provider()
    assert provider.live and isinstance(provider, ClaudeCopyProvider)
    assert provider.model == "claude-opus-5"


def test_a_sender_key_stops_the_run_instead_of_mailing_anyone(monkeypatch, delivery_records):
    """The live sender is left unimplemented on purpose. A key must not send."""
    from pipeline.providers.base import ProviderError
    monkeypatch.setenv("EMAIL_SENDER_API_KEY", "not-a-real-key")
    with pytest.raises(ProviderError):
        run_outreach(delivery_records)


def test_fixture_lead_ids_match_the_source_stage_ids():
    for row in BAKERY_OUTREACH_ROWS:
        assert row["lead_id"] == stable_lead_id(row["place_id"])
        assert row["place_id"] == f"fixture-place-{row['listing_row']:03d}"
    assert [row["bakery_name"] for row in BAKERY_OUTREACH_ROWS] == \
           [row[1] for row in DELIVERY_LIST]


# ------------------------------------------------------------------- the lint --
def test_clean_message_passes():
    assert lint({"subject": "Online orders for Sunrise Bakery",
                 "body": "Hi Rosa, a short note from Chicago, IL."}) == []


def test_all_caps_is_caught():
    assert any(problem.startswith("all-caps") for problem in
               lint({"subject": "URGENT OFFER", "body": "hi"}))


def test_common_acronyms_are_not_treated_as_shouting():
    """A lint that flags ordinary abbreviations is a lint everyone learns to override."""
    assert lint({"subject": "CRM + API", "body": "Your SDR team and its ICP."}) == []


def test_link_count_is_capped():
    body = "See https://a.example and https://b.example"
    assert "links:2>1" in lint({"subject": "s", "body": body})


def test_spam_words_are_caught():
    problems = lint({"subject": "s", "body": "A guaranteed risk-free trial."})
    assert "spam-words:free,guarantee,guaranteed,risk-free" in problems


def test_the_word_check_looks_inside_words():
    """Bakery copy has its own traps. Reword the sentence, never the check."""
    assert "spam-words:free" in lint({"subject": "s", "body": "We love your gluten-free loaf."})
    assert "spam-words:winner" in lint({"subject": "s", "body": "An award-winner pie."})
    assert "all-caps:PDF" in lint({"subject": "s", "body": "Your menu is a PDF."})


def test_more_than_one_exclamation_mark_is_caught():
    assert lint({"subject": "s", "body": "Hello!"}) == []
    assert "exclamations:2" in lint({"subject": "s", "body": "Hello! Hello!"})


def test_overlong_body_is_caught():
    assert any(problem.startswith("length:") for problem in
               lint({"subject": "s", "body": "word " * 200}))


def test_failing_messages_are_held_not_sent():
    """The order that matters: lint runs before send, and a fail means no send."""
    class ShoutingWriter:
        live = False
        name = "shouting"

        def write(self, lead, offer):
            return {"subject": "FREE MONEY NOW", "body": "GUARANTEED!!! click here"}

    class SenderThatMustNotBeCalled:
        live = False
        name = "unused"

        def send(self, lead, channel, message):
            raise AssertionError("a held message reached the sender")

    result = run_outreach([{"lead": {"id": "bakery-a", "name": "A Bakery"}}],
                          copy_provider=ShoutingWriter(),
                          send_provider=SenderThatMustNotBeCalled())
    assert result["counts"]["held"] == 1
    assert result["counts"]["sent"] == 0
    assert result["held_records"][0]["lint"]


# ------------------------------------------------------ the offline copy writer --
def test_every_template_uses_only_the_four_placeholders():
    import string
    for channel in BAKERY_RUN_CHANNELS:
        template = MESSAGE_TEMPLATES[channel]
        used = {name for part in (template["subject"], template["body"])
                for _, name, _, _ in string.Formatter().parse(part) if name}
        assert used <= set(OUTREACH_PLACEHOLDERS)


def test_the_sunrise_email_reads_as_the_course_shows_it(outreach_result):
    message = sent_message_for(outreach_result, 1)
    assert message["subject"] == "Online orders for Sunrise Bakery"
    assert message["body"] == (
        "Hi Rosa,\n\n"
        "I saw Sunrise Bakery has 212 reviews on Google Maps, and that the menu "
        "on your site still asks people to call in cake orders.\n\n"
        "I build simple websites for Chicago bakeries that let customers order "
        "and pay online, so the phone can stay quiet during the morning rush. "
        "A site like that is $500, one time.\n\n"
        "Would it help if I sent a two-minute example made with your menu?\n\n"
        "Jordan Ellis")
    assert len(message["body"].split()) == 74


def test_optional_channel_templates_are_available_but_default_run_is_email(outreach_result):
    assert MESSAGE_TEMPLATES["instagram_dm"]["subject"] == ""
    assert all(row["channel"] == "email" for row in outreach_result["records"])


def test_every_sent_message_passes_the_lint_and_stays_short(outreach_result):
    for row in outreach_result["sent_messages"]:
        assert lint(row["message"]) == []
        assert len(row["message"]["body"].split()) <= 90
        assert row["message"]["body"].endswith("Jordan Ellis")


def test_the_offline_writer_produces_some_lintable_copy(delivery_records):
    """The lint must have something to catch on a fresh run, or nobody sees it work."""
    writer = BakeryTemplateCopyProvider()
    problems = [lint(writer.write(entry["lead"], OFFER)) for entry in delivery_records]
    assert any(problems), "no spammy sample — the lint would look decorative"
    assert not all(problems), "every sample spammy — the offline copy is broken"


def test_bad_qualified_draft_is_held_with_named_reasons(outreach_result):
    assert [row["lead"]["name"] for row in outreach_result["held_records"]] == ["Night Owl Bagels & Bakes"]
    assert "links:3>1" in held_record_for(outreach_result, "Night Owl Bagels & Bakes")["lint"]


def test_rejected_or_unknown_records_never_reach_copy_or_sender():
    class Forbidden:
        live = False
        def write(self, *args): raise AssertionError("unqualified lead reached writer")
        def send(self, *args): raise AssertionError("unqualified lead reached sender")
    rejected = build_delivery_entry(*DELIVERY_LIST[9])
    unknown = build_delivery_entry(*DELIVERY_LIST[0])
    unknown['lead']['ordering_platform'] = None
    result=run_outreach([rejected,unknown],copy_provider=Forbidden(),send_provider=Forbidden())
    assert result['counts']['sent'] == 0 and result['counts']['held'] == 2
    assert all('not-qualified' in entry['lint'] for entry in result['held_records'])


def test_a_bakery_the_fixture_has_never_seen_still_gets_a_clean_message():
    lead = {"id": "your-own-search-001", "name": "Corner Loaf", "review_count": 40}
    message = BakeryTemplateCopyProvider().write(lead, OFFER)
    assert message["body"].startswith("Hi there,")
    assert "customers still order by phone or in person" in message["body"]
    assert lint(message) == []


def test_a_reworded_message_that_fails_the_lint_is_never_sent():
    class WriterWhoseRewordingShouts:
        def write_for_channel(self, lead, offer, channel):
            return {"subject": "", "body": "ACT NOW!!!"}

    linted_draft = {"subject": "s", "body": "A calm note."}
    item = {"lead": {"id": "bakery-a"}, "message": linted_draft, "lint": []}
    assert fit_to_channel(WriterWhoseRewordingShouts(), item, OFFER, "instagram_dm") is linted_draft


def test_a_writer_that_cannot_reword_keeps_its_draft():
    class WriterWithOnlyWrite:
        pass

    linted_draft = {"subject": "s", "body": "A calm note."}
    item = {"lead": {"id": "bakery-a"}, "message": linted_draft, "lint": []}
    assert fit_to_channel(WriterWithOnlyWrite(), item, OFFER, "email") is linted_draft


# ------------------------------------------------------------- the allocator --
def test_round_one_is_an_even_split():
    split = allocate(12, ["email", "linkedin_inmail", "instagram_dm"])
    assert split == {"email": 4, "linkedin_inmail": 4, "instagram_dm": 4}


def test_even_split_distributes_the_remainder():
    """Ten bakery messages over three channels: 3 each, and the first takes the 1 left."""
    assert allocate(10, ["email", "instagram_dm", "email_campaign_2"]) == {
        "email": 4, "instagram_dm": 3, "email_campaign_2": 3}


def test_later_rounds_weight_by_replies_not_by_delivery():
    """The trap delivery_city sets: the channel that delivers best converts worst."""
    prior = {
        "email": {"sent": 100, "delivered": 88, "replied": 9},
        "linkedin_inmail": {"sent": 100, "delivered": 52, "replied": 26},
    }
    split = allocate(100, ["email", "linkedin_inmail"], prior)
    assert split["linkedin_inmail"] > split["email"]
    assert sum(split.values()) == 100


def test_email_run_stays_on_email(outreach_result):
    assert allocate(7, list(BAKERY_RUN_CHANNELS), outreach_result["by_channel"]) == {"email": 7}


def test_allocator_never_zeroes_a_channel():
    """A channel that can be zeroed can never be discovered to have been good."""
    prior = {"email": {"sent": 100, "replied": 50},
             "instagram_dm": {"sent": 100, "replied": 0}}
    split = allocate(50, ["email", "instagram_dm"], prior)
    assert split["instagram_dm"] >= 1


def test_allocator_falls_back_to_even_when_prior_has_no_replies():
    prior = {"email": {"sent": 10, "replied": 0}, "instagram_dm": {"sent": 10, "replied": 0}}
    assert allocate(4, ["email", "instagram_dm"], prior) == {"email": 2, "instagram_dm": 2}


def test_the_bakery_run_uses_three_of_the_four_lanes(outreach_result):
    assert list(outreach_result["allocation"]) == list(BAKERY_RUN_CHANNELS)
    assert set(BAKERY_RUN_CHANNELS) < set(CHANNEL_MODEL)
    assert "linkedin_inmail" in CHANNEL_MODEL, "the fourth lane stays in the model"


def test_the_channel_rates_are_the_games_and_have_not_been_tidied():
    assert CHANNEL_MODEL == {
        "email":            {"deliver": 0.88, "convert": 0.10},
        "linkedin_inmail":  {"deliver": 0.52, "convert": 0.50},
        "instagram_dm":     {"deliver": 0.70, "convert": 0.22},
        "email_campaign_2": {"deliver": 0.92, "convert": 0.05},
    }


# ------------------------------------------------------- sending and tracking --
def test_send_reports_outcomes_not_just_success():
    result = BakeryFixtureSendProvider().send(
        {"id": "bakery-a"}, "email", {"subject": "s", "body": "b"})
    assert tuple(result) == SEND_RESULT_FIELDS


def test_bounce_only_applies_to_email_channels():
    message = {"subject": "s", "body": "b"}
    for listing_row in range(1, 26):
        lead = {"id": lead_id_for_listing_row(listing_row)}
        for channel in CHANNEL_MODEL:
            result = BakeryFixtureSendProvider().send(lead, channel, message)
            if result["bounced"]:
                assert channel.startswith("email"), f"{channel} reported a bounce"
            assert not (result["replied"] and not result["delivered"])


def test_qualified_email_run_has_separate_delivery_reply_and_booking_counts(outreach_result):
    assert outreach_result["counts"] == {"drafted":8,"held":1,"sent":8,"delivered":5,
      "bounced":3,"replied":2,"spammed":3,"meetings":1,"customers":0}
    assert outreach_result["allocation"] == {"email":8}
    assert outreach_result["by_channel"] == {"email":{"sent":8,"delivered":5,"bounced":3,"replied":2,"reply_rate":0.25}}
    assert [row["lead_id"] for row in outreach_result["records"] if row["replied"]] == [lead_id_for_listing_row(1),lead_id_for_listing_row(19)]


def test_send_rows_and_held_rows_keep_their_shapes(outreach_result):
    assert all(tuple(row) == SEND_RESULT_FIELDS for row in outreach_result["records"])
    assert all(tuple(row) == HELD_RECORD_FIELDS for row in outreach_result["held_records"])


def test_the_same_input_gives_the_same_run_twice(delivery_records):
    first, second = run_outreach(delivery_records), run_outreach(delivery_records)
    for volatile_key in ("at",):
        first.pop(volatile_key), second.pop(volatile_key)
    assert first == second


def test_plain_leads_work_as_well_as_qualified_entries(delivery_records):
    plain_leads = [entry["lead"] for entry in delivery_records]
    assert run_outreach(plain_leads)["counts"]["replied"] == 2


def test_delivered_never_exceeds_sent(outreach_result):
    for tally in outreach_result["by_channel"].values():
        assert tally["delivered"] <= tally["sent"]
        assert tally["replied"] <= tally["delivered"]
    assert outreach_result["counts"]["delivered"] <= outreach_result["counts"]["sent"]


def test_reply_rate_is_per_send_not_per_delivery(outreach_result):
    for tally in outreach_result["by_channel"].values():
        assert tally["reply_rate"] == round(tally["replied"] / max(1, tally["sent"]), 4)


def test_a_second_round_still_sends_every_drafted_message_once(delivery_records, outreach_result):
    second_round = run_outreach(delivery_records, prior=outreach_result["by_channel"])
    assert second_round["counts"]["sent"] == 8
    sent_ids = [row["lead_id"] for row in second_round["records"]]
    assert len(sent_ids) == len(set(sent_ids))
    assert second_round["allocation"] == {"email": 8}


def test_sequencer_plans_but_does_not_send():
    steps = plan_sequence(lead_id_for_listing_row(1), "email")
    assert [step["day_offset"] for step in steps] == [0, 3, 7]
    assert all(step["status"] == "planned" for step in steps)


def test_every_sent_lead_gets_a_sequence(outreach_result):
    assert len(outreach_result["sequence"]) == outreach_result["counts"]["sent"] * 3
    held_ids = {row["lead"]["id"] for row in outreach_result["held_records"]}
    assert not held_ids & {step["lead_id"] for step in outreach_result["sequence"]}


def test_run_is_marked_offline_without_keys(outreach_result):
    assert outreach_result["live"] is False


# ------------------------------------------------------------ the command line --
def test_the_command_line_reads_a_handoff_file_and_writes_one(tmp_path, delivery_records, capsys):
    qualified_path = tmp_path / "qualified.json"
    outreach_path = tmp_path / "outreach.json"
    qualified_path.write_text(json.dumps({"records": delivery_records}))

    assert main(["--in", str(qualified_path), "--out", str(outreach_path)]) == 0
    written = json.loads(outreach_path.read_text())
    assert written["counts"]["sent"] == 8 and written["counts"]["held"] == 1
    assert "sent 8 → delivered 5 → replied 2 (1 held by the lint)" in capsys.readouterr().out


def test_the_command_line_accepts_the_fourth_lane_and_rejects_an_unknown_one(tmp_path, delivery_records):
    qualified_path = tmp_path / "qualified.json"
    qualified_path.write_text(json.dumps({"records": delivery_records}))
    four_lane_path = tmp_path / "four_lanes.json"
    four_lanes = [flag for channel in CHANNEL_MODEL for flag in ("--channel", channel)]
    assert main(["--in", str(qualified_path), "--out", str(four_lane_path)] + four_lanes) == 0
    assert set(json.loads(four_lane_path.read_text())["allocation"]) == set(CHANNEL_MODEL)

    with pytest.raises(SystemExit):
        main(["--in", str(qualified_path), "--channel", "carrier_pigeon"])


# ------------------------------------------------ never write to a blank --
def enriched_style_lead(listing_row, bakery_name, *, email):
    return {"id": lead_id_for_listing_row(listing_row), "name": bakery_name,
            "owner_name": "Sofia Marchetti", "owner_role": "Owner", "review_count": 88,
            "location_count": 1, "ordering_platform": "none",
            "email": email, "owner_role": "Owner", "location_count": 1, "ordering_platform": "none",
            "source": {"place_id": f"fixture-place-{listing_row:03d}"},
            "enrichment": {}}


def test_an_instagram_message_needs_no_bought_column():
    from pipeline.outreach import channel_with_a_verified_address
    lead = enriched_style_lead(2, "Riverbend Bread", email="tomas@riverbend-bread.example")
    assert "instagram" not in lead
    assert channel_with_a_verified_address(lead, "instagram_dm") == "instagram_dm"
    assert channel_with_a_verified_address(lead, "linkedin_inmail") == "email"


def test_a_lead_with_no_email_is_held_on_an_email_lane():
    lead = enriched_style_lead(2, "Riverbend Bread", email=None)
    result = run_outreach([lead], channels=["email"])
    assert result["counts"]["sent"] == 0 and result["counts"]["held"] == 1
    assert result["held_records"][0]["lint"] == ["no-verified-address"]
    assert tuple(result["held_records"][0]) == HELD_RECORD_FIELDS
    assert result["sequence"] == [], "no follow-up is planned for a message never sent"


def test_the_address_rule_never_fires_on_the_course_list(outreach_result):
    reasons = [reason for row in outreach_result["held_records"] for reason in row["lint"]]
    assert "no-verified-address" not in reasons


def test_a_chased_lead_never_replies_on_any_channel():
    """It was outside the rules when it was sent: a message spent, nothing won."""
    sender = BakeryFixtureSendProvider()
    message = {"subject": "", "body": ""}
    for listing_row in range(1, 26):
        lead = {"id": lead_id_for_listing_row(listing_row)}
        for channel in CHANNEL_MODEL:
            assert not sender.send(lead, channel, message, chased=True)["replied"]


def test_every_bakery_on_the_list_gets_its_own_observation():
    writer = BakeryTemplateCopyProvider()
    rye_and_rosemary = enriched_style_lead(
        18, "Rye & Rosemary Bakery", email="sofia@rye-and-rosemary.example")
    body = writer.write_for_channel(rye_and_rosemary, OFFER, "instagram_dm")["body"]
    assert "the Order Ahead button on your site leads to a phone number" in body
    assert body.startswith("Hi Sofia, ")

    stranger = {"id": "a-bakery-from-your-own-search", "name": "Somewhere New",
                "owner_name": "", "review_count": 40}
    body = writer.write(stranger, OFFER)["body"]
    assert "customers still order by phone or in person" in body
    assert body.startswith("Hi there,")


def test_a_cleared_rye_and_rosemary_goes_out_on_email_and_is_delivered():
    rye_and_rosemary = enriched_style_lead(
        18, "Rye & Rosemary Bakery", email="sofia@rye-and-rosemary.example")
    result = BakeryFixtureSendProvider().send(rye_and_rosemary, "email",
                                              {"subject": "", "body": ""})
    assert result["delivered"] and not result["replied"] and not result["bounced"]


def test_a_head_office_is_greeted_as_there_not_by_a_desk_name():
    from pipeline.schema import outreach_placeholder_values
    values = outreach_placeholder_values(
        {"name": "Daily Dozen Donuts & Bakery", "owner_name": "Franchise Support Desk",
         "owner_role": "Corporate Office", "review_count": 1850}, "unused")
    assert values["owner_first_name"] == "there"
    assert values["review_count"] == "1,850"

