"""A hand-written agent loop on the Claude Messages API.

The Anthropic SDK ships a tool runner and the Claude Agent SDK runs a full agent loop. This module
writes the loop by hand once, so every step is visible:

1. Send the question, the tool definitions and the JSON schema the final answer must follow.
2. If the model stops with `tool_use`, run each requested tool and send every result back in one
   user message, each matched to its request by `tool_use_id`.
3. Repeat until the model stops with `end_turn`, then validate its JSON answer.

Guards: a turn cap, a dollar cap checked before every call, and a cost-ledger row after every call.
"""

import json
import uuid
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any, Protocol

import psycopg
from anthropic.types import Message, ToolUseBlock
from pydantic import BaseModel, ConfigDict, ValidationError

from leadgen.claude_pricing import TokenUsage, call_cost_usd, price_for
from leadgen.gaps import read_gaps

# Characters per token for the pre-call cost ceiling. Real text averages about four; three keeps
# the estimate on the high side, so the dollar cap errs toward stopping early.
CHARACTERS_PER_TOKEN_ESTIMATE = 3

SYSTEM_PROMPT = """\
You help a lead-generation team decide where to source new leads for mobile and in-home \
massage providers. Each market has a goal for qualified leads. Leads Available % is available \
leads divided by the leads still needed; below 100% the market needs new leads, and short_by is \
how many.

Call read_gaps to get the current numbers before answering. Use only numbers returned by the \
tool. Recommend one market and explain the choice in one or two sentences."""

READ_GAPS_TOOL: dict[str, Any] = {
    "name": "read_gaps",
    "description": (
        "Markets whose available leads cover less than the leads they still need, biggest gap "
        "first. Each market has: market, leads_available_pct, still_needed, available, short_by."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
}

RECOMMENDATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "market": {"type": "string"},
        "leads_available_pct": {"type": "integer"},
        "short_by": {"type": "integer"},
        "reason": {"type": "string"},
    },
    "required": ["market", "leads_available_pct", "short_by", "reason"],
    "additionalProperties": False,
}


class Recommendation(BaseModel):
    """The model's final answer, validated against the same schema it was constrained to."""

    model_config = ConfigDict(extra="forbid")

    market: str
    leads_available_pct: int
    short_by: int
    reason: str


class MessagesApi(Protocol):
    """The one method this loop needs: `client.messages.create`. Tests pass a replay stand-in."""

    def create(self, **request: Any) -> Message: ...


class AgentLoopError(Exception):
    """The loop stopped without a valid answer."""


class BudgetExceeded(AgentLoopError):
    pass


class TurnLimitReached(AgentLoopError):
    pass


class ModelRefused(AgentLoopError):
    pass


class AnswerInvalid(AgentLoopError):
    pass


@dataclass(frozen=True)
class CallRecord:
    call_number: int
    stop_reason: str | None
    usage: TokenUsage
    cost_usd: Decimal
    request_id: str | None


@dataclass
class AskResult:
    run_id: uuid.UUID
    model: str
    recommendation: Recommendation | None = None
    calls: list[CallRecord] = field(default_factory=list)

    @property
    def total_cost_usd(self) -> Decimal:
        return sum((call.cost_usd for call in self.calls), Decimal(0))


def ask_about_gaps(
    question: str,
    *,
    connection: psycopg.Connection,
    messages_api: MessagesApi,
    model: str,
    max_usd: Decimal,
    max_turns: int = 5,
    max_output_tokens: int = 4096,
    effort: str = "low",
) -> AskResult:
    """Run the loop until the model answers, or a guard stops it. Every call is ledgered."""
    result = AskResult(run_id=uuid.uuid4(), model=model)
    conversation: list[dict[str, Any]] = [{"role": "user", "content": question}]

    for call_number in range(1, max_turns + 1):
        request = {
            "model": model,
            "max_tokens": max_output_tokens,
            "system": SYSTEM_PROMPT,
            "tools": [READ_GAPS_TOOL],
            "messages": conversation,
            "output_config": {
                "effort": effort,
                "format": {"type": "json_schema", "schema": RECOMMENDATION_SCHEMA},
            },
        }
        ceiling_usd = result.total_cost_usd + estimated_call_ceiling_usd(request)
        if ceiling_usd > max_usd:
            raise BudgetExceeded(
                f"call {call_number} could bring this run to ${ceiling_usd:.4f}, "
                f"over the ${max_usd} cap; stopped before calling"
            )

        response = messages_api.create(**request)
        record = _record_call(connection, result, call_number, response)
        result.calls.append(record)

        if response.stop_reason == "tool_use":
            conversation.append({"role": "assistant", "content": response.content})
            conversation.append(
                {
                    "role": "user",
                    "content": [
                        _run_tool(connection, block)
                        for block in response.content
                        if isinstance(block, ToolUseBlock)
                    ],
                }
            )
            continue

        if response.stop_reason == "end_turn":
            answer_text = "".join(block.text for block in response.content if block.type == "text")
            try:
                result.recommendation = Recommendation.model_validate_json(answer_text)
            except ValidationError as error:
                raise AnswerInvalid(
                    f"the final answer did not match the schema: {error}"
                ) from error
            return result

        if response.stop_reason == "refusal":
            category = response.stop_details.category if response.stop_details else None
            raise ModelRefused(f"the model declined to answer (category: {category})")

        raise AgentLoopError(f"the model stopped with {response.stop_reason!r} before answering")

    raise TurnLimitReached(f"no final answer after {max_turns} model calls")


def estimated_call_ceiling_usd(request: dict[str, Any]) -> Decimal:
    """Worst-case cost of the next call: the whole request as uncached input, plus full output."""
    price = price_for(request["model"])
    request_characters = len(json.dumps(request, default=_jsonable))
    estimated_input_tokens = request_characters // CHARACTERS_PER_TOKEN_ESTIMATE
    return (
        price.input_per_million * estimated_input_tokens
        + price.output_per_million * request["max_tokens"]
    ) / Decimal(1_000_000)


def _jsonable(value: Any) -> Any:
    return value.model_dump() if hasattr(value, "model_dump") else str(value)


def _run_tool(connection: psycopg.Connection, block: ToolUseBlock) -> dict[str, Any]:
    """Run one requested tool. Failures go back to the model as an error result, not a crash."""
    if block.name != READ_GAPS_TOOL["name"]:
        return _tool_result(block.id, f"Unknown tool {block.name!r}.", is_error=True)
    try:
        gaps = [
            {
                "market": f"{market.market_name}, {market.state}",
                "leads_available_pct": market.leads_available_pct,
                "still_needed": market.remaining_goal,
                "available": market.available_leads,
                "short_by": market.short_by_leads,
            }
            for market in read_gaps(connection)
        ]
    except psycopg.Error as error:
        return _tool_result(block.id, f"Reading gaps failed: {error}", is_error=True)
    return _tool_result(block.id, json.dumps(gaps))


def _tool_result(tool_use_id: str, content: str, *, is_error: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
    if is_error:
        result["is_error"] = True
    return result


def _record_call(
    connection: psycopg.Connection, result: AskResult, call_number: int, response: Message
) -> CallRecord:
    usage = TokenUsage(
        input_tokens=response.usage.input_tokens,
        cache_creation_input_tokens=response.usage.cache_creation_input_tokens or 0,
        cache_read_input_tokens=response.usage.cache_read_input_tokens or 0,
        output_tokens=response.usage.output_tokens,
    )
    cost_usd = call_cost_usd(result.model, usage)
    request_id = getattr(response, "_request_id", None)
    connection.execute(
        """
        INSERT INTO cost_ledger (run_id, service, model, call_number, input_tokens,
            cache_creation_input_tokens, cache_read_input_tokens, output_tokens, cost_usd,
            request_id)
        VALUES (%(run_id)s, 'claude', %(model)s, %(call_number)s, %(input_tokens)s,
            %(cache_creation_input_tokens)s, %(cache_read_input_tokens)s, %(output_tokens)s,
            %(cost_usd)s, %(request_id)s)
        """,
        {
            "run_id": result.run_id,
            "model": result.model,
            "call_number": call_number,
            **asdict(usage),
            "cost_usd": cost_usd,
            "request_id": request_id,
        },
    )
    return CallRecord(call_number, response.stop_reason, usage, cost_usd, request_id)


def total_spend_usd(connection: psycopg.Connection) -> Decimal:
    """Everything this database's ledger has recorded, across all runs."""
    row = connection.execute("SELECT coalesce(sum(cost_usd), 0) FROM cost_ledger").fetchone()
    return Decimal(row[0]) if row else Decimal(0)
