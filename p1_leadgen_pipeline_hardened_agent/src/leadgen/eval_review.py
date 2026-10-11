"""Eval capture and human review.

The pipeline calls `record_eval` every time it decides something automatically: a sample grader's
verdict on a scrape cell, the judge's decision on one lead, a contradiction found at CRM write.
A person reads those decisions on the review page (`leadgen evals review`), labels each one correct
or incorrect with a note, and `export_fixtures` turns the labeled records into JSON files that
tests replay, so a reviewed mistake becomes a regression test instead of a memory.
"""

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

EvalKind = Literal["sample_verdict", "judge_decision", "contradiction"]
EvalLabel = Literal["correct", "incorrect"]

EVAL_KINDS: tuple[EvalKind, ...] = ("sample_verdict", "judge_decision", "contradiction")
EVAL_LABELS: tuple[EvalLabel, ...] = ("correct", "incorrect")

# Where `leadgen evals export-fixtures` writes by default, relative to the working directory.
DEFAULT_FIXTURES_DIRECTORY = Path("tests/fixtures/evals")


@dataclass(frozen=True)
class EvalRecord:
    """One automated decision, with its human label when there is one."""

    eval_record_id: int
    kind: EvalKind
    subject: str
    verdict: str
    reason: str
    evidence: dict[str, Any]
    model: str | None
    run_id: UUID | None
    created_at: datetime
    label: EvalLabel | None = None
    note: str = ""
    labeled_by: str | None = None
    labeled_at: datetime | None = None

    @property
    def is_labeled(self) -> bool:
        return self.label is not None


def record_eval(
    connection: psycopg.Connection,
    *,
    kind: EvalKind,
    subject: str,
    verdict: str,
    reason: str = "",
    evidence: dict[str, Any] | None = None,
    model: str | None = None,
    run_id: UUID | None = None,
) -> int:
    """Save one automated decision. Returns its id.

    The sourcing grader, the qualification judge and the CRM-write contradiction scan all call
    this; `evidence` should hold whatever a test needs to replay the decision.
    """
    if kind not in EVAL_KINDS:
        raise ValueError(f"kind must be one of {EVAL_KINDS}, not {kind!r}")
    row = connection.execute(
        """
        INSERT INTO eval_records (kind, subject, verdict, reason, evidence, model, run_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING eval_record_id
        """,
        (kind, subject, verdict, reason, Jsonb(evidence or {}), model, run_id),
    ).fetchone()
    assert row is not None
    return row[0]


def read_eval_records(
    connection: psycopg.Connection,
    *,
    kind: EvalKind | None = None,
    unlabeled_only: bool = False,
    limit: int = 500,
) -> list[EvalRecord]:
    """Newest first, each with its label joined in. Filters narrow the weekly review."""
    conditions = ["TRUE"]
    parameters: list[Any] = []
    if kind is not None:
        conditions.append("records.kind = %s")
        parameters.append(kind)
    if unlabeled_only:
        conditions.append("labels.label IS NULL")
    parameters.append(limit)
    with connection.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(
            f"""
            SELECT records.eval_record_id, records.kind, records.subject, records.verdict,
                   records.reason, records.evidence, records.model, records.run_id,
                   records.created_at,
                   labels.label, labels.note, labels.labeled_by, labels.labeled_at
            FROM eval_records AS records
            LEFT JOIN eval_labels AS labels USING (eval_record_id)
            WHERE {" AND ".join(conditions)}
            ORDER BY records.created_at DESC, records.eval_record_id DESC
            LIMIT %s
            """,
            parameters,
        ).fetchall()
    return [EvalRecord(**{**row, "note": row["note"] or ""}) for row in rows]


def label_eval_record(
    connection: psycopg.Connection,
    eval_record_id: int,
    *,
    label: EvalLabel,
    note: str = "",
    labeled_by: str,
) -> None:
    """Set or replace the human label on one record."""
    if label not in EVAL_LABELS:
        raise ValueError(f"label must be one of {EVAL_LABELS}, not {label!r}")
    if not labeled_by.strip():
        raise ValueError("labeled_by must name the reviewer")
    # A label for a record that does not exist fails on the foreign key, deliberately.
    connection.execute(
        """
        INSERT INTO eval_labels (eval_record_id, label, note, labeled_by)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (eval_record_id) DO UPDATE
            SET label = EXCLUDED.label, note = EXCLUDED.note,
                labeled_by = EXCLUDED.labeled_by, labeled_at = now()
        """,
        (eval_record_id, label, note.strip(), labeled_by.strip()),
    )


def review_summary(records: list[EvalRecord]) -> dict[str, int]:
    """Counts the review page shows at the top: how much is left and how the pipeline is doing."""
    labeled = [record for record in records if record.is_labeled]
    return {
        "records": len(records),
        "unlabeled": len(records) - len(labeled),
        "correct": sum(record.label == "correct" for record in labeled),
        "incorrect": sum(record.label == "incorrect" for record in labeled),
    }


def fixture_payload(record: EvalRecord) -> dict[str, Any]:
    """One labeled record as it appears in a fixture file: the decision plus the expected label."""
    payload = asdict(record)
    payload["run_id"] = None if record.run_id is None else str(record.run_id)
    payload["created_at"] = record.created_at.isoformat()
    payload["labeled_at"] = None if record.labeled_at is None else record.labeled_at.isoformat()
    return payload


def export_fixtures(
    connection: psycopg.Connection, destination: Path = DEFAULT_FIXTURES_DIRECTORY
) -> dict[Path, int]:
    """Write every labeled record to `<destination>/<kind>.json`, one file per kind.

    Each file is a JSON list sorted by id, so re-exporting after new labels gives a readable diff.
    A kind with no labeled records gets no file. Returns the files written and their record counts.
    """
    destination.mkdir(parents=True, exist_ok=True)
    written: dict[Path, int] = {}
    for kind in EVAL_KINDS:
        labeled = [
            record
            for record in read_eval_records(connection, kind=kind, limit=1_000_000)
            if record.is_labeled
        ]
        if not labeled:
            continue
        labeled.sort(key=lambda record: record.eval_record_id)
        fixture_path = destination / f"{kind}.json"
        fixture_path.write_text(
            json.dumps([fixture_payload(record) for record in labeled], indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        written[fixture_path] = len(labeled)
    return written


def load_fixtures(fixture_path: Path) -> list[dict[str, Any]]:
    """Read one exported fixture file back, for tests that replay labeled decisions."""
    return json.loads(fixture_path.read_text(encoding="utf-8"))
