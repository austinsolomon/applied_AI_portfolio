"""Demand gaps: which markets are furthest below their lead goal."""

from dataclasses import dataclass

import psycopg
from psycopg.rows import class_row


@dataclass(frozen=True)
class MarketGap:
    market_id: int
    market_name: str
    state: str
    lead_goal: int
    assigned_leads: int
    available_leads: int
    remaining_goal: int
    leads_available_pct: int


def read_gaps(connection: psycopg.Connection) -> list[MarketGap]:
    """Markets whose available leads cover less than their remaining goal, biggest gap first.

    Reads `gap_view`, the same view the demand dashboard renders.
    """
    with connection.cursor(row_factory=class_row(MarketGap)) as cursor:
        return cursor.execute(
            """
            SELECT market_id, market_name, state, lead_goal, assigned_leads, available_leads,
                   remaining_goal, leads_available_pct
            FROM gap_view
            WHERE NOT goal_met AND leads_available_pct < 100
            ORDER BY leads_available_pct, remaining_goal DESC, market_name
            """
        ).fetchall()
