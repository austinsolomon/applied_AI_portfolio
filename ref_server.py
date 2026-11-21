"""Python equivalent of server.ts (reference only, never executed).

FastAPI web UI with Approve/Deny, built on the Claude Agent SDK.

The big difference from the TS: the Agent SDK's approval hook (can_use_tool)
WAITS inside the loop instead of ending the run. So there's no "resume" call.
The /api stream simply stays open while the agent waits; /api/approve just
hands the waiting callback its answer through an asyncio.Future.

    TS (AI SDK):      /api streams -> run ENDS at approval -> /api/approve
                      appends a tool-approval-response and starts a NEW run
    Python (Agent SDK): /api streams -> run PAUSES in can_use_tool ->
                      /api/approve resolves the Future -> the SAME run continues

Agent SDK names (ClaudeSDKClient, PermissionResult*, message types) are from
memory and not verified against the current package.
"""

import asyncio
import json
import uuid
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TextBlock,
    ToolPermissionContext,
    ToolUseBlock,
)
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

from ref_index import classify, cwd, teensy

PORT = 3000
app = FastAPI()

# approvalId -> Future the waiting can_use_tool callback is blocked on.
# In memory only, like the TS `conversations` map.
pending: dict[str, asyncio.Future] = {}


def sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


def make_options(events: asyncio.Queue) -> ClaudeAgentOptions:
    """Build per-request options whose can_use_tool talks to the browser.

    For an "approval" command it sends a tool-approval-request event to the
    browser, then awaits a Future until /api/approve resolves it.
    """

    async def can_use_tool(
        tool_name: str, input_data: dict, context: ToolPermissionContext
    ) -> PermissionResultAllow | PermissionResultDeny:
        if tool_name != "mcp__teensy__bash":
            return PermissionResultAllow()

        command = input_data.get("command", "")
        verdict = classify(command)
        if verdict == "safe":
            return PermissionResultAllow()
        if verdict == "blocked":
            return PermissionResultDeny(message=f'"{command}" is not on any allowlist')

        approval_id = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        pending[approval_id] = future
        await events.put({
            "type": "tool-approval-request",
            "approvalId": approval_id,
            "reason": f'"{command}" changes files or installs packages',
        })
        approved = await future
        if approved:
            return PermissionResultAllow()
        return PermissionResultDeny(message="The user declined this command.")

    return ClaudeAgentOptions(
        model="claude-haiku-4-5",
        system_prompt=f"You are a coding agent.\nWorking directory: {cwd}",
        cwd=str(cwd),
        mcp_servers={"teensy": teensy},
        allowed_tools=["mcp__teensy__read", "mcp__teensy__grep"],
        can_use_tool=can_use_tool,
        max_turns=10,
    )


@app.get("/")
async def home():
    return FileResponse(Path(__file__).parent / "public" / "index.html")


@app.post("/api")
async def api(request: Request):
    """Run the agent and stream its events, including approval pauses.

    Two producers feed one queue: the agent's messages, and can_use_tool's
    approval requests. The response streams from that queue until the run ends.
    """
    prompt = (await request.json())["prompt"]
    events: asyncio.Queue = asyncio.Queue()

    async def run_agent():
        try:
            async with ClaudeSDKClient(options=make_options(events)) as client:
                await client.query(prompt)
                async for message in client.receive_response():
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                await events.put({"type": "text-delta", "id": "t", "delta": block.text})
                            elif isinstance(block, ToolUseBlock):
                                await events.put({
                                    "type": "tool-input-available",
                                    "toolCallId": block.id,
                                    "toolName": block.name,
                                    "input": block.input,
                                })
                    elif isinstance(message, ResultMessage):
                        break
        except Exception as error:
            print("Agent error:", error)
            await events.put({"type": "error", "errorText": str(error)})
        finally:
            await events.put(None)

    async def event_stream():
        task = asyncio.create_task(run_agent())
        while (event := await events.get()) is not None:
            yield sse(event)
        await task

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/approve")
async def approve(request: Request):
    """Resolve the Futures the paused can_use_tool callbacks are waiting on.

    No new stream starts here: the original /api stream picks back up on its own.
    """
    body = await request.json()
    for decision in body["decisions"]:
        future = pending.pop(decision["approvalId"], None)
        if future is None:
            return Response("Approval not found (server restarted?). Ask again.", status_code=404)
        future.set_result(decision["approved"])
    return Response(status_code=204)


if __name__ == "__main__":
    import uvicorn

    print(f"TeensyCode UI on http://localhost:{PORT}")
    uvicorn.run(app, port=PORT)
