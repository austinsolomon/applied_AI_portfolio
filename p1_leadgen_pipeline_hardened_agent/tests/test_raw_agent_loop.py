"""The hand-written agent loop, replayed from recorded Claude API runs: no network, no cost."""

import copy
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
from anthropic.types import Message

from leadgen.claude_pricing import TokenUsage, call_cost_usd
from leadgen.demo_seed import seed_demo_data
from leadgen.raw_agent_loop import (
    BudgetExceeded,
    ModelRefused,
    Recommendation,
    TurnLimitReached,
    ask_about_gaps,
    total_spend_usd,
)

FIXTURES = Path(__file__).parent / "fixtures" / "raw_agent_loop"


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class ReplayMessagesApi:
    """Stands in for `client.messages`: returns recorded responses in order and keeps requests."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def create(self, **request: Any) -> Message:
        self.requests.append(copy.deepcopy(request))
        if not self.responses:
            raise AssertionError("the loop made more calls than were recorded")
        return Message.model_validate(self.responses.pop(0))


@pytest.fixture
def demo_connection(migrated_connection: psycopg.Connection) -> psycopg.Connection:
    seed_demo_data(migrated_connection)
    return migrated_connection


def replay(
    connection: psycopg.Connection, fixture: dict[str, Any], **overrides: Any
) -> tuple[Any, ReplayMessagesApi]:
    messages_api = ReplayMessagesApi([exchange["response"] for exchange in fixture["exchanges"]])
    options = {"model": fixture["model"], "max_usd": Decimal("0.50"), **overrides}
    result = ask_about_gaps(
        fixture["question"], connection=connection, messages_api=messages_api, **options
    )
    return result, messages_api


@pytest.mark.parametrize("fixture_name", ["haiku", "opus"])
def test_loop_calls_the_tool_and_returns_a_validated_answer(
    demo_connection: psycopg.Connection, fixture_name: str
) -> None:
    result, _ = replay(demo_connection, load_fixture(fixture_name))

    assert [call.stop_reason for call in result.calls] == ["tool_use", "end_turn"]
    assert isinstance(result.recommendation, Recommendation)
    # Whichever market the model chose, its numbers must match the database's.
    expected_numbers = {
        "Phoenix, AZ": (10, 54),
        "Los Angeles, CA": (18, 66),
    }[result.recommendation.market]
    assert (result.recommendation.leads_available_pct, result.recommendation.short_by) == (
        expected_numbers
    )


def test_tool_result_goes_back_matched_to_its_request(demo_connection: psycopg.Connection) -> None:
    fixture = load_fixture("haiku")
    _, messages_api = replay(demo_connection, fixture)

    tool_use_id = next(
        block["id"]
        for block in fixture["exchanges"][0]["response"]["content"]
        if block["type"] == "tool_use"
    )
    second_request_turns = messages_api.requests[1]["messages"]
    tool_result = second_request_turns[-1]["content"][0]
    assert second_request_turns[-1]["role"] == "user"
    assert tool_result["tool_use_id"] == tool_use_id
    gaps = json.loads(tool_result["content"])
    assert gaps[0] == {
        "market": "Phoenix, AZ",
        "leads_available_pct": 10,
        "still_needed": 60,
        "available": 6,
        "short_by": 54,
    }


def test_every_call_is_costed_and_ledgered(demo_connection: psycopg.Connection) -> None:
    fixture = load_fixture("opus")
    result, _ = replay(demo_connection, fixture)

    expected_costs = []
    for exchange in fixture["exchanges"]:
        usage = exchange["response"]["usage"]
        expected_costs.append(
            call_cost_usd(
                fixture["model"],
                TokenUsage(
                    usage["input_tokens"],
                    usage.get("cache_creation_input_tokens") or 0,
                    usage.get("cache_read_input_tokens") or 0,
                    usage["output_tokens"],
                ),
            )
        )
    assert [call.cost_usd for call in result.calls] == expected_costs
    ledger_rows = demo_connection.execute(
        "SELECT call_number, cost_usd FROM cost_ledger WHERE run_id = %s ORDER BY call_number",
        (result.run_id,),
    ).fetchall()
    assert [(number, round(cost, 6)) for number, cost in ledger_rows] == [
        (call.call_number, round(call.cost_usd, 6)) for call in result.calls
    ]
    assert total_spend_usd(demo_connection) == round(result.total_cost_usd, 6)


def test_dollar_cap_stops_before_a_call_that_could_exceed_it(
    demo_connection: psycopg.Connection,
) -> None:
    fixture = load_fixture("opus")

    with pytest.raises(BudgetExceeded, match="stopped before calling"):
        replay(demo_connection, fixture, max_usd=Decimal("0.01"))

    assert total_spend_usd(demo_connection) == 0


def test_turn_cap_stops_a_model_that_keeps_calling_tools(
    demo_connection: psycopg.Connection,
) -> None:
    fixture = load_fixture("haiku")
    tool_use_response = fixture["exchanges"][0]["response"]
    fixture["exchanges"] = [{"response": tool_use_response} for _ in range(3)]

    with pytest.raises(TurnLimitReached, match="after 3 model calls"):
        replay(demo_connection, fixture, max_turns=3)


def test_a_refusal_is_reported_not_parsed(demo_connection: psycopg.Connection) -> None:
    fixture = load_fixture("haiku")
    refusal = copy.deepcopy(fixture["exchanges"][0]["response"])
    refusal["content"] = []
    refusal["stop_reason"] = "refusal"
    refusal["stop_details"] = {"type": "refusal", "category": None, "explanation": None}
    fixture["exchanges"] = [{"response": refusal}]

    with pytest.raises(ModelRefused):
        replay(demo_connection, fixture)


def test_an_unknown_tool_is_answered_with_an_error_result(
    demo_connection: psycopg.Connection,
) -> None:
    fixture = load_fixture("haiku")
    for block in fixture["exchanges"][0]["response"]["content"]:
        if block["type"] == "tool_use":
            block["name"] = "delete_everything"
    _, messages_api = replay(demo_connection, fixture)

    tool_result = messages_api.requests[1]["messages"][-1]["content"][0]
    assert tool_result["is_error"] is True
    assert "Unknown tool" in tool_result["content"]
