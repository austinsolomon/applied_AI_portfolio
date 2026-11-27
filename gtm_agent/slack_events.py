"""Slack front door for the harness: receive a message, run the agent, reply in the thread.

Slack's Events API sends each @mention or direct message as an HTTP POST. Slack wants an answer
within 3 seconds and retries if it doesn't get one, but an agent run takes much longer. So this
route checks the request, starts the run in the background, and returns 200 straight away. When
the run finishes, the answer is posted to the same thread with chat.postMessage.

The agent is the harness exactly as it runs from the CLI (index.options): same tools, same
prompt, and the same bash policy, where safe-list commands run and everything else is blocked,
because nobody in Slack can click Approve.

Needs: SLACK_SIGNING_SECRET and SLACK_BOT_TOKEN in the environment (from Secret Manager on Cloud
Run). Optional: ALLOWED_SLACK_USER_IDS, a comma-separated list of Slack user IDs allowed to use
the agent; empty means anyone in the workspace.
"""

import hashlib
import hmac
import json
import os
import re
import time

import httpx
from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse, Response

router = APIRouter()

# Slack signs requests with a timestamp; anything older than this is rejected so a captured
# request can't be replayed later.
MAX_REQUEST_AGE_SECONDS = 5 * 60

# Slack can deliver the same event more than once. Remember recent event IDs on this instance.
# (In memory, so a restart forgets them; the Postgres foundation replaces this later.)
_seen_event_ids: dict[str, float] = {}
_SEEN_TTL_SECONDS = 60 * 60

# A Slack message holds up to 40,000 characters; stay well under it.
MAX_REPLY_CHARS = 3_500

MENTION = re.compile(r"<@[A-Z0-9]+>\s*")


def verify_slack_signature(signing_secret: str, timestamp: str, body: bytes, signature: str, now: float | None = None) -> bool:
    """Check that a request really came from Slack.

    Slack computes HMAC-SHA256 over "v0:{timestamp}:{raw body}" with the app's signing secret and
    sends it as "v0=<hex>". Recompute it and compare in constant time, and refuse old timestamps.
    """
    if not signing_secret or not timestamp or not signature:
        return False
    try:
        request_time = int(timestamp)
    except ValueError:
        return False
    if abs((now or time.time()) - request_time) > MAX_REQUEST_AGE_SECONDS:
        return False
    base = b"v0:" + timestamp.encode() + b":" + body
    expected = "v0=" + hmac.new(signing_secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def is_duplicate(event_id: str, now: float | None = None) -> bool:
    """True if this event ID was seen recently; otherwise remember it and return False."""
    now = now or time.time()
    for old_id, seen_at in list(_seen_event_ids.items()):
        if now - seen_at > _SEEN_TTL_SECONDS:
            del _seen_event_ids[old_id]
    if event_id in _seen_event_ids:
        return True
    _seen_event_ids[event_id] = now
    return False


def allowed_user(user_id: str) -> bool:
    """Is this Slack user allowed to use the agent? Empty allow list means everyone."""
    allowed = {u.strip() for u in os.environ.get("ALLOWED_SLACK_USER_IDS", "").split(",") if u.strip()}
    return not allowed or user_id in allowed


def prompt_from_event(event: dict) -> str | None:
    """Turn a Slack event into the agent's prompt, or None if it should be ignored.

    Handles @mentions in channels and messages in the app's DM. Ignores bot messages (including
    our own replies, which would otherwise start a loop), edits, and other message subtypes.
    """
    if event.get("bot_id") or event.get("subtype"):
        return None
    if event.get("type") == "app_mention" or (event.get("type") == "message" and event.get("channel_type") == "im"):
        text = MENTION.sub("", event.get("text", "")).strip()
        return text or None
    return None


async def post_to_slack(channel: str, thread_ts: str, text: str) -> None:
    """Post a message in a thread with chat.postMessage."""
    if len(text) > MAX_REPLY_CHARS:
        text = text[:MAX_REPLY_CHARS] + "\n…(answer cut short)"
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {os.environ['SLACK_BOT_TOKEN']}"},
            json={"channel": channel, "thread_ts": thread_ts, "text": text},
        )
    body = response.json()
    if not body.get("ok"):
        print("Slack post failed:", body.get("error"), flush=True)


async def run_agent(prompt: str) -> str:
    """Run the harness on one prompt and return its final answer as text."""
    from claude_agent_sdk import AssistantMessage, ClaudeSDKClient, ResultMessage, TextBlock

    from index import options  # imported here so tests can run without starting the harness

    texts: list[str] = []
    final: str | None = None
    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                texts.extend(block.text for block in message.content if isinstance(block, TextBlock))
            elif isinstance(message, ResultMessage):
                final = message.result
                break
    return (final or "\n".join(texts)).strip() or "(no answer)"


async def handle_message(prompt: str, channel: str, thread_ts: str) -> None:
    """Background job: acknowledge in the thread, run the agent, post the answer."""
    await post_to_slack(channel, thread_ts, "Working on it…")
    try:
        answer = await run_agent(prompt)
    except Exception as error:  # report any failure back to the person who asked
        print("Agent error:", repr(error), flush=True)
        answer = f"Sorry, the run failed: {error}"
    await post_to_slack(channel, thread_ts, answer)


@router.post("/slack/events")
async def slack_events(request: Request, background: BackgroundTasks):
    """Receive one Slack event: verify it, start the run in the background, reply 200 at once."""
    body = await request.body()
    if not verify_slack_signature(
        os.environ.get("SLACK_SIGNING_SECRET", ""),
        request.headers.get("X-Slack-Request-Timestamp", ""),
        body,
        request.headers.get("X-Slack-Signature", ""),
    ):
        return Response("invalid signature", status_code=401)

    payload = json.loads(body)

    # Slack checks the URL once when you save it in the app settings.
    if payload.get("type") == "url_verification":
        return JSONResponse({"challenge": payload.get("challenge", "")})

    # A retry means Slack didn't see our first 200 in time; the first delivery is already running.
    if request.headers.get("X-Slack-Retry-Num"):
        return Response(status_code=200)

    if payload.get("type") != "event_callback":
        return Response(status_code=200)
    if is_duplicate(payload.get("event_id", "")):
        return Response(status_code=200)

    event = payload.get("event", {})
    prompt = prompt_from_event(event)
    if prompt is None:
        return Response(status_code=200)

    channel = event["channel"]
    thread_ts = event.get("thread_ts") or event["ts"]
    if not allowed_user(event.get("user", "")):
        background.add_task(post_to_slack, channel, thread_ts, "Sorry, you're not on this agent's allow list.")
        return Response(status_code=200)

    background.add_task(handle_message, prompt, channel, thread_ts)
    return Response(status_code=200)
