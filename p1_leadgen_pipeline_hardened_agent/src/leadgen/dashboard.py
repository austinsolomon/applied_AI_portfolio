"""Lead-gen dashboard: demand gaps, the current run's stages, run history, sources and spend, and
eval review, one tab each.

Run with `leadgen dashboard`. Every tab reads through the same functions the agent and the
runner use (`gaps.py`, `run_status.py`, `eval_review.py`), so the page and the pipeline always see
the same numbers. Only the Eval review tab writes, and only labels.
"""

import altair
import pandas
import psycopg
import streamlit
from pydantic import ValidationError

from leadgen import run_status
from leadgen.database import connect
from leadgen.eval_review_view import render_eval_review
from leadgen.gaps import STATUS_BELOW_GOAL, MarketDemand, read_market_demand

# Answer first, then how the number is made, in calculation order, then the actionable shortfall.
COLUMN_ORDER = [
    "Market",
    "Leads Available %",
    "Status",
    "Lead goal",
    "Assigned",
    "Still needed",
    "Available",
    "Short by",
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


def demand_table(markets: list[MarketDemand]) -> pandas.DataFrame:
    rows = [
        {
            "Market": f"{market.market_name}, {market.state}",
            "Leads Available %": (
                "—" if market.leads_available_pct is None else f"{market.leads_available_pct}%"
            ),
            "Status": market.status,
            "Lead goal": market.lead_goal,
            "Assigned": market.assigned_leads,
            "Still needed": market.remaining_goal,
            "Available": market.available_leads,
            "Short by": market.short_by_leads,
        }
        for market in markets
    ]
    return pandas.DataFrame(rows, columns=COLUMN_ORDER)


def coverage_chart(table: pandas.DataFrame) -> altair.LayerChart:
    """Bars for Leads Available %, with a rule at 100% where a market is covered."""
    chartable = table[table["Leads Available %"] != "—"].assign(
        coverage_pct=lambda frame: frame["Leads Available %"].str.rstrip("%").astype(int)
    )
    bars = (
        altair.Chart(chartable)
        .mark_bar()
        .encode(
            x=altair.X("coverage_pct:Q", title="Leads Available %"),
            y=altair.Y("Market:N", sort=None, title=None),
            tooltip=["Market", "Leads Available %", "Still needed", "Available", "Short by"],
        )
    )
    full_coverage = (
        altair.Chart(pandas.DataFrame({"covered": [100]}))
        .mark_rule(strokeDash=[4, 4])
        .encode(x="covered:Q")
    )
    return bars + full_coverage


def render_demand(connection: psycopg.Connection) -> None:
    streamlit.caption(
        "Leads Available % = available leads ÷ (lead goal − assigned). Below 100%, the market "
        "needs new leads; Short by is how many."
    )
    markets = read_market_demand(connection)
    if not markets:
        streamlit.info("No markets yet. Load the demo data with `leadgen db seed-demo`.")
        return
    below_goal = [market for market in markets if market.status == STATUS_BELOW_GOAL]
    summary_columns = streamlit.columns(3)
    summary_columns[0].metric("Markets below goal", f"{len(below_goal)} of {len(markets)}")
    if below_goal:
        biggest_gap = below_goal[0]
        summary_columns[1].metric(
            "Biggest gap",
            f"{biggest_gap.market_name}, {biggest_gap.state} · {biggest_gap.leads_available_pct}%",
        )
    summary_columns[2].metric("New leads to find", sum(market.short_by_leads for market in markets))
    table = demand_table(markets)
    streamlit.dataframe(table, hide_index=True)
    streamlit.altair_chart(coverage_chart(table))
    streamlit.caption("Markets that have met their goal have no percentage and are not charted.")


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
    demand_tab, run_tab, history_tab, sources_tab, evals_tab = streamlit.tabs(
        ["Demand gaps", "Current run", "Run history", "Sources and spend", "Eval review"]
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
    with evals_tab:
        render_eval_review()


render()
