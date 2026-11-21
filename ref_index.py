"""Python equivalent of index.ts (reference only, never executed).

Claude Agent SDK version (pip install claude-agent-sdk). The Agent SDK runs
the tool loop for you, like ToolLoopAgent. Custom tools are registered as an
in-process MCP server.
"""

import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path

from pydantic import BaseModel, Field
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TextBlock,
    ToolPermissionContext,
    create_sdk_mcp_server,
    query,
    tool,
)

cwd = Path(sys.argv[1] if len(sys.argv) > 1 else os.getcwd()).resolve()

MAX_LINES = 500
MAX_MATCHES = 50

# List 1: run automatically (read-only).
SAFE_PREFIXES = [
    "ls", "cat", "echo", "pwd", "which", "find",
    "head", "tail", "wc", "git log", "git status", "git diff",
]

# List 2: allowed, but only after a human says yes (changes files or installs).
APPROVAL_PREFIXES = [
    "mkdir", "touch", "cp", "mv", "rm",
    "git add", "git commit", "git checkout", "git restore",
    "npm install", "bun add", "bun install", "bun test", "npx tsc",
]

# List 3: everything else is blocked. These shell tricks are always blocked,
# even for list 1/2 commands, so "ls; rm -rf ~" can't smuggle anything through.
FORBIDDEN = re.compile(r"[;&|`$<>]|\s-(exec|execdir|delete|ok)\b")


def classify(command: str) -> str:
    """Return "safe", "approval", or "blocked" for a shell command.

    Matches "ls" or "ls -la" but not "lsof" (prefix plus a space, or exact).
    """
    cmd = command.strip()
    if FORBIDDEN.search(cmd):
        return "blocked"

    def matches(p: str) -> bool:
        return cmd == p or cmd.startswith(p + " ")

    if any(matches(p) for p in SAFE_PREFIXES):
        return "safe"
    if any(matches(p) for p in APPROVAL_PREFIXES):
        return "approval"
    return "blocked"


class ReadInput(BaseModel):
    """Input schema for the read tool. Same role as the Zod inputSchema.

    `int | None = None` is Pydantic's version of `.optional()`.
    Each Field(description=...) is the model-facing hint, like `.describe()`.
    """

    path: str = Field(description="File path relative to working directory")
    offset: int | None = Field(default=None, description="Start line (1-indexed)")
    limit: int | None = Field(default=None, description="Max lines to return")


# @tool(name, description, schema) is the Agent SDK's tool({...}).
# The description is a prompt to the model, same text as the TS.
# @tool takes JSON Schema, so we pass the one Pydantic generates.
@tool(
    "read",
    """Read a file from the project. Returns numbered lines.
WHEN TO USE: viewing file contents, checking configs, reading source code.
WHEN NOT TO USE: searching across files (use grep instead).
DO NOT USE FOR: running commands, listing directories.""",
    ReadInput.model_json_schema(),
)
async def read(args: dict) -> dict:
    """Read a file and return numbered lines, capped at MAX_LINES.

    1. Validate the model's input with ReadInput (like the AI SDK does with Zod).
    2. Resolve the path against the project directory.
    3. Apply offset/limit, then the 500-line cap with a truncation note.
    4. Return MCP-style content blocks (Agent SDK tools can't return a bare string).
    """
    inp = ReadInput.model_validate(args)

    abs_path = (cwd / inp.path).resolve()
    lines = abs_path.read_text(encoding="utf-8").split("\n")

    offset = inp.offset
    limit = inp.limit
    if offset:
        lines = lines[offset - 1:]
    if limit:
        lines = lines[:limit]

    truncated = len(lines) > MAX_LINES
    if truncated:
        lines = lines[:MAX_LINES]

    numbered = [f"{(offset or 1) + i}: {line}" for i, line in enumerate(lines)]
    text = "\n".join(numbered)
    if truncated:
        text += f"\n... (truncated at {MAX_LINES} lines)"

    return {"content": [{"type": "text", "text": text}]}


class GrepInput(BaseModel):
    """Input schema for the grep tool. Same role as the Zod inputSchema."""

    pattern: str = Field(description="Regex pattern to search for")
    path: str | None = Field(default=None, description="Directory to search (default: working dir)")
    glob: str | None = Field(default=None, description="File glob filter, e.g. '*.ts'")


# Same four-section contract as the TS description: WHEN TO USE,
# WHEN NOT TO USE, DO NOT USE FOR, EXAMPLES. The model routes on this text.
@tool(
    "grep",
    """Search file contents using regex. Returns matching lines with file paths.
WHEN TO USE: finding patterns across multiple files, locating function definitions,
  searching for imports, finding TODOs or error messages.
WHEN NOT TO USE: reading a known file (use read instead).
DO NOT USE FOR: running commands, listing directories.
EXAMPLES:
  - Find all TODO comments: pattern "TODO" glob "*.ts"
  - Find function definitions: pattern "function \\w+" glob "*.ts\"""",
    GrepInput.model_json_schema(),
)
async def grep(args: dict) -> dict:
    """Search files with `grep -rn`, capped at MAX_MATCHES.

    Differences from the TS, both deliberate:
      - subprocess.run gets a list of arguments, so no shell is involved and no
        quote-escaping is needed (the TS builds a shell string, so it escapes ').
      - grep exits 1 when nothing matches. With check=False that's just a normal
        result, which replaces the TS try/catch.
    Output past MAX_MATCHES is cut, with the total count reported.
    """
    inp = GrepInput.model_validate(args)
    search_dir = (cwd / (inp.path or ".")).resolve()

    proc = subprocess.run(
        [
            "grep", "-rn",
            "--exclude-dir=node_modules", "--exclude-dir=.git",
            f"--include={inp.glob or '*'}",
            "-E", inp.pattern,
            str(search_dir),
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    lines = [line for line in proc.stdout.strip().split("\n") if line]

    if not lines:
        text = "No matches found."
    elif len(lines) > MAX_MATCHES:
        text = "\n".join(lines[:MAX_MATCHES]) + f"\n... ({len(lines)} total, showing first {MAX_MATCHES})"
    else:
        text = "\n".join(lines)

    return {"content": [{"type": "text", "text": text}]}


class BashInput(BaseModel):
    """Input schema for the bash tool. Same role as the Zod inputSchema."""

    command: str = Field(description="Shell command to execute")


# Same four-section contract as the TS description.
@tool(
    "bash",
    """Execute a shell command in the working directory.
WHEN TO USE: running build commands, installing packages, running tests,
  git operations, directory listings.
WHEN NOT TO USE: reading file contents (use read instead).
  Searching for patterns (use grep instead).
DO NOT USE FOR: reading files (use read), searching code (use grep).""",
    BashInput.model_json_schema(),
)
async def bash(args: dict) -> dict:
    """Run a shell command that already passed the permission check.

    can_use_tool (below) runs first and decides safe / ask the human / deny,
    so this function only sees safe or human-approved commands. The blocked
    check here is a second line of defense, same as in the TS execute.
    shell=True is required because commands arrive as one string; classify()
    is what keeps that from being dangerous.
    """
    inp = BashInput.model_validate(args)

    if classify(inp.command) == "blocked":
        text = (
            f'Blocked: "{inp.command}" is not allowed. '
            f"Safe: {', '.join(SAFE_PREFIXES)}. Needs approval: {', '.join(APPROVAL_PREFIXES)}."
        )
        return {"content": [{"type": "text", "text": text}]}

    try:
        proc = subprocess.run(
            inp.command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return {"content": [{"type": "text", "text": "Exit 124: timed out after 30s"}]}

    if proc.returncode != 0:
        text = f"Exit {proc.returncode}: {proc.stdout or proc.stderr}"
    else:
        text = proc.stdout or "(no output)"
    return {"content": [{"type": "text", "text": text}]}


# Register the hand-written tools with the Agent SDK. "MCP server" is just
# the SDK's packaging format for custom tools: in-process, not networked.
# Same job as `tools: { read, grep, bash }` in the TS.
teensy = create_sdk_mcp_server(name="teensy", version="0.1.0", tools=[read, grep, bash])


async def can_use_tool(
    tool_name: str, input_data: dict, context: ToolPermissionContext
) -> PermissionResultAllow | PermissionResultDeny:
    """Permission callback: the Agent SDK's version of toolApproval.

    The SDK calls this before running any tool that isn't in allowed_tools.
    Unlike the AI SDK, the loop does NOT stop: this function simply awaits the
    human's answer, then returns allow or deny, and the loop carries on.
      safe     -> allow without asking            ("not-applicable")
      approval -> ask on the terminal, then allow or deny  ("user-approval")
      blocked  -> deny with a message the model sees       ("denied")
    input() blocks, so it runs in a thread to keep the event loop free.
    """
    if tool_name != "mcp__teensy__bash":
        return PermissionResultAllow()

    command = input_data.get("command", "")
    verdict = classify(command)

    if verdict == "safe":
        return PermissionResultAllow()
    if verdict == "blocked":
        return PermissionResultDeny(message=f'"{command}" is not on any allowlist')

    answer = await asyncio.to_thread(
        input, f"\nThe agent wants to run: {command}\nAllow? [y/N] "
    )
    if answer.strip().lower() == "y":
        return PermissionResultAllow()
    return PermissionResultDeny(message="The user declined this command.")


# ClaudeAgentOptions plays the role of `new ToolLoopAgent({...})`.
#   max_turns=10   -> stopWhen: isStepCount(10)
#   allowed_tools  -> auto-approved, so can_use_tool is never asked about them.
#                     bash is deliberately left OUT so every bash call goes
#                     through can_use_tool. (Full names are mcp__<server>__<tool>.)
#   can_use_tool   -> the approval callback above (toolApproval in the TS).
#                     Unverified detail: the SDK may require streaming input
#                     (an async-iterable prompt or ClaudeSDKClient) for this.
options = ClaudeAgentOptions(
    model="claude-haiku-4-5",
    system_prompt=f"You are a coding agent.\nWorking directory: {cwd}",
    cwd=str(cwd),
    mcp_servers={"teensy": teensy},
    allowed_tools=["mcp__teensy__read", "mcp__teensy__grep"],
    can_use_tool=can_use_tool,
    max_turns=10,
)


async def main():
    """CLI entry point, same as the `if (import.meta.main)` block in the TS.

    query() runs the whole tool loop internally (ask model -> run tools ->
    feed results back -> repeat). The `async for` is not the loop itself;
    it reads the live stream of messages the loop produces:
      - AssistantMessage: the model's turn; keep the latest TextBlock as the answer.
      - ResultMessage: the loop finished; num_turns is the step count.
    """
    prompt = " ".join(sys.argv[2:]) or "Hello!"
    text, turns = "", 0
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    text = block.text
        elif isinstance(message, ResultMessage):
            turns = message.num_turns
    print(text)
    print(f"\n({turns} steps)")


# Same as `if (import.meta.main)`: only run when executed directly.
if __name__ == "__main__":
    asyncio.run(main())
