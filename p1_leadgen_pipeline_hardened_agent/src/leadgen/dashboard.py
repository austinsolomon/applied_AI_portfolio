"""Demand dashboard: Leads Available % per market, biggest gap first.

Run with `leadgen dashboard`. Read-only: it only queries `gap_view`, through the same function the
agent uses, so the page and the agent always see the same numbers.
"""

import altair
import pandas
import psycopg
import streamlit
from pydantic import ValidationError

from leadgen.database import connect
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


def render() -> None:
    streamlit.set_page_config(page_title="Lead demand", layout="wide")
    streamlit.title("Lead demand by market")
    streamlit.caption(
        "Leads Available % = available leads ÷ (lead goal − assigned). Below 100%, the market "
        "needs new leads; Short by is how many."
    )

    try:
        with connect() as connection:
            markets = read_market_demand(connection)
    except ValidationError:
        streamlit.error(
            "DATABASE_URL is missing or malformed. Copy .env.example to .env, or export "
            "DATABASE_URL before starting the dashboard."
        )
        return
    except psycopg.Error as error:
        streamlit.error(f"Cannot read demand from the database: {error}")
        return

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


render()
