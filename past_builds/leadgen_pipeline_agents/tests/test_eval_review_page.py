"""The eval review page, rendered headless with Streamlit's AppTest against a seeded database."""

import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from streamlit.testing.v1 import AppTest

from leadgen import eval_review_page as page_module
from leadgen.eval_review import label_judge_decision, read_eval_records, record_eval
from leadgen.settings import get_settings

PAGE_PATH = Path(page_module.__file__)
RUN_ID = uuid.uuid4()


def judge_evidence(page: str, quote: str, url: str, **extra) -> dict:
    return {
        "sources": {url: page},
        "judgment": {
            "verdict": extra.get("verdict", "qualified"),
            "reason": "r",
            "evidence_quote": quote,
            "source_url": url,
            "eligible_services": ["financial planning"],
            "serves_market": "yes",
        },
        "tier": 1,
        "source": extra.get("source", "exa"),
        "market": "Phoenix, AZ",
        "proposed_verdict": extra.get("verdict", "qualified"),
    }


@pytest.fixture
def review_database(
    migrated_connection: psycopg.Connection,
    empty_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[psycopg.Connection]:
    """Three judge decisions (one labeled), a contradiction and a sample grade."""
    record_eval(
        migrated_connection,
        kind="sample_verdict",
        subject="Phoenix, AZ · financial planner",
        verdict="PROCEED",
        evidence={"count": 20, "category_purity": 0.95, "in_market_share": 0.9},
        run_id=RUN_ID,
    )
    arcadia = record_eval(
        migrated_connection,
        kind="judge_decision",
        subject="Arcadia Financial Planning",
        verdict="qualified",
        reason="Independent firm; Tom Becker, CFP®.",
        evidence=judge_evidence(
            "Arcadia is independent. Tom Becker, CFP®, plans.", "Tom Becker, CFP®", "https://a.ex/"
        ),
        model="claude-haiku-4-5",
        run_id=RUN_ID,
    )
    record_eval(
        migrated_connection,
        kind="judge_decision",
        subject="Driscoll Financial Advisors",
        verdict="qualified",
        reason="Edward Jones advisor with the CFP®.",
        evidence=judge_evidence(
            "Kevin Driscoll, CFP® <b>Edward Jones</b>", "Kevin Driscoll, CFP®", "https://d.ex/"
        ),
        model="claude-haiku-4-5",
        run_id=RUN_ID,
    )
    record_eval(
        migrated_connection,
        kind="contradiction",
        subject="Driscoll Financial Advisors",
        verdict="contradiction",
        reason="reason affirms 'edward jones': Edward Jones advisor with the CFP®",
        evidence={"lead_id": 1},
        run_id=RUN_ID,
    )
    record_eval(
        migrated_connection,
        kind="judge_decision",
        subject="Saguaro Ridge Wealth Partners",
        verdict="review",
        reason="Planner is pursuing the CFP®.",
        evidence=judge_evidence(
            "Dana is currently pursuing the CFP®.",
            "pursuing the CFP®",
            "https://s.ex/",
            verdict="review",
            source="google_maps",
        ),
        model="claude-haiku-4-5",
        run_id=RUN_ID,
    )
    (arcadia_record,) = [
        record
        for record in read_eval_records(migrated_connection)
        if record.eval_record_id == arcadia
    ]
    label_judge_decision(migrated_connection, arcadia_record, passed=True, labeled_by="austin")
    monkeypatch.setenv("DATABASE_URL", empty_database_url)
    get_settings.cache_clear()
    yield migrated_connection
    get_settings.cache_clear()


def render_page() -> AppTest:
    app = AppTest.from_file(str(PAGE_PATH), default_timeout=30)
    app.run()
    assert not app.exception, app.exception
    return app


def html_text(app: AppTest) -> str:
    return "\n".join(str(element.value) for element in app.get("html"))


def record_id(connection: psycopg.Connection, subject: str) -> int:
    (found,) = [
        record.eval_record_id
        for record in read_eval_records(connection, kind="judge_decision")
        if record.subject == subject
    ]
    return found


def test_blind_mode_shows_the_review_verdict_first_without_the_judges_answer(
    review_database: psycopg.Connection,
) -> None:
    app = render_page()

    assert app.toggle(key="blind_mode").value is True
    assert app.get("progress")[0].proto.text == "Labeled 1 of 3 judge decisions"
    page = html_text(app)
    # The unlabeled review verdict comes first, with its market, source and page text.
    assert "Saguaro Ridge Wealth Partners" in page
    assert "Phoenix, AZ" in page and "Google Maps" in page
    assert "Dana is currently pursuing the CFP®." in page
    # Hidden: the verdict, the reason, the highlight.
    assert "Planner is pursuing the CFP®." not in page
    assert "<mark" not in page
    # No "Was the judge right?" yet: the reviewer picks their own verdict first.
    saguaro = record_id(review_database, "Saguaro Ridge Wealth Partners")
    assert [radio.key for radio in app.radio if "pass_fail" in radio.key] == []
    assert app.radio(key=f"blind_radio_{saguaro}").value is None
    assert any("1 of 3" in markdown.value for markdown in app.markdown)


def test_picking_a_blind_verdict_reveals_the_judge_and_presets_the_label(
    review_database: psycopg.Connection,
) -> None:
    saguaro = record_id(review_database, "Saguaro Ridge Wealth Partners")
    app = render_page()

    app.radio(key=f"blind_radio_{saguaro}").set_value("rejected").run()

    assert not app.exception, app.exception
    page = html_text(app)
    assert "Planner is pursuing the CFP®." in page
    assert ">pursuing the CFP®</mark>" in page
    assert app.radio(key=f"pass_fail_{saguaro}").value == "Fail"
    assert app.radio(key=f"correct_verdict_{saguaro}").value == "rejected"


def test_a_fail_without_a_critique_is_refused_then_saved_with_tags(
    review_database: psycopg.Connection,
) -> None:
    saguaro = record_id(review_database, "Saguaro Ridge Wealth Partners")
    app = render_page()
    app.radio(key=f"blind_radio_{saguaro}").set_value("rejected").run()

    app.button(key="save").click().run()

    assert "critique" in app.error[0].value
    assert read_eval_records(review_database, kind="judge_decision")[0].label is None

    app.text_input(key=f"critique_{saguaro}").set_value("Only the ChFC® is held.")
    app.multiselect(key=f"failure_modes_{saguaro}").select("no CFP® on the page")
    app.text_input(key="labeled_by").set_value("austin")
    app.button(key="save").click().run()

    assert not app.exception, app.exception
    (saved,) = [
        record
        for record in read_eval_records(review_database, kind="judge_decision")
        if record.eval_record_id == saguaro
    ]
    assert saved.label == "incorrect" and saved.correct_verdict == "rejected"
    assert saved.critique == "Only the ChFC® is held."
    assert saved.failure_modes == ("no CFP® on the page",)
    assert app.success[0].value.startswith("Saved: Saguaro Ridge Wealth Partners labeled fail")
    # The page moved on to the next unlabeled decision.
    assert "Driscoll Financial Advisors" in html_text(app)
    assert app.get("progress")[0].proto.text == "Labeled 2 of 3 judge decisions"


def test_with_blind_mode_off_the_checks_read_as_sentences(
    review_database: psycopg.Connection,
) -> None:
    app = render_page()
    app.toggle(key="blind_mode").set_value(False).run()
    app.button(key="next").click().run()

    assert not app.exception, app.exception
    page = html_text(app)
    assert "Driscoll Financial Advisors" in page
    # Untrusted page text is escaped; the quote is highlighted.
    assert "&lt;b&gt;Edward Jones&lt;/b&gt;" in page and "<b>Edward Jones" not in page
    assert ">Kevin Driscoll, CFP®</mark>" in page
    infos = [info.value for info in app.info]
    assert any(
        info.startswith(
            "Automatic check: the judge said qualified, but its own reason says 'edward jones'"
        )
        for info in infos
    )


def test_alignment_and_filters(review_database: psycopg.Connection) -> None:
    app = render_page()

    metrics = {metric.label: metric.value for metric in app.metric}
    assert metrics == {"Decisions compared": "1", "Raw agreement": "100%", "Passed": "1 of 1"}
    scores = app.dataframe[0].value
    assert scores["Precision"].tolist() == ["100%", "—", "—"]

    app.selectbox(key="queue_show").select("Labeled").run()
    assert "Arcadia Financial Planning" in html_text(app)
    app.selectbox(key="queue_source").select("Exa").run()
    app.selectbox(key="queue_show").select("Unlabeled").run()
    assert "Driscoll Financial Advisors" in html_text(app)
    assert "Saguaro" not in html_text(app)


def test_empty_database_says_nothing_to_review(
    migrated_connection: psycopg.Connection,
    empty_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", empty_database_url)
    get_settings.cache_clear()

    app = render_page()

    assert "Nothing to review" in app.info[0].value
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
