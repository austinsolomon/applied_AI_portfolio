"""Tests for the Slack front door, with signed fake Slack requests.

No real Slack, model, or agent: the agent run and the Slack post are replaced with fakes that
record what they were asked to do.
"""

import hashlib
import hmac
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import slack_events

SECRET = "test-signing-secret"


def signed_headers(body: bytes, timestamp: int | None = None, secret: str = SECRET) -> dict:
    ts = str(timestamp if timestamp is not None else int(time.time()))
    signature = "v0=" + hmac.new(secret.encode(), b"v0:" + ts.encode() + b":" + body, hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": signature, "Content-Type": "application/json"}


def mention(text: str = "<@U0BOT> what does this project do?", event_id: str = "Ev1", **extra) -> bytes:
    event = {"type": "app_mention", "user": "U123", "text": text, "channel": "C1", "ts": "111.222", **extra}
    return json.dumps({"type": "event_callback", "event_id": event_id, "event": event}).encode()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", SECRET)
    monkeypatch.delenv("ALLOWED_SLACK_USER_IDS", raising=False)
    slack_events._seen_event_ids.clear()

    calls = {"posts": [], "prompts": []}

    async def fake_post(channel, thread_ts, text):
        calls["posts"].append((channel, thread_ts, text))

    async def fake_run(prompt):
        calls["prompts"].append(prompt)
        return "It is a coding agent harness."

    monkeypatch.setattr(slack_events, "post_to_slack", fake_post)
    monkeypatch.setattr(slack_events, "run_agent", fake_run)

    app = FastAPI()
    app.include_router(slack_events.router)
    test_client = TestClient(app)
    test_client.calls = calls
    return test_client


def test_url_verification_returns_the_challenge(client):
    body = json.dumps({"type": "url_verification", "challenge": "abc123"}).encode()
    response = client.post("/slack/events", content=body, headers=signed_headers(body))
    assert response.status_code == 200
    assert response.json() == {"challenge": "abc123"}


def test_mention_runs_the_agent_and_replies_in_the_thread(client):
    body = mention()
    response = client.post("/slack/events", content=body, headers=signed_headers(body))
    assert response.status_code == 200
    assert client.calls["prompts"] == ["what does this project do?"]
    assert client.calls["posts"] == [
        ("C1", "111.222", "I'm on it...running"),
        ("C1", "111.222", "It is a coding agent harness."),
    ]


def test_reply_stays_in_an_existing_thread(client):
    body = mention(thread_ts="100.000")
    client.post("/slack/events", content=body, headers=signed_headers(body))
    assert {post[1] for post in client.calls["posts"]} == {"100.000"}


def test_direct_message_is_handled(client):
    event = {"type": "message", "channel_type": "im", "user": "U123", "text": "hello", "channel": "D1", "ts": "1.2"}
    body = json.dumps({"type": "event_callback", "event_id": "EvDM", "event": event}).encode()
    client.post("/slack/events", content=body, headers=signed_headers(body))
    assert client.calls["prompts"] == ["hello"]


def test_bad_signature_is_rejected(client):
    body = mention()
    response = client.post("/slack/events", content=body, headers=signed_headers(body, secret="wrong"))
    assert response.status_code == 401
    assert client.calls["prompts"] == []


def test_stale_timestamp_is_rejected(client):
    body = mention()
    response = client.post("/slack/events", content=body, headers=signed_headers(body, timestamp=int(time.time()) - 600))
    assert response.status_code == 401


def test_slack_retry_is_ignored(client):
    body = mention()
    headers = {**signed_headers(body), "X-Slack-Retry-Num": "1"}
    response = client.post("/slack/events", content=body, headers=headers)
    assert response.status_code == 200
    assert client.calls["prompts"] == []


def test_duplicate_event_runs_once(client):
    body = mention(event_id="EvSame")
    client.post("/slack/events", content=body, headers=signed_headers(body))
    client.post("/slack/events", content=body, headers=signed_headers(body))
    assert len(client.calls["prompts"]) == 1


def test_bot_messages_are_ignored(client):
    body = mention(bot_id="B999")
    client.post("/slack/events", content=body, headers=signed_headers(body))
    assert client.calls["prompts"] == []


def test_users_off_the_allow_list_get_a_refusal(client, monkeypatch):
    monkeypatch.setenv("ALLOWED_SLACK_USER_IDS", "U999")
    body = mention()
    client.post("/slack/events", content=body, headers=signed_headers(body))
    assert client.calls["prompts"] == []
    assert "allow list" in client.calls["posts"][0][2]


def test_agent_failure_is_reported_in_the_thread(client, monkeypatch):
    async def failing_run(prompt):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(slack_events, "run_agent", failing_run)
    body = mention()
    client.post("/slack/events", content=body, headers=signed_headers(body))
    assert "model unavailable" in client.calls["posts"][-1][2]
