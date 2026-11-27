"""The eval review screen's pure parts: quote highlighting, check sentences, filters."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from leadgen.eval_review import EvalRecord
from leadgen.eval_review_view import (
    automatic_check_sentences,
    contradiction_sentence,
    highlight_quote,
    matches_filters,
    other_decision_sentence,
    shown_reason,
)
from leadgen.qualification import UNVERIFIED_REASON_PREFIX

MARK = '<mark style="'


def judge_record(**overrides) -> EvalRecord:
    fields = {
        "eval_record_id": 1,
        "kind": "judge_decision",
        "subject": "Cedar Financial Planning",
        "verdict": "qualified",
        "reason": "Independent firm with a CFP® planner",
        "evidence": {
            "sources": {"https://cedar.example/team": "Jordan Lee, CFP®, leads planning."},
            "judgment": {
                "verdict": "qualified",
                "evidence_quote": "Jordan Lee, CFP®",
                "source_url": "https://cedar.example/team",
                "eligible_services": ["financial planning"],
                "serves_market": "yes",
            },
            "source": "exa",
        },
        "model": "claude-haiku-4-5",
        "run_id": None,
        "created_at": datetime(2026, 11, 1, tzinfo=UTC),
    }
    fields.update(overrides)
    return EvalRecord(**fields)


def test_the_quote_is_highlighted_inside_the_escaped_page() -> None:
    page = "<b>Team</b>\nJordan Lee, CFP® & partner, leads planning."

    text_html, found = highlight_quote(page, "Jordan Lee, CFP® & partner")

    assert found
    assert text_html.startswith("&lt;b&gt;Team&lt;/b&gt;\n")
    assert ">Jordan Lee, CFP® &amp; partner</mark>, leads planning." in text_html
    assert "<b>" not in text_html


def test_whitespace_differences_still_match_like_the_citation_check() -> None:
    page = "Dana Whitfield is currently   pursuing\nthe CFP® certification."

    text_html, found = highlight_quote(page, "Dana Whitfield is currently pursuing the  CFP®")

    assert found
    assert ">Dana Whitfield is currently   pursuing\nthe CFP®</mark>" in text_html


def test_a_quote_that_is_not_on_the_page_highlights_nothing() -> None:
    text_html, found = highlight_quote("<i>About us</i>", "Elena Park, CFP®")

    assert not found
    assert text_html == "&lt;i&gt;About us&lt;/i&gt;"
    assert MARK not in text_html


@pytest.mark.parametrize("quote", ["", "   \n "])
def test_an_empty_quote_highlights_nothing(quote: str) -> None:
    assert highlight_quote("a < b", quote) == ("a &lt; b", False)


def test_a_quote_with_regex_characters_matches_literally() -> None:
    text_html, found = highlight_quote("Fees (flat) + 1.0% [AUM]", "(flat) + 1.0% [AUM]")

    assert found and ">(flat) + 1.0% [AUM]</mark>" in text_html


def test_citation_downgrade_reads_as_a_sentence_naming_the_failed_part() -> None:
    record = judge_record(
        verdict="review",
        reason=f"{UNVERIFIED_REASON_PREFIX} Names a CFP® planner.",
        evidence={
            **judge_record().evidence,
            "judgment": {**judge_record().evidence["judgment"], "evidence_quote": "Not there"},
            "proposed_verdict": "qualified",
        },
    )

    assert automatic_check_sentences(record, eligible_services=["financial planning"]) == [
        "Automatic check: the judge said qualified, but the quote could not be found on the "
        "cited page, so the verdict was lowered to review."
    ]
    assert shown_reason(record) == "Names a CFP® planner."


def test_citation_downgrade_for_an_unlisted_service() -> None:
    record = judge_record(verdict="review", reason=f"{UNVERIFIED_REASON_PREFIX} ok")

    (sentence,) = automatic_check_sentences(record, eligible_services=["tax planning"])

    assert "none of the services it named are on the category's list" in sentence


def test_contradiction_reads_as_a_sentence_about_the_judges_own_reason() -> None:
    contradiction = judge_record(
        kind="contradiction",
        verdict="contradiction",
        reason="reason affirms 'insurance agency': an insurance agency in Tempe",
        evidence={"lead_id": 4},
    )

    sentence = contradiction_sentence(contradiction)

    assert sentence.startswith(
        "Automatic check: the judge said qualified, but its own reason says 'insurance agency' "
        '("an insurance agency in Tempe"), which the category lists as a disqualifier.'
    )
    assert "still written to the CRM" in sentence
    assert automatic_check_sentences(judge_record(), [contradiction]) == [sentence]
    assert other_decision_sentence(contradiction).startswith("The judge said qualified")


def test_a_clean_decision_has_no_check_sentences() -> None:
    assert automatic_check_sentences(judge_record(), eligible_services=["financial planning"]) == []


def test_no_evidence_and_market_override_have_their_own_sentences() -> None:
    no_pages = judge_record(verdict="review", reason="No readable evidence", evidence={})
    out_of_market = judge_record(
        verdict="rejected", evidence={**judge_record().evidence, "proposed_verdict": "qualified"}
    )

    assert "no page about this business could be read" in automatic_check_sentences(no_pages)[0]
    assert "does not serve the market" in automatic_check_sentences(out_of_market)[0]


def test_sample_grades_read_as_a_sentence() -> None:
    grade = judge_record(
        kind="sample_verdict",
        verdict="PROCEED",
        evidence={"count": 20, "category_purity": 0.95, "in_market_share": 0.9},
    )

    assert other_decision_sentence(grade) == (
        "The Maps sample grader read 20 sampled places: 95% on-category, 90% inside the market, "
        "so the cell was graded PROCEED."
    )


def test_filters_narrow_by_verdict_source_and_label_state() -> None:
    labeled_fail = judge_record(label="incorrect", correct_verdict="rejected")
    unlabeled_maps = judge_record(
        eval_record_id=2,
        verdict="review",
        evidence={"source": "google_maps"},
        run_id=uuid.uuid4(),
        created_at=datetime(2026, 11, 1, tzinfo=UTC) + timedelta(minutes=1),
    )
    everything = {"verdicts": [], "source": "All", "show": "Everything, in queue order"}

    assert matches_filters(labeled_fail, **everything)
    assert not matches_filters(labeled_fail, **{**everything, "verdicts": ["review"]})
    assert matches_filters(unlabeled_maps, **{**everything, "source": "Google Maps"})
    assert not matches_filters(labeled_fail, **{**everything, "source": "Google Maps"})
    assert matches_filters(labeled_fail, **{**everything, "show": "Disagreements"})
    assert not matches_filters(unlabeled_maps, **{**everything, "show": "Labeled"})
    assert matches_filters(unlabeled_maps, **{**everything, "show": "Unlabeled"})
