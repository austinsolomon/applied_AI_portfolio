"""Demand per market: available leads against the market's lead goal."""

from dataclasses import dataclass

import psycopg
from psycopg.rows import class_row

STATUS_BELOW_GOAL = "Below goal"
STATUS_GOAL_MET = "Goal met"


@dataclass(frozen=True)
class MarketDemand:
    """One market's row of `gap_view`."""

    market_id: int
    market_name: str
    state: str
    lead_goal: int
    available_leads: int
    leads_needed: int
    leads_available_pct: int
    goal_met: bool

    @property
    def status(self) -> str:
        return STATUS_GOAL_MET if self.goal_met else STATUS_BELOW_GOAL


def read_market_demand(connection: psycopg.Connection) -> list[MarketDemand]:
    """Every market, furthest from its goal first; markets that met it come last.

    Reads `gap_view`, the view behind both the dashboard and the agent's gap reading.
    """
    with connection.cursor(row_factory=class_row(MarketDemand)) as cursor:
        return cursor.execute(
            """
            SELECT market_id, market_name, state, lead_goal, available_leads, leads_needed,
                   leads_available_pct, goal_met
            FROM gap_view
            ORDER BY goal_met, leads_available_pct, leads_needed DESC, market_name
            """
        ).fetchall()


def read_gaps(connection: psycopg.Connection) -> list[MarketDemand]:
    """Markets below their lead goal, furthest from it first."""
    return [market for market in read_market_demand(connection) if not market.goal_met]
