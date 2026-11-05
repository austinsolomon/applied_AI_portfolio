"""Eval capture, labeling and fixture export against a real database."""

import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest

from leadgen.eval_review import (
    EvalRecord,
    export_fixtures,
    failure_mode_counts,
    judge_alignment,
    label_eval_record,
    label_judge_decision,
    labeled_jsonl,
    load_fixtures,
    read_eval_records,
    record_eval,
    review_queue,
    review_summary,
)


def record_three_decisions(connection: psycopg.Connection) -> dict[str, int]:
    """One record of each kind, created in this order (so newest is the contradiction)."""
    sample_id = record_eval(
        connection,
        kind="sample_verdict",
        subject="Phoenix, AZ · certified financial planner",
        verdict="PROCEED",
        reason="purity 88%, in-market 95%",
        evidence={"purity_pct": 88, "in_market_pct": 95, "sampled": 40},
        run_id=uuid.uuid4(),
    )
    judge_id = record_eval(
        connection,
        kind="judge_decision",
        subject="Cedar Financial Planning",
        verdict="qualified",
        reason="names a CFP® planner serving Phoenix",
        evidence={"evidence_quote": "Jordan Example, CFP®, Phoenix", "serves_market": "yes"},
        model="claude-haiku-5-5",
    )
    contradiction_id = record_eval(
        connection,
        kind="contradiction",
        subject="Spa Supply Depot",
        verdict="contradiction",
        reason="reason affirms a disqualifier: 'sells equipment only'",
    )
    return {"sample": sample_id, "judge": judge_id, "contradiction": contradiction_id}


def test_records_are_read_newest_first_with_no_label(migrated_connection: psycopg.Connection):
    ids = record_three_decisions(migrated_connection)

    records = read_eval_records(migrated_connection)

    assert [record.eval_record_id for record in records] == [
        ids["contradiction"],
        ids["judge"],
        ids["sample"],
    ]
    assert all(not record.is_labeled for record in records)
    sample = records[-1]
    assert sample.evidence == {"purity_pct": 88, "in_market_pct": 95, "sampled": 40}
    assert sample.run_id is not None and sample.model is None
    assert records[1].model == "claude-haiku-5-5"


def test_filters_narrow_by_kind_and_unlabeled(migrated_connection: psycopg.Connection):
    ids = record_three_decisions(migrated_connection)
    label_eval_record(migrated_connection, ids["judge"], label="correct", labeled_by="austin")

    judge_only = read_eval_records(migrated_connection, kind="judge_decision")
    unlabeled = read_eval_records(migrated_connection, unlabeled_only=True)

    assert [record.eval_record_id for record in judge_only] == [ids["judge"]]
    assert judge_only[0].label == "correct" and judge_only[0].labeled_by == "austin"
    assert {record.eval_record_id for record in unlabeled} == {ids["sample"], ids["contradiction"]}


def test_relabeling_replaces_the_earlier_label(migrated_connection: psycopg.Connection):
    ids = record_three_decisions(migrated_connection)
    label_eval_record(migrated_connection, ids["sample"], label="correct", labeled_by="austin")

    label_eval_record(
        migrated_connection,
        ids["sample"],
        label="incorrect",
        note="  bleed was 40%, grader missed it  ",
        labeled_by="austin",
    )

    (sample,) = read_eval_records(migrated_connection, kind="sample_verdict")
    assert sample.label == "incorrect"
    assert sample.note == "bleed was 40%, grader missed it"
    label_count = migrated_connection.execute("SELECT count(*) FROM eval_labels").fetchone()
    assert label_count == (1,)
    assert review_summary(read_eval_records(migrated_connection)) == {
        "records": 3,
        "unlabeled": 2,
        "correct": 0,
        "incorrect": 1,
    }


def test_invalid_input_is_refused(migrated_connection: psycopg.Connection):
    ids = record_three_decisions(migrated_connection)

    with pytest.raises(ValueError, match="kind must be one of"):
        record_eval(migrated_connection, kind="vibe", subject="x", verdict="y")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="label must be one of"):
        label_eval_record(migrated_connection, ids["judge"], label="meh", labeled_by="a")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="labeled_by"):
        label_eval_record(migrated_connection, ids["judge"], label="correct", labeled_by="  ")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        label_eval_record(migrated_connection, 999_999, label="correct", labeled_by="austin")


def test_export_writes_one_fixture_file_per_kind_with_labels(
    migrated_connection: psycopg.Connection, tmp_path: Path
):
    ids = record_three_decisions(migrated_connection)
    label_eval_record(migrated_connection, ids["judge"], label="correct", labeled_by="austin")
    label_eval_record(
        migrated_connection,
        ids["contradiction"],
        label="incorrect",
        note="'equipment only' is negated in the page",
        labeled_by="austin",
    )
    destination = tmp_path / "fixtures" / "evals"

    written = export_fixtures(migrated_connection, destination)

    # The unlabeled sample verdict is not exported; the two labeled kinds are.
    assert written == {
        destination / "judge_decision.json": 1,
        destination / "contradiction.json": 1,
    }
    judge_fixture = load_fixtures(destination / "judge_decision.json")
    assert judge_fixture == [
        {
            "eval_record_id": ids["judge"],
            "kind": "judge_decision",
            "subject": "Cedar Financial Planning",
            "verdict": "qualified",
            "reason": "names a CFP® planner serving Phoenix",
            "evidence": {"evidence_quote": "Jordan Example, CFP®, Phoenix", "serves_market": "yes"},
            "model": "claude-haiku-5-5",
            "run_id": None,
            "created_at": judge_fixture[0]["created_at"],
            "label": "correct",
            "note": "",
            "labeled_by": "austin",
            # A label saved without the new fields reads back with their empty defaults.
            "correct_verdict": None,
            "failure_modes": [],
            "critique": "",
            "human_verdict": "qualified",
            "labeled_at": judge_fixture[0]["labeled_at"],
        }
    ]
    contradiction_fixture = load_fixtures(destination / "contradiction.json")
    assert contradiction_fixture[0]["label"] == "incorrect"
    assert contradiction_fixture[0]["note"] == "'equipment only' is negated in the page"
    # Stable formatting, so a re-export after new labels is a readable diff.
    raw = (destination / "judge_decision.json").read_text()
    assert raw == json.dumps(judge_fixture, indent=2, sort_keys=True) + "\n"


def test_export_with_nothing_labeled_writes_no_files(
    migrated_connection: psycopg.Connection, tmp_path: Path
):
    record_three_decisions(migrated_connection)

    assert export_fixtures(migrated_connection, tmp_path / "evals") == {}
    assert not any((tmp_path / "evals").iterdir())


def judge_decision(connection: psycopg.Connection, subject: str, verdict: str) -> EvalRecord:
    record_id = record_eval(
        connection, kind="judge_decision", subject=subject, verdict=verdict, reason="r"
    )
    (record,) = [
        record for record in read_eval_records(connection) if record.eval_record_id == record_id
    ]
    return record


def test_migration_adds_the_error_analysis_columns_with_safe_defaults(
    migrated_connection: psycopg.Connection,
):
    ids = record_three_decisions(migrated_connection)
    # A label written the old way, naming only the original columns.
    migrated_connection.execute(
        "INSERT INTO eval_labels (eval_record_id, label, note, labeled_by) "
        "VALUES (%s, 'incorrect', 'old note', 'austin')",
        (ids["judge"],),
    )

    (judge,) = read_eval_records(migrated_connection, kind="judge_decision")

    assert (judge.correct_verdict, judge.failure_modes, judge.critique) == (None, (), "")
    assert judge.human_verdict is None and judge.is_disagreement
    with pytest.raises(psycopg.errors.CheckViolation):
        migrated_connection.execute(
            "UPDATE eval_labels SET correct_verdict = 'maybe' WHERE eval_record_id = %s",
            (ids["judge"],),
        )


def test_a_pass_records_the_judges_verdict_as_correct(migrated_connection: psycopg.Connection):
    record = judge_decision(migrated_connection, "Cedar Financial Planning", "qualified")

    label_judge_decision(migrated_connection, record, passed=True, labeled_by="austin")

    (saved,) = read_eval_records(migrated_connection)
    assert saved.label == "correct" and saved.correct_verdict == "qualified"
    assert saved.human_verdict == "qualified" and not saved.is_disagreement


def test_a_fail_needs_a_critique_and_the_verdict_it_should_have_been(
    migrated_connection: psycopg.Connection,
):
    record = judge_decision(migrated_connection, "Driscoll Financial Advisors", "qualified")

    with pytest.raises(ValueError, match="critique"):
        label_judge_decision(
            migrated_connection, record, passed=False, labeled_by="a", correct_verdict="rejected"
        )
    with pytest.raises(ValueError, match="should have been"):
        label_judge_decision(
            migrated_connection, record, passed=False, labeled_by="a", critique="Edward Jones"
        )
    assert read_eval_records(migrated_connection)[0].label is None

    label_judge_decision(
        migrated_connection,
        record,
        passed=False,
        labeled_by="austin",
        critique="  The page says Edward Jones.  ",
        correct_verdict="rejected",
        failure_modes=["national-firm branch advisor missed", " wrong market", "wrong market"],
    )

    (saved,) = read_eval_records(migrated_connection)
    assert saved.label == "incorrect" and saved.human_verdict == "rejected"
    assert saved.critique == "The page says Edward Jones."
    assert saved.failure_modes == ("national-firm branch advisor missed", "wrong market")


def test_only_judge_decisions_take_a_judge_label(migrated_connection: psycopg.Connection):
    record_three_decisions(migrated_connection)
    sample = read_eval_records(migrated_connection, kind="sample_verdict")[0]

    with pytest.raises(ValueError, match="not a judge decision"):
        label_judge_decision(migrated_connection, sample, passed=True, labeled_by="austin")


def queue_record(record_id: int, verdict: str, label=None, minutes: int = 0) -> EvalRecord:
    return EvalRecord(
        eval_record_id=record_id,
        kind="judge_decision",
        subject=f"Firm {record_id}",
        verdict=verdict,
        reason="",
        evidence={},
        model=None,
        run_id=None,
        created_at=datetime(2026, 11, 1, tzinfo=UTC) + timedelta(minutes=minutes),
        label=label,
    )


def test_queue_puts_unlabeled_reviews_first_then_unlabeled_then_disagreements():
    records = [
        queue_record(1, "qualified", "correct", minutes=1),
        queue_record(2, "review", "incorrect", minutes=2),
        queue_record(3, "rejected", minutes=3),
        queue_record(4, "review", minutes=4),
        queue_record(5, "review", minutes=5),
        queue_record(6, "qualified", minutes=6),
    ]

    order = [record.eval_record_id for record in review_queue(records)]

    # Newest first inside each group.
    assert order == [5, 4, 6, 3, 2, 1]
    assert review_queue([]) == []


def aligned(verdict: str, human: str | None, label: str = "incorrect") -> EvalRecord:
    return replace(queue_record(0, verdict, label=label), correct_verdict=human)


def test_alignment_builds_the_confusion_matrix_and_per_verdict_scores():
    records = [
        aligned("qualified", "qualified", "correct"),
        aligned("qualified", "rejected"),
        aligned("rejected", "rejected", "correct"),
        aligned("rejected", "rejected", "correct"),
        aligned("review", "qualified"),
        # An old fail without a correct verdict, and an unlabeled record: both left out.
        aligned("rejected", None),
        queue_record(9, "qualified"),
    ]

    alignment = judge_alignment(records)

    assert alignment.confusion[("qualified", "rejected")] == 1
    assert alignment.confusion[("review", "qualified")] == 1
    assert alignment.confusion[("rejected", "rejected")] == 2
    assert sum(alignment.confusion.values()) == alignment.compared == 5
    assert alignment.agreed == 3 and alignment.agreement == pytest.approx(0.6)
    scores = {score.verdict: score for score in alignment.scores}
    assert (scores["qualified"].precision, scores["qualified"].recall) == (0.5, 0.5)
    assert scores["rejected"].precision == 1.0
    assert scores["rejected"].recall == pytest.approx(2 / 3)
    # The judge said review once and the reviewer never did: precision 0, recall undefined.
    assert (scores["review"].precision, scores["review"].recall) == (0.0, None)


def test_alignment_with_nothing_labeled_has_no_scores():
    alignment = judge_alignment([queue_record(1, "qualified")])

    assert alignment.compared == 0 and alignment.agreement is None
    assert all(value == 0 for value in alignment.confusion.values())
    assert all(score.precision is None and score.recall is None for score in alignment.scores)


def test_failure_modes_are_counted_across_labels():
    records = [
        replace(queue_record(1, "qualified", "incorrect"), failure_modes=("wrong market",)),
        replace(
            queue_record(2, "qualified", "incorrect"),
            failure_modes=("wrong market", "no CFP® on the page"),
        ),
    ]

    assert failure_mode_counts(records) == {"wrong market": 2, "no CFP® on the page": 1}


def test_exports_carry_the_human_verdict_failure_modes_and_critique(
    migrated_connection: psycopg.Connection, tmp_path: Path
):
    record = judge_decision(migrated_connection, "Driscoll Financial Advisors", "qualified")
    judge_decision(migrated_connection, "Unlabeled Firm", "review")
    label_judge_decision(
        migrated_connection,
        record,
        passed=False,
        labeled_by="austin",
        critique="Edward Jones advisor",
        correct_verdict="rejected",
        failure_modes=["national-firm branch advisor missed"],
    )

    export_fixtures(migrated_connection, tmp_path)
    (fixture,) = load_fixtures(tmp_path / "judge_decision.json")
    lines = labeled_jsonl(read_eval_records(migrated_connection)).splitlines()

    assert fixture["human_verdict"] == "rejected"
    assert fixture["correct_verdict"] == "rejected"
    assert fixture["failure_modes"] == ["national-firm branch advisor missed"]
    assert fixture["critique"] == "Edward Jones advisor"
    assert [json.loads(line) for line in lines] == [fixture]
