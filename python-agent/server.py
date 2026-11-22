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
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    create_sdk_mcp_server,
)
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

from approval import Background, Interactive, create_approval
from index import MODEL, SYSTEM_PROMPT, sandbox
from tools import create_bash_tool, create_grep_tool, create_read_tool

PORT = 3000
app = FastAPI()

# Approvals waiting on a click: approval id -> the Future that the paused
# can_use_tool call is waiting on. Kept in memory, so a server restart
# forgets them.
pending: dict[str, asyncio.Future] = {}

# The web UI asks a human instead of blocking outright (the CLI in
# index.py blocks). Two pieces make that work:
#   needs_human  the interactive rule: is this command off the safe list?
#                can_use_tool uses it to decide whether to ask you.
#   web_tools    read and grep, plus a bash tool in background mode (no gate
#                of its own), because by the time bash runs, the command was
#                either safe or you approved it.
needs_human = create_approval(Interactive())
web_tools = create_sdk_mcp_server(
    name="teensy",
    version="0.1.0",
    tools=[
        create_read_tool(sandbox),
        create_grep_tool(sandbox),
        create_bash_tool(sandbox, create_approval(Background())),
    ],
)


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
    requests into this request's event queue (and so into its stream).
    """

    async def can_use_tool(
        tool_name: str, input_data: dict, context: ToolPermissionContext
    ) -> PermissionResultAllow | PermissionResultDeny:
        """Decide whether a tool call may run, asking the browser if needed.

          read / grep              -> allowed (they only look at files)
          bash, safe-list command  -> allowed
          bash, anything else      -> send Approve/Deny to the browser, wait
                                      for the click, then allow or refuse
          any other tool           -> refused (only our three exist anyway)
        context.tool_use_id lets the browser attach the buttons to the
        right tool call on screen.
        """
        if tool_name in ("mcp__teensy__read", "mcp__teensy__grep"):
            return PermissionResultAllow()
        if tool_name != "mcp__teensy__bash":
            return PermissionResultDeny(message=f"{tool_name} is not available.")

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
        max_turns=10,
    )


@app.get("/")
async def home():
    """Serve the chat page."""
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
      ResultMessage     the run is done
    """
    prompt = (await request.json())["prompt"]
    events: asyncio.Queue = asyncio.Queue()

    async def run_agent():
        # One model turn can arrive as several AssistantMessages (one per
        # content block) sharing a message_id, so a step starts only when
        # the id changes.
        last_message_id = None
        try:
            async with ClaudeSDKClient(options=make_options(events)) as client:
                await client.query(prompt)
                async for message in client.receive_response():
                    if isinstance(message, AssistantMessage):
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
    body = await request.json()
    for decision in body["decisions"]:
        future = pending.pop(decision["approvalId"], None)
        if future is None:
            return Response("Approval not found (server restarted?). Ask again.", status_code=404)
        future.set_result(decision["approved"])
    return Response(status_code=204)


if __name__ == "__main__":
    import uvicorn

    print(f"TeensyCode UI (Python) on http://localhost:{PORT}  ·  model {MODEL}  ·  working in {sandbox.working_directory}")
    uvicorn.run(app, port=PORT)
