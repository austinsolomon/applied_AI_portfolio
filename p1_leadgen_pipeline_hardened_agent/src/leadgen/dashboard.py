"""Lead-gen dashboard: demand gaps, the current run's stages, run history, sources and spend, and
eval review, one tab each.

Run with `leadgen dashboard`. Every tab reads through the same functions the agent and the
runner use (`gaps.py`, `run_status.py`, `eval_review.py`), so the page and the pipeline always see
the same numbers. Only the Eval review tab writes, and only labels.
"""

import html
import json

import altair
import pandas
import psycopg
import streamlit
from pydantic import ValidationError

from leadgen import data_model, run_status
from leadgen.audit import ACTORS
from leadgen.category_config import load_category_config
from leadgen.database import connect
from leadgen.eval_review_view import render_eval_review
from leadgen.gaps import STATUS_BELOW_GOAL, MarketDemand, read_market_demand

# Market, then the goal, what is in hand, what is missing, and the share of the goal in hand.
COLUMN_ORDER = ["Market", "Lead Goal", "Leads Available", "Leads Needed", "Leads Available %"]

# How far a market is from its goal, for the progress chart: the band and its colour. One hue,
# Exa blue, light (far) to deep navy (goal met); the ramp passes the ordinal palette checks on
# the Marble surface, and every bar also carries its "available / goal" label.
DISTANCE_BANDS = [
    (100, "Goal met", "#0A1A66"),
    (75, "Close (75–99%)", "#1840ED"),
    (40, "Halfway (40–74%)", "#4D6FF1"),
    (0, "Far (under 40%)", "#8EA3F6"),
]

# Stage track colours: gray until a stage finishes, Exa blue once done.
STAGE_COLOURS = {
    run_status.STAGE_DONE: ("#1840ED", "#FFFFFF"),
    run_status.STAGE_RUNNING: ("#C9D4FB", "#0A1A66"),
    run_status.STAGE_FAILED: ("#D93025", "#FFFFFF"),
    run_status.STAGE_BLOCKED: ("#F29900", "#FFFFFF"),
    run_status.STAGE_WAITING: ("#E3E5EA", "#5F6368"),
}
STAGE_STATUS_WORDS = {
    run_status.STAGE_DONE: "done",
    run_status.STAGE_RUNNING: "running",
    run_status.STAGE_FAILED: "failed",
    run_status.STAGE_BLOCKED: "needs a human",
    run_status.STAGE_WAITING: "waiting",
}
DATABASE_URL_HELP = (
    "DATABASE_URL is missing or malformed. Copy .env.example to .env, or export "
    "DATABASE_URL before starting the dashboard."
)


def usd(value) -> str:
    return "—" if value is None else f"${value:,.4f}".rstrip("0").rstrip(".")


def percent(value) -> str:
    return "—" if value is None else f"{value:.0%}"


def distance_band(available_pct: int) -> str:
    return next(label for floor, label, _ in DISTANCE_BANDS if available_pct >= floor)


def demand_table(markets: list[MarketDemand]) -> pandas.DataFrame:
    rows = [
        {
            "Market": f"{market.market_name}, {market.state}",
            "Lead Goal": market.lead_goal,
            "Leads Available": market.available_leads,
            "Leads Needed": market.leads_needed,
            "Leads Available %": market.leads_available_pct,
        }
        for market in markets
    ]
    return pandas.DataFrame(rows, columns=COLUMN_ORDER)


def progress_chart(table: pandas.DataFrame) -> altair.LayerChart:
    """One track per market from 0 to 100% of its goal, filled to Leads Available % and coloured
    by how far the market still is from its goal; furthest first."""
    chartable = table.assign(
        filled=lambda frame: frame["Leads Available %"].clip(upper=100),
        band=lambda frame: frame["Leads Available %"].map(distance_band),
        label=lambda frame: (
            frame["Leads Available"].astype(str) + " / " + frame["Lead Goal"].astype(str)
        ),
        goal=100,
    )
    market_axis = altair.Y("Market:N", sort=None, title=None)
    track = (
        altair.Chart(chartable)
        .mark_bar(color="#E4E1DC", cornerRadius=4)
        .encode(x=altair.X("goal:Q", scale=altair.Scale(domain=[0, 100])), y=market_axis)
    )
    filled = (
        altair.Chart(chartable)
        .mark_bar(cornerRadius=4)
        .encode(
            x=altair.X(
                "filled:Q", title="Leads Available % of goal", scale=altair.Scale(domain=[0, 100])
            ),
            y=market_axis,
            color=altair.Color(
                "band:N",
                title=None,
                # Legend reads far → met, the order a reader scans for problems.
                scale=altair.Scale(
                    domain=[label for _, label, _ in reversed(DISTANCE_BANDS)],
                    range=[colour for _, _, colour in reversed(DISTANCE_BANDS)],
                ),
                legend=altair.Legend(orient="top"),
            ),
            tooltip=["Market", "Lead Goal", "Leads Available", "Leads Needed", "Leads Available %"],
        )
    )
    labels = (
        altair.Chart(chartable)
        .mark_text(align="left", dx=6, color="#111318")
        .encode(x="filled:Q", y=market_axis, text="label:N")
    )
    return (track + filled + labels).properties(height=altair.Step(30))


def render_demand(connection: psycopg.Connection) -> None:
    markets = read_market_demand(connection)
    if not markets:
        streamlit.info("No markets yet. Load the demo data with `leadgen db seed-demo`.")
        return
    below_goal = [market for market in markets if market.status == STATUS_BELOW_GOAL]
    summary_columns = streamlit.columns(3)
    summary_columns[0].metric("Markets below goal", f"{len(below_goal)} of {len(markets)}")
    if below_goal:
        furthest = below_goal[0]
        summary_columns[1].metric(
            "Furthest from goal",
            f"{furthest.market_name}, {furthest.state} · {furthest.leads_available_pct}%",
        )
    summary_columns[2].metric("Leads needed", sum(market.leads_needed for market in markets))
    table = demand_table(markets)
    streamlit.altair_chart(progress_chart(table), use_container_width=True)
    streamlit.dataframe(
        table,
        hide_index=True,
        column_config={
            "Leads Available %": streamlit.column_config.ProgressColumn(
                "Leads Available %", format="%d%%", min_value=0, max_value=100
            )
        },
    )
    streamlit.caption(
        "Leads Available % = Leads Available ÷ Lead Goal. Leads Needed = Lead Goal − Leads "
        "Available: the new qualified leads the market still needs."
    )


def stage_result_text(result: dict | None) -> str:
    if not result:
        return ""
    return " · ".join(f"{key} {value}" for key, value in result.items())


def stage_track_html(detail) -> str:
    """The seven stages left to right, each gray until it finishes and blue once done."""
    cells = []
    for number, stage in enumerate(detail.stages, start=1):
        background, colour = STAGE_COLOURS[stage.status]
        progress = detail.jobs.get(stage.stage)
        text = stage_result_text(stage.result)
        if progress and stage.status != run_status.STAGE_DONE:
            text = " · ".join(f"{count} {status}" for status, count in sorted(progress.items()))
        cells.append(
            '<div style="flex:1;min-width:0">'
            f'<div style="background:{background};color:{colour};border-radius:8px;'
            'padding:10px 8px;text-align:center;font-weight:600">'
            f"{number}. {html.escape(stage.label)}"
            f'<div style="font-size:0.75rem;font-weight:400">'
            f"{STAGE_STATUS_WORDS[stage.status]}</div></div>"
            '<div style="font-size:0.75rem;color:#5F6368;padding:6px 2px;text-align:center">'
            f"{html.escape(text or '—')}</div></div>"
        )
    return '<div style="display:flex;gap:6px;align-items:flex-start">' + "".join(cells) + "</div>"


def render_run(connection: psycopg.Connection) -> None:
    runs = run_status.list_runs(connection)
    if not runs:
        streamlit.info(
            "No runs yet. Plan one with `leadgen plan`, then start it with `leadgen run`."
        )
        return
    labels = dict(runs)
    run_id = streamlit.selectbox(
        "Run", list(labels), format_func=labels.get, key="run_choice", help="Newest first"
    )
    detail = run_status.read_run_detail(connection, run_id)
    # The same one-line summary as the Run history tab, for this run only.
    summary = [row for row in run_status.read_run_history(connection) if row["run_id"] == run_id]
    if summary:
        streamlit.dataframe(history_table(summary), hide_index=True)

    if detail.needs_a_human:
        streamlit.warning(
            f"{len(detail.needs_reconciliation)} paid calls ended without a known outcome. "
            "Check the provider's usage records, reconcile each reservation, then resume with "
            f"`leadgen run --plan {detail.plan_id} --resume` (see docs/operations.md)."
        )

    streamlit.subheader("Stages")
    streamlit.markdown(stage_track_html(detail), unsafe_allow_html=True)

    streamlit.subheader("Spend by service")
    spend = pandas.DataFrame(
        [{"Service": key, "USD": float(value)} for key, value in detail.spend_by_service.items()]
    )
    if spend.empty:
        streamlit.caption("No paid calls yet.")
    else:
        streamlit.dataframe(spend, hide_index=True)


def history_table(rows: list[dict]) -> pandas.DataFrame:
    return pandas.DataFrame(
        [
            {
                "Started": row["started_at"].strftime("%Y-%m-%d %H:%M"),
                "Market": row["market"],
                "Status": row["status"],
                "Maps": row["maps_sourced"],
                "Exa": row["exa_sourced"],
                "New": row["new_leads"],
                "Qualified": row["qualified"],
                "Review": row["review"],
                "Rejected": row["rejected"],
                "In CRM": row["crm_inserted"],
                "Spend": usd(row["spend_usd"]),
                "Budget": usd(row["budget_usd"]),
                "Per qualified": usd(row["cost_per_qualified_usd"]),
                "Run": str(row["run_id"])[:8],
            }
            for row in rows
        ]
    )


def render_history(connection: psycopg.Connection) -> None:
    rows = run_status.read_run_history(connection)
    if not rows:
        streamlit.info("No runs yet.")
        return
    totals = streamlit.columns(4)
    totals[0].metric("Runs", len(rows))
    totals[1].metric("Qualified", sum(row["qualified"] for row in rows))
    totals[2].metric("Added to CRM", sum(row["crm_inserted"] for row in rows))
    totals[3].metric("Spend", usd(sum(row["spend_usd"] for row in rows)))
    streamlit.dataframe(history_table(rows), hide_index=True)
    chart_data = pandas.DataFrame(
        [
            {"Run": row["started_at"].strftime("%m-%d %H:%M"), "USD": float(row["spend_usd"])}
            for row in reversed(rows)
        ]
    )
    streamlit.altair_chart(
        altair.Chart(chart_data)
        .mark_bar(color="#1840ED", cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
        .encode(x=altair.X("Run:N", sort=None), y=altair.Y("USD:Q", title="Spend (USD)"))
    )


def table_or_note(rows: list[dict], note: str) -> None:
    """A table, or a plain sentence when there is nothing to show yet."""
    if rows:
        streamlit.dataframe(pandas.DataFrame(rows), hide_index=True)
    else:
        streamlit.caption(note)


def render_sources(connection: psycopg.Connection) -> None:
    no_runs = "Nothing yet: this fills in after the first run."
    streamlit.subheader("Yield by source")
    table_or_note(
        [
            {
                "Source": row["source"],
                "Sourced": row["sourced"],
                "New": row["new_leads"],
                "Judged": row["judged"],
                "Qualified": row["qualified"],
                "Qualified ÷ judged": percent(row["qualified_rate"]),
            }
            for row in run_status.read_source_yield(connection)
        ],
        no_runs,
    )
    streamlit.subheader("Exa leads by query consensus")
    streamlit.caption("How many Exa query phrasings found the business.")
    table_or_note(
        [
            {
                "Found by": row["query_consensus"],
                "Leads": row["leads"],
                "Judged": row["judged"],
                "Qualified": row["qualified"],
                "Qualified ÷ judged": percent(row["qualified_rate"]),
            }
            for row in run_status.read_consensus_yield(connection)
        ],
        no_runs,
    )
    streamlit.subheader("Spend by service, all runs")
    streamlit.caption(
        "Estimated amounts are conservative ceilings where the provider reported none."
    )
    table_or_note(
        [
            {
                "Service": row["service"],
                "Calls": row["calls"],
                "Reported": usd(row["reported_usd"]),
                "Estimated": usd(row["estimated_usd"]),
                "Total": usd(row["total_usd"]),
            }
            for row in run_status.read_spend_by_service(connection)
        ],
        no_runs,
    )


def audit_summary(action: str, target: str | None) -> str:
    """One plain sentence for what the actor did: the action, plus what it acted on when the
    action does not already name it."""
    sentence = action[:1].upper() + action[1:] if action else ""
    if target and target.lower() not in sentence.lower():
        sentence = f"{sentence} — {target}"
    return sentence


def audit_table(events: list[dict]) -> pandas.DataFrame:
    started = events[0]["recorded_at"] if events else None
    return pandas.DataFrame(
        [
            {
                "#": index,
                "Time": event["recorded_at"].strftime("%H:%M:%S.%f")[:-3],
                "+s": round((event["recorded_at"] - started).total_seconds(), 1),
                "Who (X)": event["actor"],
                "Summary": audit_summary(event["action"], event["target"]),
                # Everything recorded, including what the action was applied to.
                "Details": json.dumps({"target": event["target"], **(event["detail"] or {})}),
            }
            for index, event in enumerate(events, start=1)
        ]
    )


def render_audit(connection: psycopg.Connection) -> None:
    plans = run_status.audited_plans(connection)
    if not plans:
        streamlit.info(
            "No audit trail yet. Plan and run with `--audit`, e.g. "
            "`leadgen plan --audit ...` then `leadgen run --plan latest --audit`. "
            "Every step is then recorded here as who did it and what happened."
        )
        return
    labels = dict(plans)
    plan_id = streamlit.selectbox(
        "Plan", list(labels), format_func=labels.get, key="audit_plan", help="Newest first"
    )
    events = run_status.read_audit_events(connection, plan_id)
    table = audit_table(events)
    controls = streamlit.columns([2, 2, 1])
    # The 19 audit actors in pipeline order, whether or not this plan's trail has them yet.
    actors = controls[0].multiselect("Who (X)", list(ACTORS), key="audit_actors")
    search = controls[1].text_input("Search summaries and details", key="audit_search")
    newest_first = controls[2].toggle("Newest first", value=True, key="audit_newest")
    shown = table
    if actors:
        shown = shown[shown["Who (X)"].isin(actors)]
    if search:
        needle = search.lower()
        shown = shown[
            shown.apply(lambda row: needle in " ".join(map(str, row.values)).lower(), axis=1)
        ]
    if newest_first:
        shown = shown.iloc[::-1]
    streamlit.caption(
        f"{len(shown)} of {len(table)} events. Each row: who (X) did it, a plain-language "
        "summary, and the recorded details. Refreshes every 5 seconds."
    )
    streamlit.dataframe(
        shown,
        hide_index=True,
        column_config={
            "Summary": streamlit.column_config.TextColumn(width="large"),
            "Details": streamlit.column_config.TextColumn(width="medium"),
        },
    )
    streamlit.download_button(
        "Download as CSV",
        table.to_csv(index=False),
        file_name=f"audit_plan_{plan_id}.csv",
        mime="text/csv",
        key="audit_download",
    )


def example_frame(rows: list[dict]) -> pandas.DataFrame:
    return pandas.DataFrame(
        [
            {
                key: json.dumps(value) if isinstance(value, dict | list) else value
                for key, value in row.items()
            }
            for row in rows
        ]
    )


def render_data_model(connection: psycopg.Connection) -> None:
    streamlit.caption(
        "Every table and view in the database, what it holds and why. The example rows are made "
        "up to show the shape of the data and use a selection of each table's columns; the row "
        "count is the live number in your database."
    )
    for group, guides in data_model.GROUPS:
        streamlit.subheader(group)
        for guide in guides:
            count = connection.execute(f'SELECT count(*) FROM "{guide.name}"').fetchone()[0]
            kind = " (view)" if guide.kind == "view" else ""
            streamlit.markdown(f"**`{guide.name}`**{kind} · {count:,} rows now")
            streamlit.caption(guide.summary)
            streamlit.dataframe(example_frame(guide.examples), hide_index=True)


@streamlit.fragment(run_every="5s")
def live_audit() -> None:
    try:
        with connect() as connection:
            render_audit(connection)
    except psycopg.Error as error:
        streamlit.error(f"Cannot read the audit trail from the database: {error}")


@streamlit.fragment(run_every="10s")
def live_run() -> None:
    """The Current run tab redraws itself every 10 seconds while the page is open."""
    try:
        with connect() as connection:
            render_run(connection)
    except psycopg.Error as error:
        streamlit.error(f"Cannot read the run from the database: {error}")


HEADER = """
<link rel="stylesheet"
      href="https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,400&display=swap">
<div style="display:flex;align-items:baseline;justify-content:space-between;
            border-bottom:1px solid #E4E1DC;padding-bottom:0.75rem;margin-bottom:0.5rem">
  <div>
    <div style="font-size:0.75rem;letter-spacing:0.08em;text-transform:uppercase;color:#1840ED;
                font-weight:600">Lead-gen agent</div>
    <h1 style="margin:0;padding:0;font-weight:400;
               font-family:Newsreader,Georgia,serif">{title}</h1>
  </div>
  <div style="font-size:0.8rem;color:#5B5F68">Search and evidence powered by Exa</div>
</div>
"""


def render() -> None:
    streamlit.set_page_config(page_title="Lead-gen dashboard", layout="wide")
    try:
        title = load_category_config().label
    except Exception:
        title = "Lead-gen dashboard"
    streamlit.html(HEADER.format(title=title))
    (
        demand_tab,
        run_tab,
        audit_tab,
        history_tab,
        sources_tab,
        evals_tab,
        data_model_tab,
    ) = streamlit.tabs(
        [
            "Demand gaps",
            "Current run",
            "Audit log",
            "Run history",
            "Sources and spend",
            "Eval review",
            "Data model",
        ]
    )
    try:
        with connect() as connection:
            with demand_tab:
                render_demand(connection)
            with history_tab:
                render_history(connection)
            with sources_tab:
                render_sources(connection)
            with data_model_tab:
                render_data_model(connection)
    except ValidationError:
        streamlit.error(DATABASE_URL_HELP)
        return
    except psycopg.Error as error:
        streamlit.error(f"Cannot read from the database: {error}")
        return
    with run_tab:
        live_run()
    with audit_tab:
        live_audit()
    with evals_tab:
        render_eval_review()


render()
