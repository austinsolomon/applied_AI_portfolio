"""Lead-gen dashboard: demand gaps, the current run's stages, run history, sources and spend, and
eval review, one tab each.

Run with `leadgen dashboard`. Every tab reads through the same functions the agent and the
runner use (`gaps.py`, `run_status.py`, `eval_review.py`), so the page and the pipeline always see
the same numbers. Only the Eval review tab writes, and only labels.
"""

import json

import altair
import pandas
import psycopg
import streamlit
from pydantic import ValidationError

from leadgen import run_status
from leadgen.database import connect
from leadgen.eval_review_view import render_eval_review
from leadgen.gaps import STATUS_BELOW_GOAL, MarketDemand, read_market_demand

# Market, then the goal, what is in hand, what is missing, and the share of the goal in hand.
COLUMN_ORDER = ["Market", "Lead Goal", "Leads Available", "Leads Needed", "Leads Available %"]

# How far a market is from its goal, for the progress chart: the band and its colour.
DISTANCE_BANDS = [
    (100, "Goal met", "#2e7d5b"),
    (75, "Close (75–99%)", "#7cb342"),
    (40, "Halfway (40–74%)", "#f2a541"),
    (0, "Far (under 40%)", "#d1495b"),
]

STAGE_BADGES = {
    run_status.STAGE_DONE: ":green-badge[:material/check: done]",
    run_status.STAGE_RUNNING: ":blue-badge[:material/progress_activity: running]",
    run_status.STAGE_FAILED: ":red-badge[:material/error: failed]",
    run_status.STAGE_BLOCKED: ":orange-badge[:material/pan_tool: needs a human]",
    run_status.STAGE_WAITING: ":gray-badge[waiting]",
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
        .mark_bar(color="#8a8f98", opacity=0.18, cornerRadius=3)
        .encode(x=altair.X("goal:Q", scale=altair.Scale(domain=[0, 100])), y=market_axis)
    )
    filled = (
        altair.Chart(chartable)
        .mark_bar(cornerRadius=3)
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
        .mark_text(align="left", dx=6)
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
    summary = streamlit.columns(4)
    summary[0].metric("Market", detail.market)
    summary[1].metric("Status", detail.status)
    budget = detail.budget_usd
    summary[2].metric(
        "Spent", usd(detail.spend_usd), help="Ledger total, all services, all attempts"
    )
    summary[3].metric("Budget", usd(budget), help="Approved for this plan")
    if budget:
        streamlit.progress(
            min(float(detail.spend_usd / budget), 1.0),
            text=f"{percent(detail.spend_usd / budget)} of budget spent, "
            f"{usd(detail.held_usd)} held by unresolved calls",
        )

    if detail.needs_a_human:
        streamlit.warning(
            f"{len(detail.needs_reconciliation)} paid calls ended without a known outcome. "
            "Check the provider's usage records, reconcile each reservation, then resume with "
            f"`leadgen run --plan {detail.plan_id} --resume` (see docs/operations.md)."
        )

    streamlit.subheader("Stages")
    for stage in detail.stages:
        row = streamlit.columns([2, 2, 5])
        row[0].markdown(f"**{stage.label}**")
        row[1].markdown(STAGE_BADGES[stage.status])
        progress = detail.jobs.get(stage.stage)
        text = stage_result_text(stage.result)
        if progress and stage.status != run_status.STAGE_DONE:
            text = " · ".join(f"{count} {status}" for status, count in sorted(progress.items()))
        row[2].markdown(text or "—")

    columns = streamlit.columns(2)
    with columns[0]:
        streamlit.subheader("Spend by service")
        spend = pandas.DataFrame(
            [
                {"Service": key, "USD": float(value)}
                for key, value in detail.spend_by_service.items()
            ]
        )
        if spend.empty:
            streamlit.caption("No paid calls yet.")
        else:
            streamlit.dataframe(spend, hide_index=True)
    with columns[1]:
        streamlit.subheader("Recent events")
        events = pandas.DataFrame(
            [
                {
                    "Time": event["created_at"].strftime("%H:%M:%S"),
                    "Step": event["operation"],
                    "Outcome": event["outcome"],
                }
                for event in detail.recent_events
            ]
        )
        if events.empty:
            streamlit.caption("No events recorded.")
        else:
            streamlit.dataframe(events, hide_index=True)


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
        .mark_bar()
        .encode(x=altair.X("Run:N", sort=None), y=altair.Y("USD:Q", title="Spend (USD)"))
    )


def render_sources(connection: psycopg.Connection) -> None:
    streamlit.subheader("Yield by source")
    streamlit.dataframe(
        pandas.DataFrame(
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
            ]
        ),
        hide_index=True,
    )
    streamlit.subheader("Exa leads by query consensus")
    streamlit.caption("How many Exa query phrasings found the business.")
    streamlit.dataframe(
        pandas.DataFrame(
            [
                {
                    "Found by": row["query_consensus"],
                    "Leads": row["leads"],
                    "Judged": row["judged"],
                    "Qualified": row["qualified"],
                    "Qualified ÷ judged": percent(row["qualified_rate"]),
                }
                for row in run_status.read_consensus_yield(connection)
            ]
        ),
        hide_index=True,
    )
    streamlit.subheader("Spend by service, all runs")
    streamlit.caption(
        "Estimated amounts are conservative ceilings where the provider reported none."
    )
    streamlit.dataframe(
        pandas.DataFrame(
            [
                {
                    "Service": row["service"],
                    "Calls": row["calls"],
                    "Reported": usd(row["reported_usd"]),
                    "Estimated": usd(row["estimated_usd"]),
                    "Total": usd(row["total_usd"]),
                }
                for row in run_status.read_spend_by_service(connection)
            ]
        ),
        hide_index=True,
    )


def audit_table(events: list[dict]) -> pandas.DataFrame:
    started = events[0]["recorded_at"] if events else None
    return pandas.DataFrame(
        [
            {
                "#": index,
                "Time": event["recorded_at"].strftime("%H:%M:%S.%f")[:-3],
                "+s": round((event["recorded_at"] - started).total_seconds(), 1),
                "Who (X)": event["actor"],
                "Does (Y)": event["action"],
                "To (Z)": event["target"],
                "Details": json.dumps(event["detail"]) if event["detail"] else "",
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
            "Every step is then recorded here as who → does → to whom."
        )
        return
    labels = dict(plans)
    plan_id = streamlit.selectbox(
        "Plan", list(labels), format_func=labels.get, key="audit_plan", help="Newest first"
    )
    events = run_status.read_audit_events(connection, plan_id)
    table = audit_table(events)
    controls = streamlit.columns([2, 2, 1])
    actors = controls[0].multiselect(
        "Who (X)", sorted(table["Who (X)"].unique()), key="audit_actors"
    )
    search = controls[1].text_input("Search actions and details", key="audit_search")
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
        f"{len(shown)} of {len(table)} events. Each row reads: Who (X) → Does (Y) → To (Z). "
        "Refreshes every 5 seconds."
    )
    streamlit.dataframe(
        shown,
        hide_index=True,
        column_config={
            "Does (Y)": streamlit.column_config.TextColumn(width="large"),
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


def render() -> None:
    streamlit.set_page_config(page_title="Lead-gen dashboard", layout="wide")
    streamlit.title("Lead-gen dashboard")
    demand_tab, run_tab, audit_tab, history_tab, sources_tab, evals_tab = streamlit.tabs(
        [
            "Demand gaps",
            "Current run",
            "Audit log",
            "Run history",
            "Sources and spend",
            "Eval review",
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
