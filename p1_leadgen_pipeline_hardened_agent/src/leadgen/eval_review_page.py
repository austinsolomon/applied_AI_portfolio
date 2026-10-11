"""Weekly eval review page: read what the pipeline decided, label it, leave a note.

Run with `leadgen evals review`. Reads `eval_records`, writes only `eval_labels`, through the same
functions the exporter uses, so what you label here is exactly what becomes a fixture.
"""

import pandas
import psycopg
import streamlit
from pydantic import ValidationError

from leadgen.database import connect
from leadgen.eval_review import (
    EVAL_KINDS,
    EVAL_LABELS,
    EvalRecord,
    label_eval_record,
    read_eval_records,
    review_summary,
)

ALL_KINDS = "all kinds"

# What was decided, by whom, and whether a person has agreed yet.
COLUMN_ORDER = ["Id", "Kind", "Subject", "Verdict", "Reason", "Model", "Label", "Note", "Recorded"]


def records_table(records: list[EvalRecord]) -> pandas.DataFrame:
    rows = [
        {
            "Id": record.eval_record_id,
            "Kind": record.kind,
            "Subject": record.subject,
            "Verdict": record.verdict,
            "Reason": record.reason,
            "Model": record.model or "—",
            "Label": record.label or "—",
            "Note": record.note,
            "Recorded": record.created_at.strftime("%Y-%m-%d %H:%M"),
        }
        for record in records
    ]
    return pandas.DataFrame(rows, columns=COLUMN_ORDER)


def record_choice(record: EvalRecord) -> str:
    marker = "" if record.is_labeled else "• "
    return f"{marker}#{record.eval_record_id} {record.kind}: {record.subject} → {record.verdict}"


def render() -> None:
    streamlit.set_page_config(page_title="Eval review", layout="wide")
    streamlit.title("Eval review")
    streamlit.caption(
        "Every automated decision the pipeline made, newest first. Mark each one correct or "
        "incorrect; labels become test fixtures with `leadgen evals export-fixtures`."
    )

    saved_message = streamlit.session_state.pop("saved_message", None)
    if saved_message:
        streamlit.success(saved_message)

    filter_columns = streamlit.columns(2)
    kind_choice = filter_columns[0].selectbox("Kind", [ALL_KINDS, *EVAL_KINDS], key="kind")
    unlabeled_only = filter_columns[1].checkbox("Unlabeled only", key="unlabeled_only")
    kind = None if kind_choice == ALL_KINDS else kind_choice

    try:
        with connect() as connection:
            records = read_eval_records(connection, kind=kind, unlabeled_only=unlabeled_only)
    except ValidationError:
        streamlit.error(
            "DATABASE_URL is missing or malformed. Copy .env.example to .env, or export "
            "DATABASE_URL before starting the review page."
        )
        return
    except psycopg.Error as error:
        streamlit.error(f"Cannot read eval records from the database: {error}")
        return

    summary = review_summary(records)
    summary_columns = streamlit.columns(4)
    summary_columns[0].metric("Records", summary["records"])
    summary_columns[1].metric("Unlabeled", summary["unlabeled"])
    summary_columns[2].metric("Correct", summary["correct"])
    summary_columns[3].metric("Incorrect", summary["incorrect"])

    if not records:
        streamlit.info("Nothing to review: no eval records match. Run the pipeline first.")
        return

    streamlit.dataframe(records_table(records), hide_index=True)

    streamlit.subheader("Label a record")
    chosen = streamlit.selectbox(
        "Record", records, format_func=record_choice, key="record", help="• = not yet labeled"
    )
    with streamlit.expander("Evidence behind this decision"):
        streamlit.json(chosen.evidence)
    label_columns = streamlit.columns([1, 2, 1])
    label = label_columns[0].radio(
        "Label",
        EVAL_LABELS,
        index=EVAL_LABELS.index(chosen.label) if chosen.is_labeled else 0,
        key=f"label_{chosen.eval_record_id}",
        horizontal=True,
    )
    note = label_columns[1].text_input(
        "Note", value=chosen.note, key=f"note_{chosen.eval_record_id}"
    )
    labeled_by = label_columns[2].text_input("Reviewer", value="reviewer", key="labeled_by")

    if streamlit.button("Save label", key="save"):
        try:
            with connect() as connection:
                label_eval_record(
                    connection,
                    chosen.eval_record_id,
                    label=label,
                    note=note,
                    labeled_by=labeled_by,
                )
        except (ValueError, psycopg.Error) as error:
            streamlit.error(f"Label not saved: {error}")
            return
        # Rerun so the table and the counts show the new label; the message survives the rerun.
        streamlit.session_state["saved_message"] = (
            f"Saved: #{chosen.eval_record_id} labeled {label}."
        )
        streamlit.rerun()


render()
