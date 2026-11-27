"""TeensyCode web UI, built on the Claude Agent SDK and FastAPI.

Serves the same page as the Bun server (public/index.html) and streams the
agent's steps to it live: text, tool calls, tool results, and Approve/Deny
buttons for commands that aren't on the safe list.

How approval works here: when the agent wants to run an unsafe command,
can_use_tool pauses the run and waits. The browser shows Approve/Deny, and
your click (POST /api/approve) wakes the waiting callback up. The same run
then carries on, and its results keep flowing down the original stream.

Run:  .venv/bin/python server.py <folder>    ->  http://localhost:3000
Needs: ANTHROPIC_API_KEY in the environment.

Slack: POST /slack/events (slack_events.py) runs the same agent for Slack
messages and replies in the thread. On Cloud Run the web UI routes are
switched off with DISABLE_WEB_UI=1, because the URL is public; only the
Slack route (which checks Slack's signature) and /health stay on.
"""

import asyncio
import json
import os
import uuid
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    StreamEvent,
    TextBlock,
    ToolPermissionContext,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    create_sdk_mcp_server,
)
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

from approval import Background, Interactive, create_approval
from index import MODEL, SKILLS, SYSTEM_PROMPT, sandbox
from telemetry import TokenBudget
from registry import create_registry, register_builtins
from slack_events import router as slack_router

# Cloud Run tells the container which port to listen on; locally it's 3000.
PORT = int(os.environ.get("PORT", "3000"))
# Locally the server only listens on this machine; in the container it must listen on all
# interfaces (HOST=0.0.0.0) so Cloud Run can reach it.
HOST = os.environ.get("HOST", "127.0.0.1")
# The web UI can run the agent and approve bash commands, so it must not be reachable on a
# public URL. Cloud Run sets DISABLE_WEB_UI=1.
WEB_UI_DISABLED = os.environ.get("DISABLE_WEB_UI") == "1"

app = FastAPI()
app.include_router(slack_router)


@app.get("/health")
async def health():
    """Liveness check for Cloud Run and for you: returns ok if the server is up."""
    return {"ok": True}

# Approvals waiting on a click: approval id -> the Future that the paused
# can_use_tool call is waiting on. Kept in memory, so a server restart
# forgets them.
pending: dict[str, asyncio.Future] = {}

# The web UI asks a human instead of blocking outright (the CLI in
# index.py blocks). Two pieces make that work:
#   needs_human  the interactive rule: is this command off the safe list?
#                can_use_tool uses it to decide whether to ask you.
#   web tools    read and grep, plus a bash tool in background mode (no gate
#                of its own), because by the time bash runs, the command was
#                either safe or you approved it. Built per request (in
#                make_options) so the task tool's subagent events reach that
#                request's stream.
needs_human = create_approval(Interactive())


def sse(event: dict) -> str:
    """Format one event as a Server-Sent Events message ("data: {...}" + blank line)."""
    return f"data: {json.dumps(event)}\n\n"


def result_text(block: ToolResultBlock) -> str:
    """Pull the plain text out of a tool result.

    The content can be a plain string or a list of content blocks like
    [{"type": "text", "text": "..."}]; either way, return one string.
    """
    if isinstance(block.content, str):
        return block.content
    if isinstance(block.content, list):
        return "\n".join(part.get("text", "") for part in block.content if isinstance(part, dict))
    return ""


def make_options(events: asyncio.Queue) -> ClaudeAgentOptions:
    """Build the agent settings for one request.

    can_use_tool is created fresh per request so it can push approval
    requests into this request's event queue (and so into its stream). The
    tool bundle is also built per request, from the registry, so the task
    tool's subagent events reach this request's stream.
    """

    async def on_subagent(event: dict) -> None:
        """Forward a subagent event to the browser's Subagents panel."""
        await events.put({"type": "data-subagent", "data": event})

    registry = create_registry()
    register_builtins(registry, sandbox, SKILLS, Background(), on_subagent)
    web_tools = create_sdk_mcp_server(name="teensy", version="0.1.0", tools=registry.tools())

    async def can_use_tool(
        tool_name: str, input_data: dict, context: ToolPermissionContext
    ) -> PermissionResultAllow | PermissionResultDeny:
        """Decide whether a tool call may run, asking the browser if needed.

          our tools except bash    -> allowed (read, grep, task, askUser,
                                      todo, loadSkill change nothing)
          bash, safe-list command  -> allowed
          bash, anything else      -> send Approve/Deny to the browser, wait
                                      for the click, then allow or refuse
          any other tool           -> refused (built-in tools are off anyway)
        context.tool_use_id lets the browser attach the buttons to the
        right tool call on screen.
        """
        if not tool_name.startswith("mcp__teensy__"):
            return PermissionResultDeny(message=f"{tool_name} is not available.")
        if tool_name != "mcp__teensy__bash":
            return PermissionResultAllow()

        command = input_data.get("command", "")
        if not needs_human(command):
            return PermissionResultAllow()

        approval_id = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        pending[approval_id] = future
        await events.put({
            "type": "tool-approval-request",
            "approvalId": approval_id,
            "toolCallId": context.tool_use_id,
            "reason": f'"{command}" is not on the safe list',
        })
        approved = await future
        if approved:
            return PermissionResultAllow()
        return PermissionResultDeny(message="The user declined this command.")

    # Isolation: the SDK runs the Claude Code CLI underneath, which would
    # otherwise load your whole local setup (settings, plugins, skills, MCP
    # servers) and blow past the context limit.
    #   tools=[]                no built-in tools (its own Bash, Read, ...),
    #                           so the agent can't step around the approval check
    #   setting_sources=[]      don't load user/project settings, CLAUDE.md,
    #                           plugins or skills
    #   strict_mcp_config=True  use only the MCP servers listed here
    # No allowed_tools: every call goes through can_use_tool, which waves
    # read and grep straight through.
    return ClaudeAgentOptions(
        model=MODEL,
        system_prompt=SYSTEM_PROMPT,
        cwd=sandbox.working_directory,
        tools=[],
        setting_sources=[],
        strict_mcp_config=True,
        mcp_servers={"teensy": web_tools},
        can_use_tool=can_use_tool,
        max_turns=15,
        include_partial_messages=True,
    )


@app.get("/")
async def home():
    """Serve the chat page."""
    if WEB_UI_DISABLED:
        return Response(status_code=404)
    return FileResponse(Path(__file__).parent / "public" / "index.html")


@app.post("/api")
async def api(request: Request):
    """Run the agent on a prompt and stream everything it does.

    Two things feed one queue: the agent's messages (read in run_agent) and
    can_use_tool's approval requests. event_stream() sends whatever lands in
    the queue to the browser, until run_agent puts None to say it's finished.

    What gets sent, by message type:
      AssistantMessage  a new step; its text and tool calls
      UserMessage       tool results coming back (that's how the SDK labels them)
      StreamEvent       raw API events; each "message_delta" carries a step's
                        final token usage, priced and sent as "data-usage"
                        for the token-budget panel
      ResultMessage     the run is done
    """
    if WEB_UI_DISABLED:
        return Response(status_code=404)
    prompt = (await request.json())["prompt"]
    events: asyncio.Queue = asyncio.Queue()

    async def run_agent():
        # One model turn can arrive as several AssistantMessages (one per
        # content block) sharing a message_id, so a step starts only when
        # the id changes.
        last_message_id = None
        budget = TokenBudget(MODEL)
        try:
            async with ClaudeSDKClient(options=make_options(events)) as client:
                await client.query(prompt)
                async for message in client.receive_response():
                    if isinstance(message, StreamEvent):
                        if message.event.get("type") == "message_delta":
                            step = budget.record(message.event.get("usage", {}))
                            await events.put({"type": "data-usage", "data": step.to_event()})
                    elif isinstance(message, AssistantMessage):
                        if message.message_id != last_message_id:
                            last_message_id = message.message_id
                            await events.put({"type": "start-step"})
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                await events.put({"type": "text-delta", "id": str(uuid.uuid4()), "delta": block.text})
                            elif isinstance(block, ToolUseBlock):
                                await events.put({
                                    "type": "tool-input-available",
                                    "toolCallId": block.id,
                                    "toolName": block.name.removeprefix("mcp__teensy__"),
                                    "input": block.input,
                                })
                    elif isinstance(message, UserMessage) and isinstance(message.content, list):
                        for block in message.content:
                            if isinstance(block, ToolResultBlock):
                                if block.is_error:
                                    await events.put({"type": "tool-output-error", "toolCallId": block.tool_use_id, "errorText": result_text(block)})
                                else:
                                    await events.put({"type": "tool-output-available", "toolCallId": block.tool_use_id, "output": result_text(block)})
                    elif isinstance(message, ResultMessage):
                        break
            print(budget.summary(), flush=True)
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
    """Hand your Approve/Deny clicks to the paused can_use_tool calls.

    Each decision resolves its Future, which wakes the waiting call. No new
    stream starts here (204 = "done, nothing to send back"): the original
    /api stream picks back up on its own.
    """
    if WEB_UI_DISABLED:
        return Response(status_code=404)
    body = await request.json()
    for decision in body["decisions"]:
        future = pending.pop(decision["approvalId"], None)
        if future is None:
            return Response("Approval not found (server restarted?). Ask again.", status_code=404)
        future.set_result(decision["approved"])
    return Response(status_code=204)


if __name__ == "__main__":
    import uvicorn

    print(f"TeensyCode UI (Python) on http://{HOST}:{PORT}  ·  model {MODEL}  ·  working in {sandbox.working_directory}")
    uvicorn.run(app, host=HOST, port=PORT)
