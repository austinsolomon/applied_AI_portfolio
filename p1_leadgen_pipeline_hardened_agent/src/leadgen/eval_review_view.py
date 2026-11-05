"""Eval review: one judge decision per screen, a binary "Was the judge right?" label, and how well
the judge agrees with those labels.

The workflow follows Hamel Husain's "Your AI Product Needs Evals": look at real decisions one at a
time next to the evidence the judge saw, label each pass or fail, write a one-line critique and tag
the failure mode on a fail, then measure the judge against the labels (per-verdict precision and
recall, not just raw agreement) and feed the labels back as fixtures. Blind mode hides the judge's
answer until the reviewer has made their own call, so the judge cannot anchor the label.

Shown as the dashboard's Eval review tab and as its own page (`leadgen evals review`). Reads
`eval_records`, writes only `eval_labels`, through the same functions the exporter uses, so what
you label here is exactly what becomes a fixture. The page shows business contact details and
page text, so it is for local use only.
"""

import html
import re
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import altair
import pandas
import psycopg
import streamlit
from pydantic import ValidationError

from leadgen.category_config import DEFAULT_EVAL_FAILURE_MODES, load_category_config
from leadgen.database import connect
from leadgen.eval_review import (
    EVAL_LABELS,
    JUDGE_VERDICTS,
    EvalRecord,
    JudgeAlignment,
    failure_mode_counts,
    judge_alignment,
    label_eval_record,
    label_judge_decision,
    labeled_jsonl,
    read_decision_markets,
    read_eval_records,
    review_queue,
)
from leadgen.qualification import UNVERIFIED_REASON_PREFIX
from leadgen.run_status import list_runs

ALL = "All"
SOURCE_NAMES = {"google_maps": "Google Maps", "exa": "Exa"}
TIER_NAMES = {1: "Tier 1 · fixed pages", 2: "Tier 2 · research agent"}
PASS, FAIL = "Pass", "Fail"
SHOW_CHOICES = ["Everything, in queue order", "Unlabeled", "Labeled", "Disagreements"]
# Enough for many weekly runs; the page reads them once per rerun.
RECORD_LIMIT = 5000

# Verdict chips: Exa blue for qualified, muted red and amber for rejected and review, all
# readable on the Marble surface.
VERDICT_CHIP_COLOURS = {
    "qualified": ("#E8ECFE", "#0A1A66"),
    "rejected": ("#FCE8E6", "#A50E0E"),
    "review": ("#FEF3D6", "#7A4B00"),
}
# The judge's quote, highlighted inside the page it came from.
HIGHLIGHT_STYLE = "background:#FFE9A8;color:#111318;padding:1px 2px;border-radius:3px"
PAGE_TEXT_STYLE = (
    "white-space:pre-wrap;max-height:460px;overflow-y:auto;background:#FFFFFF;"
    "border:1px solid #E4E1DC;border-radius:8px;padding:12px 14px;font-size:0.88rem;"
    "line-height:1.5;color:#111318"
)
CONTRADICTION_REASON = re.compile(r"^reason affirms '(?P<phrase>.+?)': (?P<clause>.*)$", re.DOTALL)


# --- Pure helpers: what the screen says, testable without Streamlit -------------------------


def quote_pattern(quote: str) -> re.Pattern[str] | None:
    """The quote with every run of whitespace matching any run of whitespace, the same
    normalization the citation check applies before it looks for the quote on the page."""
    words = quote.split()
    if not words:
        return None
    return re.compile(r"\s+".join(re.escape(word) for word in words))


def highlight_quote(page_text: str, quote: str) -> tuple[str, bool]:
    """The page as escaped HTML with the quote marked, and whether the quote was found.

    Page text is untrusted web content: every part of it is escaped, the match included.
    """
    pattern = quote_pattern(quote)
    match = pattern.search(page_text) if pattern else None
    if match is None:
        return html.escape(page_text), False
    return (
        html.escape(page_text[: match.start()])
        + f'<mark style="{HIGHLIGHT_STYLE}">{html.escape(match.group())}</mark>'
        + html.escape(page_text[match.end() :]),
        True,
    )


def is_web_link(url: str) -> bool:
    return urlsplit(url).scheme in {"http", "https"}


def judgment_of(record: EvalRecord) -> dict[str, Any]:
    return record.evidence.get("judgment") or {}


def sources_of(record: EvalRecord) -> dict[str, str]:
    sources = record.evidence.get("sources")
    return sources if isinstance(sources, dict) else {}


def shown_reason(record: EvalRecord) -> str:
    """The judge's reason without the citation check's prefix, which a check sentence explains."""
    return record.reason.removeprefix(UNVERIFIED_REASON_PREFIX).strip() or record.reason


def citation_problem(record: EvalRecord, eligible_services: Sequence[str]) -> str:
    """Which part of the citation check failed, as the end of a sentence."""
    judgment = judgment_of(record)
    page = sources_of(record).get(judgment.get("source_url") or "")
    quote = judgment.get("evidence_quote") or ""
    if page is None:
        return "the page it cited was not one of the pages it was given"
    if quote_pattern(quote) is None or not quote_pattern(quote).search(page):
        return "the quote could not be found on the cited page"
    if not set(judgment.get("eligible_services") or []) & set(eligible_services):
        return "none of the services it named are on the category's list"
    if judgment.get("serves_market") != "yes":
        return "it was not sure the firm serves the market"
    return "the quote, service or market could not be verified"


def contradiction_sentence(contradiction: EvalRecord, judge_verdict: str = "qualified") -> str:
    match = CONTRADICTION_REASON.match(contradiction.reason)
    if match is None:
        return (
            f"Automatic check: the judge said {judge_verdict}, but its own reason affirms a "
            f"disqualifier ({contradiction.reason}). The lead was still written to the CRM; it "
            "is flagged here for a person to check."
        )
    clause = match["clause"].strip()
    quoted_clause = f' ("{clause}")' if clause.lower() != match["phrase"].lower() else ""
    return (
        f"Automatic check: the judge said {judge_verdict}, but its own reason says "
        f"'{match['phrase']}'{quoted_clause}, which the category lists as a disqualifier. The "
        "lead was still written to the CRM; it is flagged here for a person to check."
    )


def automatic_check_sentences(
    record: EvalRecord,
    contradictions: Sequence[EvalRecord] = (),
    eligible_services: Sequence[str] = (),
) -> list[str]:
    """Every automatic check that touched this decision, as plain sentences."""
    sentences = []
    proposed = record.evidence.get("proposed_verdict")
    if record.reason == "No readable evidence":
        sentences.append(
            "Automatic check: no page about this business could be read, so it went to review "
            "without asking the judge."
        )
    elif record.reason.startswith("Judge returned"):
        sentences.append(
            "Automatic check: the judge's answer came back incomplete, so the lead was kept for "
            "review."
        )
    if record.reason.startswith(UNVERIFIED_REASON_PREFIX):
        sentences.append(
            f"Automatic check: the judge said {proposed or 'qualified'}, but "
            f"{citation_problem(record, eligible_services)}, so the verdict was lowered to "
            f"{record.verdict}."
        )
    elif proposed and proposed != record.verdict and record.verdict == "rejected":
        sentences.append(
            f"Automatic check: the judge said {proposed}, but also said the firm does not serve "
            "the market, so the verdict was changed to rejected."
        )
    sentences.extend(contradiction_sentence(hit, record.verdict) for hit in contradictions)
    return sentences


def other_decision_sentence(record: EvalRecord) -> str:
    """A grader or contradiction record as one sentence, for the secondary table."""
    if record.kind == "contradiction":
        sentence = contradiction_sentence(record).removeprefix("Automatic check: ")
        return sentence[:1].upper() + sentence[1:]
    if record.kind == "sample_verdict":
        evidence = record.evidence
        if "category_purity" in evidence:
            return (
                f"The Maps sample grader read {evidence.get('count', '?')} sampled places: "
                f"{evidence['category_purity']:.0%} on-category, "
                f"{evidence.get('in_market_share', 0):.0%} inside the market, so the cell was "
                f"graded {record.verdict}."
            )
        return f"The Maps sample grader graded the cell {record.verdict}. {record.reason}".strip()
    return f"{record.verdict}: {record.reason}"


def matches_filters(
    record: EvalRecord,
    *,
    verdicts: Sequence[str],
    source: str,
    show: str,
) -> bool:
    if verdicts and record.verdict not in verdicts:
        return False
    if source != ALL and SOURCE_NAMES.get(record.evidence.get("source"), "Unknown") != source:
        return False
    if show == "Unlabeled":
        return not record.is_labeled
    if show == "Labeled":
        return record.is_labeled
    if show == "Disagreements":
        return record.is_disagreement
    return True


def percent_or_dash(value: float | None) -> str:
    return "—" if value is None else f"{value:.0%}"


# --- Rendering --------------------------------------------------------------------------------


def verdict_chip(verdict: str) -> str:
    background, colour = VERDICT_CHIP_COLOURS.get(verdict, ("#E3E5EA", "#3C4043"))
    return (
        f'<span style="background:{background};color:{colour};border-radius:999px;'
        f'padding:2px 10px;font-weight:600;font-size:0.85rem">{html.escape(verdict)}</span>'
    )


def fact(label: str, value: str) -> str:
    return (
        '<div style="min-width:0"><div style="font-size:0.7rem;letter-spacing:0.06em;'
        f'text-transform:uppercase;color:#5B5F68">{html.escape(label)}</div>'
        f'<div style="font-size:0.92rem;color:#111318">{html.escape(value)}</div></div>'
    )


def decision_header_html(record: EvalRecord, market: str, run_label: str) -> str:
    tier = record.evidence.get("tier")
    facts = [
        fact("Market", market),
        fact("Found by", SOURCE_NAMES.get(record.evidence.get("source"), "Unknown")),
        fact("Run", run_label),
        fact("Judge tier", TIER_NAMES.get(tier, f"Tier {tier}" if tier else "—")),
        fact("Model", record.model or "—"),
    ]
    return (
        '<div style="background:#FFFFFF;border:1px solid #E4E1DC;border-radius:10px;'
        'padding:14px 16px">'
        '<div style="font-family:Newsreader,Georgia,serif;font-size:1.6rem;line-height:1.2;'
        f'color:#111318">{html.escape(record.subject)}</div>'
        '<div style="display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;'
        f'margin-top:10px">{"".join(facts)}</div></div>'
    )


def page_html(url: str, text_html: str, caption: str) -> str:
    link = (
        f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener noreferrer" '
        f'style="color:#1840ED;word-break:break-all">{html.escape(url)} ↗</a>'
        if is_web_link(url)
        else html.escape(url)
    )
    return (
        f'<div style="font-size:0.8rem;color:#5B5F68;margin:2px 0 6px">{caption} {link}</div>'
        f'<div style="{PAGE_TEXT_STYLE}">{text_html}</div>'
    )


def render_evidence(record: EvalRecord, revealed: bool) -> None:
    """The pages the judge read. Once revealed, the cited page comes first with the judge's quote
    highlighted in it; in blind mode the pages appear in the order they were gathered."""
    sources = sources_of(record)
    streamlit.markdown("**Evidence the judge read**")
    if not sources:
        streamlit.caption("No page text was saved with this decision.")
        return
    judgment = judgment_of(record)
    cited_url = judgment.get("source_url") or ""
    quote = judgment.get("evidence_quote") or ""
    ordered = list(sources)
    if revealed and cited_url in sources:
        ordered.remove(cited_url)
        ordered.insert(0, cited_url)
    for index, url in enumerate(ordered):
        text = sources[url] or ""
        if revealed and url == cited_url:
            text_html, found = highlight_quote(text, quote)
            caption = "Cited page" + (
                ", judge's quote highlighted:" if found else " (the judge's quote is not on it):"
            )
        else:
            text_html, caption = html.escape(text), f"Page {index + 1} of {len(ordered)}:"
        if index == 0:
            streamlit.html(page_html(url, text_html, caption))
        else:
            with streamlit.expander(f"Page {index + 1} of {len(ordered)}: {url}"):
                streamlit.html(page_html(url, text_html, caption))
    if revealed and quote and cited_url not in sources:
        streamlit.warning(f"The judge cited a page it was not given: {cited_url or '(none)'}")


def render_judge_answer(record: EvalRecord, check_sentences: list[str]) -> None:
    judgment = judgment_of(record)
    streamlit.html(
        '<div style="margin:4px 0 2px;font-size:0.8rem;color:#5B5F68">The judge decided</div>'
        f"<div>{verdict_chip(record.verdict)}</div>"
        f'<div style="margin-top:8px;color:#111318">{html.escape(shown_reason(record))}</div>'
    )
    quote = judgment.get("evidence_quote")
    if quote:
        streamlit.html(
            '<div style="margin-top:6px;border-left:3px solid #FFD666;padding:2px 10px;'
            f'color:#3C4043;font-size:0.9rem">“{html.escape(quote)}”</div>'
        )
    services = ", ".join(judgment.get("eligible_services") or []) or "none named"
    streamlit.caption(
        f"Services it named: {services} · Serves the market: {judgment.get('serves_market', '—')}"
    )
    for sentence in check_sentences:
        streamlit.info(sentence)


def save_judge_label(record: EvalRecord, next_record_id: int | None, **label: Any) -> None:
    try:
        with connect() as connection:
            label_judge_decision(connection, record, **label)
    except (ValueError, psycopg.Error) as error:
        streamlit.error(f"Label not saved: {error}")
        return
    outcome = "pass" if label["passed"] else f"fail (should be {label['correct_verdict']})"
    streamlit.session_state["saved_message"] = f"Saved: {record.subject} labeled {outcome}."
    if next_record_id is not None:
        streamlit.session_state["eval_current_id"] = next_record_id
    streamlit.rerun()


def render_label_form(
    record: EvalRecord,
    *,
    blind_pick: str | None,
    failure_modes: Sequence[str],
    next_record_id: int | None,
) -> None:
    record_id = record.eval_record_id
    streamlit.markdown("**Was the judge right?**")
    if record.is_labeled:
        default = PASS if record.label == "correct" else FAIL
    elif blind_pick is not None:
        default = PASS if blind_pick == record.verdict else FAIL
    else:
        default = None
    choice = streamlit.radio(
        "Was the judge right?",
        [PASS, FAIL],
        index=None if default is None else [PASS, FAIL].index(default),
        key=f"pass_fail_{record_id}",
        horizontal=True,
        label_visibility="collapsed",
        help="Pass: the verdict and its reason hold up against the page. Fail: anything wrong.",
    )
    label: dict[str, Any] = {}
    if choice == FAIL:
        critique = streamlit.text_input(
            "Critique (one line, required)",
            value=record.critique or record.note,
            key=f"critique_{record_id}",
            placeholder="e.g. the page says the advisor is with Edward Jones",
        )
        should_be = record.correct_verdict or blind_pick
        correct_verdict = streamlit.radio(
            "What it should have been",
            JUDGE_VERDICTS,
            index=JUDGE_VERDICTS.index(should_be) if should_be in JUDGE_VERDICTS else None,
            key=f"correct_verdict_{record_id}",
            horizontal=True,
        )
        # Tags saved before the list changed stay selectable.
        options = list(dict.fromkeys([*failure_modes, *record.failure_modes]))
        tags = streamlit.multiselect(
            "Failure modes",
            options,
            default=list(record.failure_modes),
            key=f"failure_modes_{record_id}",
            help="How it went wrong. The list lives in config/category.yaml (eval_failure_modes).",
        )
        label = {
            "passed": False,
            "critique": critique,
            "correct_verdict": correct_verdict,
            "failure_modes": tags,
        }
    elif choice == PASS:
        note = streamlit.text_input(
            "Note (optional)", value=record.critique, key=f"pass_note_{record_id}"
        )
        label = {"passed": True, "critique": note}
    labeled_by = streamlit.text_input("Reviewer", value="reviewer", key="labeled_by")
    if streamlit.button(
        "Save label and go to the next decision",
        key="save",
        type="primary",
        disabled=choice is None,
    ):
        save_judge_label(record, next_record_id, labeled_by=labeled_by, **label)


def move_to(record_id: int) -> None:
    streamlit.session_state["eval_current_id"] = record_id


def blind_pick_key(record_id: int) -> str:
    return f"blind_pick_{record_id}"


def remember_blind_pick(record_id: int) -> None:
    """Keep the reviewer's blind verdict outside the radio's own state: Streamlit drops a widget's
    state once it is no longer drawn, and the radio disappears as soon as the judge is revealed."""
    streamlit.session_state[blind_pick_key(record_id)] = streamlit.session_state[
        f"blind_radio_{record_id}"
    ]


def render_decision(
    queue: list[EvalRecord],
    *,
    blind_mode: bool,
    markets: dict[tuple[UUID, str], str],
    run_labels: dict[UUID, str],
    contradictions: dict[tuple[UUID | None, str], list[EvalRecord]],
    category_services: Sequence[str],
    failure_modes: Sequence[str],
) -> None:
    ids = [record.eval_record_id for record in queue]
    current_id = streamlit.session_state.get("eval_current_id")
    position = ids.index(current_id) if current_id in ids else 0
    record = queue[position]

    navigation = streamlit.columns([1, 1, 4])
    navigation[0].button(
        "← Previous",
        key="previous",
        disabled=position == 0,
        on_click=move_to,
        args=(ids[position - 1],) if position > 0 else (record.eval_record_id,),
        width="stretch",
    )
    navigation[1].button(
        "Next →",
        key="next",
        disabled=position == len(ids) - 1,
        on_click=move_to,
        args=(ids[position + 1],) if position < len(ids) - 1 else (record.eval_record_id,),
        width="stretch",
    )
    status = (
        "not labeled yet"
        if not record.is_labeled
        else f"labeled {'pass' if record.label == 'correct' else 'fail'} by {record.labeled_by}"
    )
    navigation[2].markdown(
        f"Decision **{position + 1} of {len(queue)}** in this queue · #{record.eval_record_id} · "
        f"{status}"
    )

    market = record.evidence.get("market") or markets.get(
        (record.run_id, record.subject), "unknown market"
    )
    run_label = (
        "—" if record.run_id is None else run_labels.get(record.run_id, str(record.run_id)[:8])
    )
    streamlit.html(decision_header_html(record, market, run_label))

    blind_pick = None
    if blind_mode and not record.is_labeled:
        blind_pick = streamlit.session_state.get(blind_pick_key(record.eval_record_id))
    revealed = not blind_mode or record.is_labeled or blind_pick is not None

    left, right = streamlit.columns([2, 3], gap="large")
    with left:
        if not revealed:
            streamlit.markdown("**Blind first: what would you decide?**")
            streamlit.radio(
                "Your verdict",
                JUDGE_VERDICTS,
                index=None,
                key=f"blind_radio_{record.eval_record_id}",
                on_change=remember_blind_pick,
                args=(record.eval_record_id,),
                horizontal=True,
                label_visibility="collapsed",
            )
            streamlit.caption(
                "Read the evidence and pick a verdict. The judge's verdict, its reason and the "
                "quote it relied on appear once you have picked, so they cannot sway you. Turn "
                "blind mode off at the top to see them straight away."
            )
        else:
            if blind_pick is not None:
                agreement = "agrees" if blind_pick == record.verdict else "disagrees"
                streamlit.caption(f"You said **{blind_pick}**; the judge {agreement}.")
            render_judge_answer(
                record,
                automatic_check_sentences(
                    record,
                    contradictions.get((record.run_id, record.subject), []),
                    category_services,
                ),
            )
            streamlit.divider()
            next_ids = ids[position + 1 :] or ids[:position]
            render_label_form(
                record,
                blind_pick=blind_pick,
                failure_modes=failure_modes,
                next_record_id=next_ids[0] if next_ids else None,
            )
    with right:
        render_evidence(record, revealed)


def cell_colours(judge: str, human: str, count: int) -> tuple[str, str]:
    """(fill, text) for one confusion-matrix cell: gray when empty, Exa blue where the judge and
    the reviewer agree, muted red where they disagree."""
    if count == 0:
        return "#F2F0EC", "#5B5F68"
    if judge == human:
        return "#1840ED", "#FFFFFF"
    return "#F6C7C3", "#7A1410"


def confusion_chart(alignment: JudgeAlignment) -> altair.LayerChart:
    cells = pandas.DataFrame(
        [
            {
                "Judge said": judge,
                "You said": human,
                "Decisions": count,
                "fill": cell_colours(judge, human, count)[0],
                "ink": cell_colours(judge, human, count)[1],
            }
            for (judge, human), count in alignment.confusion.items()
        ]
    )
    order = list(JUDGE_VERDICTS)
    base = altair.Chart(cells).encode(
        x=altair.X(
            "You said:N", sort=order, axis=altair.Axis(orient="top", labelAngle=0, ticks=False)
        ),
        y=altair.Y("Judge said:N", sort=order, axis=altair.Axis(ticks=False)),
    )
    # Colours come straight from the data (scale=None), so the theme's palette cannot remap them.
    squares = base.mark_rect(stroke="#FAF9F8", strokeWidth=3, cornerRadius=4).encode(
        color=altair.Color("fill:N", scale=None, legend=None),
        tooltip=["Judge said", "You said", "Decisions"],
    )
    numbers = base.mark_text(fontSize=16, fontWeight=600).encode(
        text="Decisions:Q", color=altair.Color("ink:N", scale=None, legend=None)
    )
    return (squares + numbers).properties(height=210)


def alignment_table(alignment: JudgeAlignment) -> pandas.DataFrame:
    return pandas.DataFrame(
        [
            {
                "Verdict": score.verdict,
                "Judge said it": score.judge_count,
                "You said it": score.human_count,
                "Both": score.agreed,
                "Precision": percent_or_dash(score.precision),
                "Recall": percent_or_dash(score.recall),
            }
            for score in alignment.scores
        ]
    )


def render_alignment(records: list[EvalRecord], run_labels: dict[UUID, str]) -> None:
    streamlit.subheader("Judge alignment")
    runs = sorted({record.run_id for record in records if record.run_id}, key=str)
    run_choice = streamlit.columns([2, 3])[0].selectbox(
        "Run",
        [None, *runs],
        format_func=lambda run_id: (
            "All runs" if run_id is None else run_labels.get(run_id, str(run_id)[:8])
        ),
        key="alignment_run",
    )
    scoped = [record for record in records if run_choice is None or record.run_id == run_choice]
    alignment = judge_alignment(scoped)
    labeled = [record for record in scoped if record.is_labeled]
    metrics = streamlit.columns(3)
    metrics[0].metric("Decisions compared", alignment.compared)
    metrics[1].metric("Raw agreement", percent_or_dash(alignment.agreement))
    metrics[2].metric(
        "Passed",
        f"{sum(record.label == 'correct' for record in labeled)} of {len(labeled)}",
    )
    if not alignment.compared:
        streamlit.info("Label a few decisions to see how the judge lines up with you.")
        return
    chart_column, table_column = streamlit.columns([2, 3], gap="large")
    chart_column.altair_chart(confusion_chart(alignment), width="stretch")
    table_column.dataframe(alignment_table(alignment), hide_index=True)
    streamlit.caption(
        "Rows are what the judge decided, columns what you said it should have been; the "
        "diagonal is where you agree. Precision: when the judge says a verdict, how often you "
        "agree. Recall: of the decisions you say deserve a verdict, how many the judge gave it. "
        "Most leads are rejected, so raw agreement can look high while the judge still misses "
        "the rare qualified leads; read precision and recall for each verdict on its own. A "
        "fail labeled before the correct verdict was recorded is left out of the matrix."
    )


def render_failure_modes(records: list[EvalRecord]) -> None:
    streamlit.subheader("Failure modes")
    counts = failure_mode_counts(records)
    if not counts:
        streamlit.caption("No failure modes tagged yet. Tag them when you fail a decision.")
        return
    frame = pandas.DataFrame(
        [{"Failure mode": tag, "Decisions": count} for tag, count in counts.most_common()]
    )
    chart = (
        altair.Chart(frame)
        .mark_bar(color="#1840ED", cornerRadiusTopRight=4, cornerRadiusBottomRight=4)
        .encode(
            x=altair.X(
                "Decisions:Q",
                axis=altair.Axis(tickMinStep=1, format="d"),
                scale=altair.Scale(domainMin=0, nice=True),
            ),
            y=altair.Y("Failure mode:N", sort="-x", title=None, axis=altair.Axis(labelLimit=320)),
            tooltip=["Failure mode", "Decisions"],
        )
    )
    labels = chart.mark_text(align="left", dx=4, color="#111318").encode(text="Decisions:Q")
    # Half width: a handful of tags reads better as short bars than as page-wide stripes.
    streamlit.columns([3, 2])[0].altair_chart(
        (chart + labels).properties(height=altair.Step(28)), width="stretch"
    )
    streamlit.caption(
        "How often each tag was given across failed decisions; fix the most common one first."
    )


def render_other_decisions(others: list[EvalRecord]) -> None:
    with streamlit.expander(
        f"Other automatic decisions: Maps sample grades and CRM contradiction checks "
        f"({len(others)})"
    ):
        if not others:
            streamlit.caption("None recorded yet.")
            return
        streamlit.dataframe(
            pandas.DataFrame(
                [
                    {
                        "Id": record.eval_record_id,
                        "About": record.subject,
                        "What happened": other_decision_sentence(record),
                        "Label": {"correct": "pass", "incorrect": "fail"}.get(
                            record.label or "", "—"
                        ),
                        "Note": record.note,
                        "Recorded": record.created_at.strftime("%Y-%m-%d %H:%M"),
                    }
                    for record in others
                ]
            ),
            hide_index=True,
            column_config={"What happened": streamlit.column_config.TextColumn(width="large")},
        )
        columns = streamlit.columns([3, 1, 3, 1])
        chosen = columns[0].selectbox(
            "Decision",
            others,
            format_func=lambda record: f"#{record.eval_record_id} {record.subject}",
            key="other_record",
        )
        label = columns[1].radio(
            "Right?",
            EVAL_LABELS,
            format_func={"correct": PASS, "incorrect": FAIL}.get,
            index=EVAL_LABELS.index(chosen.label) if chosen.is_labeled else 0,
            key=f"other_label_{chosen.eval_record_id}",
        )
        note = columns[2].text_input(
            "Note", value=chosen.note, key=f"other_note_{chosen.eval_record_id}"
        )
        if columns[3].button("Save", key="save_other"):
            try:
                with connect() as connection:
                    label_eval_record(
                        connection,
                        chosen.eval_record_id,
                        label=label,
                        note=note,
                        labeled_by=streamlit.session_state.get("labeled_by") or "reviewer",
                    )
            except (ValueError, psycopg.Error) as error:
                streamlit.error(f"Label not saved: {error}")
                return
            outcome = "pass" if label == "correct" else "fail"
            streamlit.session_state["saved_message"] = (
                f"Saved: #{chosen.eval_record_id} labeled {outcome}."
            )
            streamlit.rerun()


def load_category_lists() -> tuple[list[str], list[str]]:
    """The category's failure-mode tags and eligible services; generic tags when the file is
    missing, so the page still opens."""
    try:
        category = load_category_config()
    except Exception:
        return list(DEFAULT_EVAL_FAILURE_MODES), []
    return list(category.eval_failure_modes), list(category.rubric.eligible_services)


def render_eval_review() -> None:
    saved_message = streamlit.session_state.pop("saved_message", None)
    if saved_message:
        streamlit.success(saved_message)

    try:
        with connect() as connection:
            records = read_eval_records(connection, limit=RECORD_LIMIT)
            markets = read_decision_markets(connection)
            run_labels = dict(list_runs(connection, limit=500))
    except ValidationError:
        streamlit.error(
            "DATABASE_URL is missing or malformed. Copy .env.example to .env, or export "
            "DATABASE_URL before starting the review page."
        )
        return
    except psycopg.Error as error:
        streamlit.error(f"Cannot read eval records from the database: {error}")
        return

    failure_modes, category_services = load_category_lists()
    judge_records = [record for record in records if record.kind == "judge_decision"]
    others = [record for record in records if record.kind != "judge_decision"]
    contradictions: dict[tuple[UUID | None, str], list[EvalRecord]] = {}
    for record in records:
        if record.kind == "contradiction":
            contradictions.setdefault((record.run_id, record.subject), []).append(record)

    intro, blind_column = streamlit.columns([4, 1])
    intro.caption(
        "Read one judge decision at a time against the page it read, then say whether the judge "
        "was right. A fail takes a one-line critique, the verdict it should have been and a "
        "failure-mode tag. This is the error-analysis loop from Hamel Husain's \"Your AI "
        'Product Needs Evals". Unlabeled review verdicts come first.'
    )
    blind_mode = blind_column.toggle(
        "Blind mode",
        value=True,
        key="blind_mode",
        help="Hide the judge's verdict, reason and quote until you have made your own call.",
    )

    if not judge_records:
        streamlit.info(
            "Nothing to review: no judge decisions recorded yet. Run the pipeline first."
        )
        render_other_decisions(others)
        return

    runs = sorted({record.run_id for record in judge_records if record.run_id}, key=str)
    filters = streamlit.columns([3, 3, 2, 2])
    run_choice = filters[0].selectbox(
        "Run",
        [None, *runs],
        format_func=lambda run_id: (
            "All runs" if run_id is None else run_labels.get(run_id, str(run_id)[:8])
        ),
        key="queue_run",
    )
    verdicts = filters[1].multiselect(
        "Judge verdict", JUDGE_VERDICTS, key="queue_verdicts", placeholder="All verdicts"
    )
    source = filters[2].selectbox("Found by", [ALL, *SOURCE_NAMES.values()], key="queue_source")
    show = filters[3].selectbox("Show", SHOW_CHOICES, key="queue_show")

    in_run = [
        record for record in judge_records if run_choice is None or record.run_id == run_choice
    ]
    labeled_count = sum(record.is_labeled for record in in_run)
    streamlit.progress(
        labeled_count / len(in_run) if in_run else 0.0,
        text=f"Labeled {labeled_count} of {len(in_run)} judge decisions"
        + ("" if run_choice is None else " in this run"),
    )

    queue = review_queue(
        record
        for record in in_run
        if matches_filters(record, verdicts=verdicts, source=source, show=show)
    )
    if not queue:
        streamlit.info("No judge decisions match these filters.")
    else:
        render_decision(
            queue,
            blind_mode=blind_mode,
            markets=markets,
            run_labels=run_labels,
            contradictions=contradictions,
            category_services=category_services,
            failure_modes=failure_modes,
        )

    streamlit.divider()
    render_alignment(judge_records, run_labels)
    render_failure_modes(judge_records)

    streamlit.subheader("Feed the labels back")
    streamlit.download_button(
        "Export labeled decisions (JSONL)",
        labeled_jsonl(records),
        file_name="labeled_decisions.jsonl",
        mime="application/jsonl",
        key="export_labels",
        disabled=not any(record.is_labeled for record in records),
    )
    streamlit.caption(
        "Labels become regression fixtures (`leadgen evals export-fixtures`, same fields as this "
        "file: your verdict, failure modes, critique) and worked examples for tuning the judge "
        "requirement in config/category.yaml."
    )
    render_other_decisions(others)
