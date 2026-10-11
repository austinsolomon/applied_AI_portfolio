"""Eval capture, labeling and fixture export against a real database."""

import json
import uuid
from pathlib import Path

import psycopg
import pytest

from leadgen.eval_review import (
    export_fixtures,
    label_eval_record,
    load_fixtures,
    read_eval_records,
    record_eval,
    review_summary,
)


def record_three_decisions(connection: psycopg.Connection) -> dict[str, int]:
    """One record of each kind, created in this order (so newest is the contradiction)."""
    sample_id = record_eval(
        connection,
        kind="sample_verdict",
        subject="Phoenix, AZ · massage therapist",
        verdict="PROCEED",
        reason="purity 88%, in-market 95%",
        evidence={"purity_pct": 88, "in_market_pct": 95, "sampled": 40},
        run_id=uuid.uuid4(),
    )
    judge_id = record_eval(
        connection,
        kind="judge_decision",
        subject="Cedar Massage",
        verdict="qualified",
        reason="offers deep tissue massage in Phoenix",
        evidence={"evidence_quote": "Deep tissue massage, Phoenix", "serves_market": "yes"},
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
            "subject": "Cedar Massage",
            "verdict": "qualified",
            "reason": "offers deep tissue massage in Phoenix",
            "evidence": {"evidence_quote": "Deep tissue massage, Phoenix", "serves_market": "yes"},
            "model": "claude-haiku-5-5",
            "run_id": None,
            "created_at": judge_fixture[0]["created_at"],
            "label": "correct",
            "note": "",
            "labeled_by": "austin",
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
