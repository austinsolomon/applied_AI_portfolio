# Python equivalent of app/api/route.ts (reference only, never executed).
# A FastAPI endpoint. Events are sent as Server-Sent Events; the exact
# AI SDK UI stream format is simplified.

import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from lib.ref_agent import create_agent

agent = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Build the agent (sandbox + files) once at startup, like TS top-level await
    global agent
    agent = await create_agent()
    yield


app = FastAPI(lifespan=lifespan)


def sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


@app.post("/api")
async def post(request: Request):
    body = await request.json()
    messages = body["messages"]

    # Extract the last user message as the prompt
    last_message = messages[-1] if messages else {}
    prompt = "\n".join(
        part["text"]
        for part in last_message.get("parts", [])
        if part.get("type") == "text"
    ) or ""

    async def event_stream():
        try:
            async for event in agent.stream(prompt):
                yield sse(event)
        except Exception as error:
            # Unlike the TS version, this also catches errors that happen
            # mid-stream, so the user sees a message instead of a blank reply.
            print("Agent error:", error)
            yield sse({"type": "text-start", "id": "error"})
            yield sse({"type": "text-delta", "id": "error", "delta": "An error occurred. Please try again."})
            yield sse({"type": "text-end", "id": "error"})

    return StreamingResponse(event_stream(), media_type="text/event-stream")
