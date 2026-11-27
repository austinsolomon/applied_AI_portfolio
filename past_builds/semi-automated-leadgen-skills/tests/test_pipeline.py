"""End-to-end tests — the handoff contract and the funnel arithmetic.

These are the tests that would catch a schema drift between this repo and the
game. If one fails, either the game changed or somebody 'tidied' a field name.

THE NUMBERS PINNED HERE ARE THE ONES THE COURSE PRINTS. Twenty-five Google Maps
rows go in. Every later number comes out of the four stages run for real, with
no keys, on the fixtures in this repo.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pipeline import run_pipeline
from pipeline.providers.bakery_outreach_fixture import OFFER
from pipeline.schema import (ENRICHED_KEY, LEADS_KEY, OUTREACH_KEY, QUALIFIED_KEY,
                             dedupe_by_id, make_lead)

REPO_ROOT = Path(__file__).resolve().parent.parent

FUNNEL_LINE_THE_COURSE_PRINTS = (
    "PIPELINE  HAULED 25 → LEADS 23 → ENRICHED 23 (COMPLETE 21) → "
    "QUALIFIED 9 (REJECTED 12, NEEDS REVIEW 2) → SENT 8 → DELIVERED 5 (UNDELIVERED 3) → REPLIES 2 → MEETINGS 1 (Opportunities to pitch; no sale recorded)")
COST_LINE_THE_COURSE_PRINTS = "enrichment cost $2.2460 ($0.0977/lead)"

SUNRISE_BAKERY_LEAD_ID = "apify-maps-72a168d9e318"


@pytest.fixture
def full_run(tmp_path, monkeypatch, capsys):
    """Run all four stages from the repo folder and hand back every file written."""
    monkeypatch.chdir(REPO_ROOT)
    exit_code = run_pipeline.main(["--outdir", str(tmp_path)])
    printed = capsys.readouterr().out
    handoffs = {file_name: json.loads((tmp_path / file_name).read_text())
                for file_name in ("leads.json", "enriched.json", "qualified.json",
                                  "outreach.json", "funnel.json")}
    return {"exit_code": exit_code, "printed": printed, "handoffs": handoffs}


def test_full_run_writes_every_handoff_file(full_run):
    assert full_run["exit_code"] == 0
    assert set(full_run["handoffs"]) == {"leads.json", "enriched.json", "qualified.json",
                                         "outreach.json", "funnel.json"}


def test_handoff_keys_match_the_games_localstorage(full_run):
    handoffs = full_run["handoffs"]
    assert handoffs["leads.json"]["key"] == LEADS_KEY == "claude3d.leads.v1"
    assert handoffs["enriched.json"]["key"] == ENRICHED_KEY == "claude3d.enriched.v1"
    assert handoffs["qualified.json"]["key"] == QUALIFIED_KEY == "claude3d.qualified.v1"
    assert handoffs["outreach.json"]["key"] == OUTREACH_KEY == "claude3d.outreach.v1"
    assert handoffs["funnel.json"]["key"] == "claude3d.funnel.v1"


def test_funnel_stage_keys_match_funnel_ledger_js():
    assert run_pipeline.STAGE_KEYS == [
        "hauled", "bottles", "leads", "enriched", "complete",
        "qualified", "chased", "missed", "sent", "inboxed", "spammed"]


def test_the_full_run_prints_the_funnel_line_the_course_shows(full_run):
    printed_lines = full_run["printed"].splitlines()
    assert FUNNEL_LINE_THE_COURSE_PRINTS in printed_lines
    assert COST_LINE_THE_COURSE_PRINTS in printed_lines
    assert "mode: PRACTICE (saved data — nothing was sent)" in printed_lines


def test_the_readme_shows_exactly_what_the_run_prints(tmp_path):
    """The README promises the run's output character for character. Hold it to that.

    The command is run the way a buyer runs it: a fresh Python process, no keys,
    progress lines and summary lines read together in the order they appear.
    """
    readme = (REPO_ROOT / "README.md").read_text()
    transcript_start = readme.index("provider apify ")
    transcript_in_readme = readme[transcript_start:readme.index("```", transcript_start)]

    environment = {name: value for name, value in os.environ.items()
                   if not name.endswith(("_KEY", "_TOKEN"))}
    environment["PYTHONPATH"] = str(REPO_ROOT)
    environment["PYTHONIOENCODING"] = "utf-8"
    finished_run = subprocess.run(
        [sys.executable, "-m", "pipeline.run_pipeline"], cwd=tmp_path,
        env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", check=True)

    assert finished_run.stdout == transcript_in_readme
    assert FUNNEL_LINE_THE_COURSE_PRINTS in transcript_in_readme.splitlines()
    assert COST_LINE_THE_COURSE_PRINTS in transcript_in_readme.splitlines()


def test_the_funnel_file_holds_the_same_numbers_as_the_printed_line(full_run):
    funnel = full_run["handoffs"]["funnel.json"]
    assert funnel["stages"] == {
        "hauled": 25, "bottles": 4, "leads": 23, "enriched": 23, "complete": 21,
        "qualified": 9, "chased": 4, "missed": 1,
        "sent": 8, "inboxed": 5, "spammed": 3}
    assert funnel["qualification"] == {"qualified": 9, "rejected": 12, "needs_review": 2}
    assert funnel["replies"] == 2
    assert funnel["cost"]["total"] == 2.246
    assert funnel["live"] is False
    assert run_pipeline.render(funnel) == FUNNEL_LINE_THE_COURSE_PRINTS


def test_the_default_offer_is_the_bakery_offer(full_run):
    assert full_run["handoffs"]["outreach.json"]["offer"] == OFFER
    assert OFFER == "I build simple websites that let Chicago bakeries take orders online."


def test_one_bakery_can_be_followed_through_all_four_handoffs(full_run):
    """Sunrise Bakery: sourced, enriched for $0.107, qualified, emailed, replied."""
    handoffs = full_run["handoffs"]
    by_id = lambda records: {record["id"]: record for record in records}

    raw = by_id(handoffs["leads.json"]["records"])[SUNRISE_BAKERY_LEAD_ID]
    assert raw["name"] == "Sunrise Bakery" and raw["review_count"] == 212
    assert "owner_name" not in raw, "a raw lead is a listing, with no person on it"

    enriched = by_id(handoffs["enriched.json"]["records"])[SUNRISE_BAKERY_LEAD_ID]
    assert enriched["owner_name"] == "Rosa Delgado"
    assert enriched["email"] == "rosa@sunrise.example"
    assert enriched["enrichment"]["cost"] == 0.107
    assert len(enriched["enrichment"]["ledger"]) == 6
    assert enriched["enrichment"]["research"]["status"] == "confirmed"

    entry = by_id(handoffs["qualified.json"]["records"])[SUNRISE_BAKERY_LEAD_ID]
    assert entry["qualified"] is True and entry["reasons"] == []
    assert entry["contact"] == "rosa@sunrise.example"

    outreach = handoffs["outreach.json"]
    [send_row] = [row for row in outreach["records"]
                  if row["lead_id"] == SUNRISE_BAKERY_LEAD_ID]
    assert send_row["channel"] == "email"
    assert send_row["subject"] == "Online orders for Sunrise Bakery"
    assert send_row["delivered"] and send_row["replied"]
    [sent_message] = [row["message"] for row in outreach["sent_messages"]
                      if row["lead_id"] == SUNRISE_BAKERY_LEAD_ID]
    assert sent_message["body"].startswith("Hi Rosa,")
    assert sent_message["body"].endswith("Jordan Ellis")


def test_running_from_another_folder_still_finds_the_fixture(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert run_pipeline.main(["--outdir", str(tmp_path / "out")]) == 0
    assert FUNNEL_LINE_THE_COURSE_PRINTS in capsys.readouterr().out.splitlines()


def test_a_smaller_limit_sources_fewer_bakeries(tmp_path, monkeypatch):
    monkeypatch.chdir(REPO_ROOT)
    run_pipeline.main(["--outdir", str(tmp_path), "--limit", "5"])
    funnel = json.loads((tmp_path / "funnel.json").read_text())
    assert funnel["stages"]["leads"] == 5


def test_no_chase_mode_sends_only_the_qualified(tmp_path, monkeypatch):
    monkeypatch.chdir(REPO_ROOT)
    run_pipeline.main(["--outdir", str(tmp_path), "--no-chase"])
    funnel = json.loads((tmp_path / "funnel.json").read_text())
    assert funnel["stages"]["qualified"] == 9 and funnel["stages"]["chased"] == 0
    # Night Owl's draft is still held by the lint, so 8 of the 9 go out.
    assert funnel["stages"]["sent"] == 8


def test_funnel_numbers_only_shrink_down_the_pipeline(enriched, qualified):
    from pipeline.outreach import run_outreach
    sourced = {"counts": {"dataset_items": 25, "leads": 23}, "live": False}
    outreach = run_outreach(qualified["records"])
    stages = run_pipeline.build_funnel(sourced, enriched, qualified, outreach)["stages"]
    assert stages["hauled"] >= stages["leads"] >= stages["enriched"] >= stages["complete"]
    assert stages["qualified"] + stages["chased"] <= stages["enriched"]
    assert stages["sent"] <= stages["qualified"]
    assert stages["sent"] >= stages["inboxed"]
    assert stages["inboxed"] + stages["spammed"] == stages["sent"]


def test_run_is_only_called_live_when_every_stage_is_live(enriched, qualified):
    from pipeline.outreach import run_outreach
    outreach = run_outreach(qualified["records"])
    sourced = {"counts": {"dataset_items": 1, "leads": 1}, "live": True}
    funnel = run_pipeline.build_funnel(sourced, enriched, qualified, outreach)
    assert funnel["live"] is False, "a run with one mocked stage is a mocked run"


def test_dedupe_is_last_write_wins():
    first_write = make_lead("same-id", "Sunrise Bakery", review_count=200)
    second_write = make_lead("same-id", "Sunrise Bakery", review_count=212)
    merged = dedupe_by_id([first_write, second_write])
    assert len(merged) == 1 and merged[0]["review_count"] == 212


def test_rerunning_a_stage_is_idempotent_not_additive(raw_leads):
    """Replaying a stage must not double the funnel — the game merges by id."""
    from pipeline.enrich import enrich_leads
    once = enrich_leads(raw_leads)
    twice = enrich_leads(raw_leads + raw_leads)
    assert twice["counts"]["enriched"] == once["counts"]["enriched"]
    assert twice["cost"]["total"] == once["cost"]["total"], "a replay is never billed twice"


def test_no_network_is_attempted_without_keys(monkeypatch, apify_bakeries):
    """The hard guarantee this repo sells: pytest touches nothing external."""
    import socket

    def refuse_to_connect(*args, **kwargs):
        raise AssertionError("the mocked pipeline opened a socket")

    monkeypatch.setattr(socket.socket, "connect", refuse_to_connect)
    from pipeline.enrich import enrich_leads
    from pipeline.outreach import run_outreach
    from pipeline.qualify import qualify_leads
    from pipeline.source_bakeries import source_bakeries

    sourced = source_bakeries(fixture=apify_bakeries, limit=25)
    enriched = enrich_leads(sourced["records"])
    qualified = qualify_leads(enriched["records"])
    outreach = run_outreach(qualified["records"])
    assert outreach["counts"]["sent"] == 8
