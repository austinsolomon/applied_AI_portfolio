"""TeensyCode: a small coding agent built on the Claude Agent SDK.

You give it a folder and a question. It can read files, search across files,
and run shell commands to answer. Which commands it may run depends on the
approval mode (see approval.py). This file wires the pieces together:

  sandbox.py        the Sandbox interface and lifecycle hooks
  sandbox_local.py  the local backend (your real folder)
  sandbox_memory.py the throwaway backend (a temp copy, deleted on stop)
  tools.py          read / grep / bash, built from a sandbox
  approval.py       approval modes for bash
  system.py         the system prompt builder

Setup: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
Run:   set -a && . ./.env && set +a      (loads ANTHROPIC_API_KEY)
       .venv/bin/python index.py <folder> "your question"
       APPROVAL_MODE=background .venv/bin/python index.py . "..."
       MODEL=claude-sonnet-5-5 .venv/bin/python index.py . "..."
       SANDBOX=temp-copy .venv/bin/python index.py . "..."
       APPROVAL_MODE=delegated APPROVAL_TRUST="git status,pwd" .venv/bin/python index.py . "..."
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    StreamEvent,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    create_sdk_mcp_server,
    query,
)

from approval import approval_from_env, create_approval
from sandbox import Sandbox, SandboxLifecycle
from sandbox_local import create_local_sandbox
from sandbox_memory import create_temp_copy_sandbox
from system import PromptContext, build_system_prompt
from tools import create_bash_tool, create_grep_tool, create_read_tool

# The project folder the agent works in: the first command-line argument, or
# the folder you ran the script from.
cwd = Path(sys.argv[1] if len(sys.argv) > 1 else os.getcwd()).resolve()

# Setup and teardown around the sandbox. Empty for now, so nothing extra
# runs; the hook points are in place for the cloud work in Module 7.
lifecycle = SandboxLifecycle()


async def start_sandbox(directory: Path) -> Sandbox:
    """Create the sandbox picked by SANDBOX, then run the after_start hook.

    SANDBOX chooses the backend:
      local      (default) your real folder and real commands
      temp-copy  a throwaway copy of the project; your folder is never changed
    Every tool goes through the sandbox, so this is the only place that
    knows which backend is in use.
    """
    if os.environ.get("SANDBOX", "local") == "temp-copy":
        sb: Sandbox = await create_temp_copy_sandbox(directory)
    else:
        sb = create_local_sandbox(directory)
    if lifecycle.after_start:
        await lifecycle.after_start(sb)
    return sb


async def stop_sandbox(sb: Sandbox) -> None:
    """Run the before_stop hook, then shut the sandbox down."""
    if lifecycle.before_stop:
        await lifecycle.before_stop(sb)
    await sb.stop()


# Sandbox creation is async, so asyncio.run() is used once here at startup.
# (This happens on import, before any event loop is running, which holds
# for both the CLI and `python server.py`.)
sandbox: Sandbox = asyncio.run(start_sandbox(cwd))

# Which Claude model does the thinking. Haiku is the cheap default; set
# MODEL=claude-sonnet-5-5 when instruction-following matters (Haiku tends to
# skip the Verification contract; Sonnet follows it).
MODEL = os.environ.get("MODEL", "claude-haiku-4-5")

# The approval mode for this run, and the tools built on the sandbox.
approval = approval_from_env()
TOOLS = [
    create_read_tool(sandbox),
    create_grep_tool(sandbox),
    create_bash_tool(sandbox, create_approval(approval)),
]


async def gather_project_facts(sb: Sandbox) -> tuple[str | None, str | None]:
    """Ask the sandbox for the git branch and the AGENTS.md contents.

    Both go through the sandbox rather than the local disk, because with a
    cloud backend the project wouldn't be on this machine at all.
      branch   `git rev-parse --abbrev-ref HEAD`; None if it's not a git repo
      context  the text of AGENTS.md (the project's instructions for agents:
               test commands, layout, style, past mistakes); None if absent
    """
    branch = await sb.exec("git rev-parse --abbrev-ref HEAD")
    git_branch = branch.stdout.strip() if branch.exit_code == 0 else None
    try:
        project_context = await sb.read_file("AGENTS.md")
    except FileNotFoundError:
        project_context = None
    return git_branch, project_context


GIT_BRANCH, PROJECT_CONTEXT = asyncio.run(gather_project_facts(sandbox))

# The system prompt, built from runtime facts (see system.py). sandbox_type
# and tool_names are read off the real objects, so they can't drift.
SYSTEM_PROMPT = build_system_prompt(PromptContext(
    working_directory=sandbox.working_directory,
    sandbox_type=sandbox.type,
    tool_names=[t.name for t in TOOLS],
    git_branch=GIT_BRANCH,
    project_context=PROJECT_CONTEXT,
))

# Bundle the three tools so the agent can use them. The SDK calls a bundle
# of custom tools an "MCP server", but this one runs inside this script, with
# no network involved. The agent sees each tool as mcp__teensy__<name>.
teensy = create_sdk_mcp_server(name="teensy", version="0.1.0", tools=TOOLS)


# The agent's settings:
#   model          which Claude model does the thinking
#   system_prompt  standing instructions sent with every request
#   cwd            the folder the agent works in
#   tools=[]       turn off the SDK's built-in tools (its own Bash, Read,
#                  Write, ...) so the agent can only use our three
#   setting_sources=[], strict_mcp_config=True
#                  isolation: the SDK runs the Claude Code CLI underneath,
#                  which would otherwise load your local settings, plugins,
#                  skills and MCP servers (enough to exceed the context limit)
#   mcp_servers    the tool bundle from above
#   allowed_tools  our three tools run without the SDK asking permission.
#                  That's safe because the bash tool enforces the approval
#                  policy itself.
#   max_turns      stop after 15 rounds, in case the agent gets stuck in a loop
#
# Context management (Module 5) in this stack: the Claude Code CLI under the
# SDK owns the conversation history, so the harness cannot rewrite messages
# between steps. Instead the CLI:
#   - caches the repeated prefix automatically (5.4); log_step_usage shows
#     the cached share once the prefix passes the model's minimum size
#   - compacts (summarizes) the history when it nears the context limit, in
#     place of per-step pruning (5.2)
#   - replaces any single tool result over ~25k tokens with an error and
#     saves the full text to a file, on top of our own caps in tools.py (5.3)
#   include_partial_messages
#                  also stream raw API events; log_step_usage reads the
#                  per-step token counts from them
options = ClaudeAgentOptions(
    model=MODEL,
    system_prompt=SYSTEM_PROMPT,
    cwd=sandbox.working_directory,
    tools=[],
    setting_sources=[],
    strict_mcp_config=True,
    mcp_servers={"teensy": teensy},
    allowed_tools=["mcp__teensy__read", "mcp__teensy__grep", "mcp__teensy__bash"],
    max_turns=15,
    include_partial_messages=True,
)


def first_line(block: ToolResultBlock) -> str:
    """Return the first line of a tool result's text, for one-line logging."""
    content = block.content
    if isinstance(content, list):
        content = "\n".join(part.get("text", "") for part in content if isinstance(part, dict))
    return str(content or "").split("\n")[0]


def log_step_usage(event: dict, step: int) -> bool:
    """Log one step's token usage; return True if this event ended a step.

    Every step resends the whole conversation, so input grows with each tool
    result while output stays small. Each model call streams a
    "message_delta" event when it finishes, carrying that call's final usage.
    Input counts include any cached tokens (cache reads and writes are
    reported separately), so the number stays the full amount sent.
    Printed to stderr to stay separate from the agent's answer on stdout.
    """
    if event.get("type") != "message_delta":
        return False
    usage = event.get("usage", {})
    cached = usage.get("cache_read_input_tokens", 0)
    sent = usage.get("input_tokens", 0) + cached + usage.get("cache_creation_input_tokens", 0)
    print(
        f"Step {step}: {sent} input, {usage.get('output_tokens', 0)} output, {cached} cached",
        file=sys.stderr,
    )
    return True


async def main():
    """Take a question from the command line, run the agent, print the answer.

    query() runs the whole agent loop for you: ask Claude, run whatever
    tools it picks, send back the results, and repeat until it answers.
    The `async for` doesn't drive that loop; it just watches the messages
    the loop produces as they happen:
      - StreamEvent: raw API events; used only for per-step token logging.
      - AssistantMessage: something Claude said. Keep the latest text,
        because the last thing it says is the final answer. Tool calls in it
        are remembered by id until their result arrives.
      - UserMessage: carries tool results back (the SDK labels them as
        coming from the user). Each is printed live as "[tool] {args} -> first
        line of output", so a run that crashes midway still shows its steps.
      - ResultMessage: the loop is done; num_turns says how many rounds it took.
    try/finally: before_stop and stop run even if the agent fails mid-run
    (stop is a no-op locally; temp-copy deletes its copy; a cloud sandbox
    would otherwise keep running and costing money).
    """
    prompt = " ".join(sys.argv[2:]) or "Hello!"
    print(
        f"(sandbox: {sandbox.type} · model: {MODEL} · approval: {approval.model_dump()} · "
        f"AGENTS.md: {'loaded' if PROJECT_CONTEXT else 'none'})",
        flush=True,
    )
    text, turns, step = "", 0, 0
    calls: dict[str, str] = {}
    try:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, StreamEvent):
                if log_step_usage(message.event, step):
                    step += 1
            elif isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        text = block.text
                    elif isinstance(block, ToolUseBlock):
                        calls[block.id] = f"[{block.name.removeprefix('mcp__teensy__')}] {json.dumps(block.input)}"
            elif isinstance(message, UserMessage) and isinstance(message.content, list):
                for block in message.content:
                    if isinstance(block, ToolResultBlock):
                        print(f"{calls.pop(block.tool_use_id, '[tool]')} -> {first_line(block)}", flush=True)
            elif isinstance(message, ResultMessage):
                turns = message.num_turns
    finally:
        await stop_sandbox(sandbox)
    print(text)
    print(f"\n({turns} steps)")


# Run main() only when this file is executed directly, not when another
# file (like server.py) imports it to reuse the tools.
if __name__ == "__main__":
    asyncio.run(main())
