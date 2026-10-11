"""The eval review page, rendered headless with Streamlit's AppTest against a real database."""

from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from streamlit.testing.v1 import AppTest

from leadgen import eval_review_page as page_module
from leadgen.eval_review import label_eval_record, read_eval_records, record_eval
from leadgen.settings import get_settings

PAGE_PATH = Path(page_module.__file__)


@pytest.fixture
def review_database(
    migrated_connection: psycopg.Connection,
    empty_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[psycopg.Connection]:
    """Point the page at a fresh database holding two decisions, one of them already labeled."""
    sample_id = record_eval(
        migrated_connection,
        kind="sample_verdict",
        subject="Phoenix, AZ · massage therapist",
        verdict="RECONFIGURE",
        reason="purity 61%",
        evidence={"purity_pct": 61},
    )
    label_eval_record(migrated_connection, sample_id, label="correct", labeled_by="austin")
    record_eval(
        migrated_connection,
        kind="judge_decision",
        subject="Cedar Massage",
        verdict="qualified",
        reason="offers deep tissue massage in Phoenix",
        evidence={"serves_market": "yes"},
        model="claude-haiku-5-5",
    )
    monkeypatch.setenv("DATABASE_URL", empty_database_url)
    get_settings.cache_clear()
    yield migrated_connection
    get_settings.cache_clear()


def render_page() -> AppTest:
    app = AppTest.from_file(str(PAGE_PATH), default_timeout=30)
    app.run()
    assert not app.exception, app.exception
    return app


@pytest.mark.usefixtures("review_database")
def test_lists_decisions_newest_first_with_their_labels() -> None:
    app = render_page()

    table = app.dataframe[0].value
    assert list(table.columns) == [
        "Id",
        "Kind",
        "Subject",
        "Verdict",
        "Reason",
        "Model",
        "Label",
        "Note",
        "Recorded",
    ]
    rows = table[["Kind", "Subject", "Verdict", "Model", "Label"]].values.tolist()
    assert rows == [
        ["judge_decision", "Cedar Massage", "qualified", "claude-haiku-5-5", "—"],
        ["sample_verdict", "Phoenix, AZ · massage therapist", "RECONFIGURE", "—", "correct"],
    ]
    metrics = {metric.label: metric.value for metric in app.metric}
    assert metrics == {"Records": "2", "Unlabeled": "1", "Correct": "1", "Incorrect": "0"}


@pytest.mark.usefixtures("review_database")
def test_unlabeled_filter_hides_labeled_records() -> None:
    app = render_page()

    app.checkbox(key="unlabeled_only").check().run()

    assert not app.exception, app.exception
    assert app.dataframe[0].value["Subject"].tolist() == ["Cedar Massage"]
    assert app.metric[1].value == "1"


@pytest.mark.usefixtures("review_database")
def test_kind_filter_shows_one_kind() -> None:
    app = render_page()

    app.selectbox(key="kind").select("sample_verdict").run()

    assert not app.exception, app.exception
    assert app.dataframe[0].value["Kind"].tolist() == ["sample_verdict"]


def test_saving_a_label_writes_it_and_updates_the_page(
    review_database: psycopg.Connection,
) -> None:
    app = render_page()
    # The newest record (Cedar Massage, unlabeled) is selected by default.
    (judge_id,) = [
        record.eval_record_id
        for record in read_eval_records(review_database)
        if record.subject == "Cedar Massage"
    ]

    app.radio(key=f"label_{judge_id}").set_value("incorrect")
    app.text_input(key=f"note_{judge_id}").set_value("the page says 'no deep tissue'")
    app.text_input(key="labeled_by").set_value("austin")
    app.button(key="save").click().run()

    assert not app.exception, app.exception
    (judge,) = read_eval_records(review_database, kind="judge_decision")
    assert judge.label == "incorrect"
    assert judge.note == "the page says 'no deep tissue'"
    assert judge.labeled_by == "austin"
    assert app.success[0].value == f"Saved: #{judge_id} labeled incorrect."
    assert app.dataframe[0].value["Label"].tolist() == ["incorrect", "correct"]
    metrics = {metric.label: metric.value for metric in app.metric}
    assert metrics == {"Records": "2", "Unlabeled": "0", "Correct": "1", "Incorrect": "1"}


def test_empty_database_says_nothing_to_review(
    migrated_connection: psycopg.Connection,
    empty_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", empty_database_url)
    get_settings.cache_clear()

    app = render_page()

    assert "Nothing to review" in app.info[0].value
    assert not app.dataframe
    get_settings.cache_clear()


def test_missing_database_url_shows_how_to_fix_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_settings.cache_clear()

    app = render_page()

    assert "DATABASE_URL is missing or malformed" in app.error[0].value
    get_settings.cache_clear()
