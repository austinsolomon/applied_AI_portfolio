"""Record one live `ask` run as a test fixture. Paid: it calls the Claude API.

Usage, from the project folder after `leadgen db seed-demo` (model, dollar cap, output file):
    uv run python scripts/record_ask_fixture.py claude-haiku-5-5 0.05 \
        tests/fixtures/raw_agent_loop/haiku.json

The fixture holds each request's conversation and the API's response, so tests can replay the
run without a network call. It contains no credentials.
"""

import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import anthropic
from anthropic.types import Message

from leadgen.database import connect
from leadgen.raw_agent_loop import ask_about_gaps
from leadgen.settings import get_settings

QUESTION = "Which market should we source first, and why?"


class RecordingMessagesApi:
    def __init__(self, messages_api: Any) -> None:
        self.messages_api = messages_api
        self.exchanges: list[dict[str, Any]] = []

    def create(self, **request: Any) -> Message:
        response = self.messages_api.create(**request)
        self.exchanges.append(
            {
                "request_messages": json.loads(json.dumps(request["messages"], default=_dump)),
                "response": response.to_dict(),
            }
        )
        return response


def _dump(value: Any) -> Any:
    return value.model_dump(exclude_none=True) if hasattr(value, "model_dump") else str(value)


def main(model: str, max_usd: str, fixture_path: str) -> None:
    settings = get_settings()
    if settings.anthropic_api_key is None:
        raise SystemExit("ANTHROPIC_API_KEY is not set")
    headers = (
        {"anthropic-workspace-id": settings.anthropic_workspace_id}
        if settings.anthropic_workspace_id
        else {}
    )
    client = anthropic.Anthropic(
        api_key=settings.anthropic_api_key.get_secret_value(), default_headers=headers
    )
    recorder = RecordingMessagesApi(client.messages)
    with connect() as connection:
        result = ask_about_gaps(
            QUESTION,
            connection=connection,
            messages_api=recorder,
            model=model,
            max_usd=Decimal(max_usd),
        )
    fixture = {"model": model, "question": QUESTION, "exchanges": recorder.exchanges}
    Path(fixture_path).write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf-8")
    print(f"recorded {len(recorder.exchanges)} calls, ${result.total_cost_usd:.5f}: {fixture_path}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
