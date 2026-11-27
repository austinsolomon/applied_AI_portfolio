"""Eval capture and human review.

The pipeline calls `record_eval` every time it decides something automatically: a sample grader's
verdict on a scrape cell, the judge's decision on one lead, a contradiction found at CRM write.
A person reads those decisions on the review page (`leadgen evals review`) one at a time and gives
each a binary pass/fail label; a failed judge decision also gets a one-line critique, the verdict
it should have been and failure-mode tags. That is the error-analysis loop of Hamel Husain's "Your
AI Product Needs Evals": `judge_alignment` measures the judge against the labels class by class,
and `export_fixtures` turns the labeled records into JSON files that tests replay, so a reviewed
mistake becomes a regression test instead of a memory.
"""

import json
from collections import Counter
from collections.abc import Iterable, Sequence
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
JudgeVerdict = Literal["qualified", "rejected", "review"]

EVAL_KINDS: tuple[EvalKind, ...] = ("sample_verdict", "judge_decision", "contradiction")
# Binary on purpose: "correct" is a pass (the decision was right), "incorrect" a fail.
EVAL_LABELS: tuple[EvalLabel, ...] = ("correct", "incorrect")
# The qualification judge's verdicts, and what a reviewer says a failed one should have been.
JUDGE_VERDICTS: tuple[JudgeVerdict, ...] = ("qualified", "rejected", "review")

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
    # The verdict the reviewer says is right; None on labels saved before migration 0021.
    correct_verdict: str | None = None
    failure_modes: tuple[str, ...] = ()
    critique: str = ""

    @property
    def is_labeled(self) -> bool:
        return self.label is not None

    @property
    def human_verdict(self) -> str | None:
        """The verdict the reviewer stands behind: a pass agrees with the recorded verdict, a fail
        names its own. None when unlabeled, or failed before the correct verdict was recorded."""
        if self.correct_verdict is not None:
            return self.correct_verdict
        return self.verdict if self.label == "correct" else None

    @property
    def is_disagreement(self) -> bool:
        """A person looked and said the automated decision was wrong."""
        return self.label == "incorrect"


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
    with_page_text: bool = True,
) -> list[EvalRecord]:
    """Newest first, each with its label joined in. Filters narrow the weekly review.

    `evidence.sources` holds the full text of every page the judge read, almost all of a
    record's size. The review page asks without it and loads one decision's pages at a time
    (read_eval_sources): reading every record's pages on each redraw sent tens of megabytes per
    click out of the database, which Neon's free plan meters.
    """
    conditions = ["TRUE"]
    parameters: list[Any] = []
    if kind is not None:
        conditions.append("records.kind = %s")
        parameters.append(kind)
    if unlabeled_only:
        conditions.append("labels.label IS NULL")
    parameters.append(limit)
    evidence_column = "records.evidence" if with_page_text else "records.evidence - 'sources'"
    with connection.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(
            f"""
            SELECT records.eval_record_id, records.kind, records.subject, records.verdict,
                   records.reason, {evidence_column} AS evidence, records.model,
                   records.run_id,
                   records.created_at,
                   labels.label, labels.note, labels.labeled_by, labels.labeled_at,
                   labels.correct_verdict, labels.failure_modes, labels.critique
            FROM eval_records AS records
            LEFT JOIN eval_labels AS labels USING (eval_record_id)
            WHERE {" AND ".join(conditions)}
            ORDER BY records.created_at DESC, records.eval_record_id DESC
            LIMIT %s
            """,
            parameters,
        ).fetchall()
    return [
        EvalRecord(
            **{
                **row,
                "note": row["note"] or "",
                "failure_modes": tuple(row["failure_modes"] or ()),
                "critique": row["critique"] or "",
            }
        )
        for row in rows
    ]


def read_eval_sources(connection: psycopg.Connection, eval_record_id: int) -> dict[str, str]:
    """One record's evidence pages (URL -> page text), for the decision being reviewed."""
    row = connection.execute(
        "SELECT evidence -> 'sources' FROM eval_records WHERE eval_record_id = %s",
        (eval_record_id,),
    ).fetchone()
    return row[0] if row and isinstance(row[0], dict) else {}


def read_decision_markets(connection: psycopg.Connection) -> dict[tuple[UUID, str], str]:
    """(run, business name) -> "City, ST" for every raw lead, so judge decisions recorded before
    the market was kept in their evidence can still show it."""
    rows = connection.execute(
        """
        SELECT DISTINCT leads.run_id, leads.business_name, markets.name || ', ' || markets.state
        FROM raw_leads AS leads JOIN markets USING (market_id)
        """
    ).fetchall()
    return {(run_id, business_name): market for run_id, business_name, market in rows}


def label_eval_record(
    connection: psycopg.Connection,
    eval_record_id: int,
    *,
    label: EvalLabel,
    note: str = "",
    labeled_by: str,
    correct_verdict: JudgeVerdict | None = None,
    failure_modes: Iterable[str] = (),
    critique: str = "",
) -> None:
    """Set or replace the human label on one record, with its error analysis when there is one."""
    if label not in EVAL_LABELS:
        raise ValueError(f"label must be one of {EVAL_LABELS}, not {label!r}")
    if not labeled_by.strip():
        raise ValueError("labeled_by must name the reviewer")
    if correct_verdict is not None and correct_verdict not in JUDGE_VERDICTS:
        raise ValueError(
            f"correct_verdict must be one of {JUDGE_VERDICTS}, not {correct_verdict!r}"
        )
    tags = list(dict.fromkeys(tag.strip() for tag in failure_modes if tag.strip()))
    # A label for a record that does not exist fails on the foreign key, deliberately.
    connection.execute(
        """
        INSERT INTO eval_labels
            (eval_record_id, label, note, labeled_by, correct_verdict, failure_modes, critique)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (eval_record_id) DO UPDATE
            SET label = EXCLUDED.label, note = EXCLUDED.note,
                labeled_by = EXCLUDED.labeled_by, labeled_at = now(),
                correct_verdict = EXCLUDED.correct_verdict,
                failure_modes = EXCLUDED.failure_modes, critique = EXCLUDED.critique
        """,
        (
            eval_record_id,
            label,
            note.strip(),
            labeled_by.strip(),
            correct_verdict,
            tags,
            critique.strip(),
        ),
    )


def label_judge_decision(
    connection: psycopg.Connection,
    record: EvalRecord,
    *,
    passed: bool,
    labeled_by: str,
    critique: str = "",
    correct_verdict: JudgeVerdict | None = None,
    failure_modes: Iterable[str] = (),
) -> None:
    """Save the answer to "Was the judge right?" for one judge decision.

    A pass means the recorded verdict was right, so it is also the correct verdict. A fail needs
    a one-line critique and the verdict it should have been: a bare "wrong" teaches nothing.
    """
    if record.kind != "judge_decision":
        raise ValueError(f"record {record.eval_record_id} is a {record.kind}, not a judge decision")
    if passed:
        label_eval_record(
            connection,
            record.eval_record_id,
            label="correct",
            labeled_by=labeled_by,
            correct_verdict=record.verdict if record.verdict in JUDGE_VERDICTS else None,  # type: ignore[arg-type]
            critique=critique,
        )
        return
    if not critique.strip():
        raise ValueError("a fail needs a one-line critique: what did the judge get wrong?")
    if correct_verdict is None:
        raise ValueError("a fail needs the verdict it should have been")
    label_eval_record(
        connection,
        record.eval_record_id,
        label="incorrect",
        labeled_by=labeled_by,
        correct_verdict=correct_verdict,
        failure_modes=failure_modes,
        critique=critique,
    )


def review_queue(records: Iterable[EvalRecord]) -> list[EvalRecord]:
    """The order a reviewer works through decisions: unlabeled `review` verdicts first (the judge
    itself was unsure), then the other unlabeled ones, then the disagreements already found, then
    the agreed ones. Newest first inside each group."""

    def group(record: EvalRecord) -> int:
        if not record.is_labeled:
            return 0 if record.verdict == "review" else 1
        return 2 if record.is_disagreement else 3

    newest_first = sorted(
        records, key=lambda record: (record.created_at, record.eval_record_id), reverse=True
    )
    return sorted(newest_first, key=group)


def review_summary(records: list[EvalRecord]) -> dict[str, int]:
    """Counts the review page shows at the top: how much is left and how the pipeline is doing."""
    labeled = [record for record in records if record.is_labeled]
    return {
        "records": len(records),
        "unlabeled": len(records) - len(labeled),
        "correct": sum(record.label == "correct" for record in labeled),
        "incorrect": sum(record.label == "incorrect" for record in labeled),
    }


@dataclass(frozen=True)
class VerdictScores:
    """Precision and recall for one verdict class; None where the class never occurred."""

    verdict: str
    judge_count: int  # how often the judge said it
    human_count: int  # how often the reviewer said it
    agreed: int  # both said it
    precision: float | None  # of the judge's calls for this class, the share the reviewer agreed
    recall: float | None  # of the reviewer's calls for this class, the share the judge made too


@dataclass(frozen=True)
class JudgeAlignment:
    """How well the judge agrees with human labels, over decisions whose human verdict is known."""

    # (judge verdict, human verdict) -> number of decisions, with every pair present.
    confusion: dict[tuple[str, str], int]
    compared: int
    agreed: int
    scores: tuple[VerdictScores, ...]

    @property
    def agreement(self) -> float | None:
        return self.agreed / self.compared if self.compared else None


def judge_alignment(
    records: Iterable[EvalRecord], verdicts: Sequence[str] = JUDGE_VERDICTS
) -> JudgeAlignment:
    """Confusion matrix, raw agreement, and precision and recall per verdict.

    Raw agreement alone flatters a judge that is right about the common verdict and wrong about the
    rare one, so every verdict is scored on its own.
    """
    pairs = Counter(
        (record.verdict, record.human_verdict)
        for record in records
        if record.verdict in verdicts and record.human_verdict in verdicts
    )
    confusion = {(judge, human): pairs[(judge, human)] for judge in verdicts for human in verdicts}
    scores = []
    for verdict in verdicts:
        judge_count = sum(confusion[(verdict, human)] for human in verdicts)
        human_count = sum(confusion[(judge, verdict)] for judge in verdicts)
        agreed = confusion[(verdict, verdict)]
        scores.append(
            VerdictScores(
                verdict=verdict,
                judge_count=judge_count,
                human_count=human_count,
                agreed=agreed,
                precision=agreed / judge_count if judge_count else None,
                recall=agreed / human_count if human_count else None,
            )
        )
    return JudgeAlignment(
        confusion=confusion,
        compared=sum(confusion.values()),
        agreed=sum(confusion[(verdict, verdict)] for verdict in verdicts),
        scores=tuple(scores),
    )


def failure_mode_counts(records: Iterable[EvalRecord]) -> Counter[str]:
    """How often each failure-mode tag was given, across labeled decisions."""
    return Counter(tag for record in records for tag in record.failure_modes)


def fixture_payload(record: EvalRecord) -> dict[str, Any]:
    """One labeled record as it appears in a fixture file: the decision plus the expected label."""
    payload = asdict(record)
    payload["failure_modes"] = list(record.failure_modes)
    # The verdict a replaying test expects: the recorded one on a pass, the reviewer's on a fail.
    payload["human_verdict"] = record.human_verdict
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


def labeled_jsonl(records: Iterable[EvalRecord]) -> str:
    """Labeled records as JSON Lines, oldest first, each in the fixture-file shape."""
    labeled = sorted(
        (record for record in records if record.is_labeled),
        key=lambda record: record.eval_record_id,
    )
    return "".join(
        json.dumps(fixture_payload(record), sort_keys=True, ensure_ascii=False) + "\n"
        for record in labeled
    )


def load_fixtures(fixture_path: Path) -> list[dict[str, Any]]:
    """Read one exported fixture file back, for tests that replay labeled decisions."""
    return json.loads(fixture_path.read_text(encoding="utf-8"))
