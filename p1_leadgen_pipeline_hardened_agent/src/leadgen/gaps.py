"""Demand per market: how far each market is from its lead goal."""

from dataclasses import dataclass

import psycopg
from psycopg.rows import class_row

STATUS_BELOW_GOAL = "Below goal"
STATUS_COVERED = "Covered"
STATUS_GOAL_MET = "Goal met"


@dataclass(frozen=True)
class MarketDemand:
    """One market's row of `gap_view`."""

    market_id: int
    market_name: str
    state: str
    lead_goal: int
    assigned_leads: int
    available_leads: int
    remaining_goal: int
    leads_available_pct: int | None
    goal_met: bool
    short_by_leads: int

    @property
    def status(self) -> str:
        if self.goal_met:
            return STATUS_GOAL_MET
        if self.leads_available_pct is not None and self.leads_available_pct >= 100:
            return STATUS_COVERED
        return STATUS_BELOW_GOAL


def read_market_demand(connection: psycopg.Connection) -> list[MarketDemand]:
    """Every market: below goal first (biggest gap first), then covered, then goal met.

    Reads `gap_view`, the view behind both the demand dashboard and the agent's gap reading.
    """
    with connection.cursor(row_factory=class_row(MarketDemand)) as cursor:
        return cursor.execute(
            """
            SELECT market_id, market_name, state, lead_goal, assigned_leads, available_leads,
                   remaining_goal, leads_available_pct, goal_met, short_by_leads
            FROM gap_view
            ORDER BY goal_met, leads_available_pct >= 100, leads_available_pct,
                     remaining_goal DESC, market_name
            """
        ).fetchall()


def read_gaps(connection: psycopg.Connection) -> list[MarketDemand]:
    """Markets whose available leads cover less than their remaining goal, biggest gap first."""
    return [
        market for market in read_market_demand(connection) if market.status == STATUS_BELOW_GOAL
    ]
