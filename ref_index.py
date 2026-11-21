"""TeensyCode: a small coding agent built on the Claude Agent SDK.

You give it a folder and a question. It can read files, search across files,
and run a limited set of shell commands to answer. Anything that changes your
files waits for you to type "y" first.

Run:  python ref_index.py <folder> "your question"
Needs: pip install claude-agent-sdk pydantic, and ANTHROPIC_API_KEY set.
(Reference file: it compiles but has not been run.)
"""

import asyncio
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

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

# The project folder the agent works in: the first command-line argument, or
# the folder you ran the script from. Every file path the agent uses is
# resolved relative to this.
cwd = Path(sys.argv[1] if len(sys.argv) > 1 else os.getcwd()).resolve()

# Output caps. Tool output goes straight into the model's context, and huge
# outputs waste tokens and bury the useful part, so long results are cut off
# with a note saying so.
MAX_LINES = 500
MAX_MATCHES = 50

# Commands the agent may run on its own. All of them only look at things;
# none of them change anything.
SAFE_PREFIXES = [
    "ls", "cat", "echo", "pwd", "which", "find",
    "head", "tail", "wc", "git log", "git status", "git diff",
]

# Commands that change files or install packages. Allowed, but only after
# you approve each one.
APPROVAL_PREFIXES = [
    "mkdir", "touch", "cp", "mv", "rm",
    "git add", "git commit", "git checkout", "git restore",
    "npm install", "bun add", "bun install", "bun test", "npx tsc",
]

# Shell tricks that are always refused, even inside an allowed command:
# chaining (; & |), backticks and $(...) substitution, redirects (< >), and
# find's -exec/-delete. Without this, "ls; rm -rf ~" would pass because it
# starts with "ls". Any command on neither list above is refused too.
FORBIDDEN = re.compile(r"[;&|`$<>]|\s-(exec|execdir|delete|ok)\b")


def create_classifier(
    safe_prefixes: list[str], approval_prefixes: list[str] | None = None
) -> Callable[[str], str]:
    """Build a function that sorts a command into "safe", "approval", or "blocked".

    You pass in the two lists, and it returns a classify(command) function
    that checks, in order:
      1. Contains a forbidden shell trick?  -> "blocked"
      2. Starts with a safe command?        -> "safe"
      3. Starts with an approval command?   -> "approval"
      4. Anything else                      -> "blocked"
    "Starts with" means the whole word: "ls" and "ls -la" match "ls",
    but "lsof" does not.

    It's a factory (a function that returns a function) so the bash tool and
    the permission check below share the exact same rules.
    """
    approval_prefixes = approval_prefixes or []

    def classify(command: str) -> str:
        cmd = command.strip()
        if FORBIDDEN.search(cmd):
            return "blocked"

        def matches(p: str) -> bool:
            return cmd == p or cmd.startswith(p + " ")

        if any(matches(p) for p in safe_prefixes):
            return "safe"
        if any(matches(p) for p in approval_prefixes):
            return "approval"
        return "blocked"

    return classify


# The one shared rule set, used by can_use_tool further down.
classify = create_classifier(SAFE_PREFIXES, APPROVAL_PREFIXES)


class ReadInput(BaseModel):
    """The arguments the model must send when it calls the read tool.

    Pydantic does two jobs here. model_json_schema() turns this class into
    the schema the model is shown, so it knows which arguments exist. And
    model_validate() checks what the model actually sent before we use it.
    Each Field description is a hint the model reads.
    `int | None = None` means the argument is optional.
    """

    path: str = Field(description="File path relative to working directory")
    offset: int | None = Field(default=None, description="Start line (1-indexed)")
    limit: int | None = Field(default=None, description="Max lines to return")


# Register `read` as a tool: its name, the description the model reads to
# decide when to use it, and its argument schema. The description is
# effectively a prompt, and its five sections steer the model toward the
# right tool for each job.
@tool(
    "read",
    """Read a file from the project. Returns numbered lines.

WHEN TO USE: viewing file contents, checking configurations, reading source code,
  examining specific lines with offset/limit.

WHEN NOT TO USE: searching for patterns across files (use grep instead).
  Running commands (use bash instead).

DO NOT USE FOR: searching code (use grep), executing commands (use bash),
  listing directories (use bash).

USAGE: path is relative to working directory. offset (1-indexed) and limit
  are optional. Output is capped at 500 lines.

EXAMPLES:
  - Read a config: path "package.json"
  - Read lines 40-60 of a file: path "index.ts" offset 40 limit 21""",
    ReadInput.model_json_schema(),
)
async def read(args: dict) -> dict:
    """Open one file and return its lines, numbered.

    1. Check the model's arguments against ReadInput.
    2. Turn the relative path into a full path inside the project.
    3. If offset/limit were given, keep only that slice of lines.
    4. Cut off anything past MAX_LINES and say so.
    5. Number each line ("12: const x = 1") so the model can refer to
       exact lines later.
    Tools return a dict of "content blocks" rather than a plain string;
    that's the format the SDK expects.
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
    """The arguments for the grep tool: what to search for, and where.

    Only `pattern` is required. `path` narrows the search to a subfolder,
    and `glob` narrows it to certain file types, such as "*.ts".
    """

    pattern: str = Field(description="Regex pattern to search for")
    path: str | None = Field(default=None, description="Directory to search (default: working dir)")
    glob: str | None = Field(default=None, description="File glob filter, e.g. '*.ts'")


# Register `grep`. Its description steers searches here instead of opening
# file after file with read, or running grep through bash.
@tool(
    "grep",
    """Search file contents using regex. Returns matching lines with file paths.

WHEN TO USE: finding patterns across multiple files, locating function definitions,
  searching for imports, finding TODOs or error messages.

WHEN NOT TO USE: reading a known file (use read instead).
  Running commands (use bash instead).

DO NOT USE FOR: reading files (use read), listing directories (use bash).

USAGE: pattern is an extended regex string. glob filters by file name
  (default: all files). node_modules and .git are skipped.
  Results are capped at 50 matches.

EXAMPLES:
  - Find all TODO comments: pattern "TODO" glob "*.ts"
  - Find function definitions: pattern "function \\w+" glob "*.ts"
  - Find imports of a package: pattern "from 'express'" glob "*.ts\"""",
    GrepInput.model_json_schema(),
)
async def grep(args: dict) -> dict:
    """Search every file under a folder and return the lines that match.

    Runs the system `grep` with:
      -r  search subfolders too
      -n  include line numbers
      --exclude-dir  skip node_modules and .git (huge, and never useful)
      --include      only look in files matching the glob
      -E             use extended regex syntax
    Each result looks like "path/file.ts:42:matching line".

    The command is passed as a list of separate arguments, not one string,
    so no shell is involved and odd characters in the pattern can't break
    anything. grep exits with 1 when it finds nothing; check=False makes
    that count as a normal result instead of an error. More than
    MAX_MATCHES results are cut off, with the total count reported.
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
    """The single argument for the bash tool: the command to run, as one string."""

    command: str = Field(description="Shell command to execute")


@dataclass
class ExecResult:
    """What you get back after running a command.

    stdout:     the text the command printed (or its error message)
    exit_code:  0 means it worked; any other number means it failed

    @dataclass writes the __init__ for you, so ExecResult("hi", 0) just works.
    """

    stdout: str
    exit_code: int


class BashOperations(Protocol):
    """The job description for anything that can run a shell command.

    To qualify, an object just needs an async exec(command) method that
    returns an ExecResult. It doesn't have to inherit from this class;
    having the right method is enough.

    This is what lets the bash tool stay the same while the place commands
    run changes: today it's LocalOps (your own machine), later it will be
    a sandbox (a throwaway remote machine).
    """

    async def exec(self, command: str) -> ExecResult: ...


def create_bash_tool( operations: BashOperations, safe_prefixes: list[str], approval_prefixes: list[str] | None = None,):
    """Build the bash tool, wired to whichever command runner you pass in.

    The tool itself only handles the decision side:
      1. Check the model's argument.
      2. Refuse blocked commands with a message listing what IS allowed,
         so the model can pick a different command.
      3. Hand the command to operations.exec() to actually run it.
      4. Report the output, prefixed with "Exit N:" if it failed, so the
         model can tell failure apart from empty output.

    The blocked check is a backup. can_use_tool (below) already stops blocked
    commands and asks you about approval ones before this code runs; the
    backup keeps the tool safe if it's ever used without that check.

    The inner bash() function remembers `operations` and the prefix lists
    from this outer function. That's what makes this a factory: call it
    with a different runner and you get the same tool, running commands
    somewhere else. `tool(...)(bash)` is the @tool decorator written as a
    plain call, because bash() is defined inside this function.
    """
    approval_prefixes = approval_prefixes or []
    classify_command = create_classifier(safe_prefixes, approval_prefixes)

    async def bash(args: dict) -> dict:
        inp = BashInput.model_validate(args)

        if classify_command(inp.command) == "blocked":
            text = (
                f'Blocked: "{inp.command}" is not allowed. '
                f"Safe: {', '.join(safe_prefixes)}. Needs approval: {', '.join(approval_prefixes)}."
            )
            return {"content": [{"type": "text", "text": text}]}

        result = await operations.exec(inp.command)
        if result.exit_code != 0:
            text = f"Exit {result.exit_code}: {result.stdout}"
        else:
            text = result.stdout or "(no output)"
        return {"content": [{"type": "text", "text": text}]}

    return tool(
        "bash",
        """Execute a shell command in the working directory.

WHEN TO USE: running build commands, installing packages, running tests,
  git operations, directory listings.

WHEN NOT TO USE: reading file contents (use read instead).
  Searching for patterns (use grep instead).

DO NOT USE FOR: reading files (use read), searching code (use grep).

USAGE: command is a single shell string; pipes, redirects, ; and && are blocked.
  Read-only commands (ls, git status...) run immediately. Commands that change
  files or install packages (mkdir, rm, git commit, bun add...) wait for the
  user's approval. Anything else is blocked with an error. 30s timeout.

EXAMPLES:
  - List files: command "ls -la"
  - Check git status: command "git status"
  - Run the test suite (needs approval): command "bun test\"""",
        BashInput.model_json_schema(),
    )(bash)


class LocalOps:
    """Runs commands on your own machine, inside the project folder.

    This is the only place a shell command actually executes. Success,
    failure and timeout all come back as an ExecResult, so the bash tool
    never has to know how running a command works.

    Details:
      - shell=True: the command arrives as one string ("ls -la src"), so a
        shell has to split it up. The classifier is what makes this safe.
      - cwd=cwd: commands run inside the project folder.
      - timeout=30: a stuck command is killed after 30 seconds and reported
        as exit code 124 (the usual "timed out" code).
      - asyncio.to_thread: subprocess.run blocks until the command finishes,
        so it runs on a side thread while the rest of the agent keeps going.
    """

    async def exec(self, command: str) -> ExecResult:
        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                command,
                shell=True,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            return ExecResult(stdout="timed out after 30s", exit_code=124)
        if proc.returncode != 0:
            return ExecResult(stdout=proc.stdout or proc.stderr, exit_code=proc.returncode)
        return ExecResult(stdout=proc.stdout, exit_code=0)


# The bash tool the agent uses: built with LocalOps, so commands run on
# this machine. Swapping LocalOps() for a sandbox runner is a one-line change.
bash = create_bash_tool(LocalOps(), SAFE_PREFIXES, APPROVAL_PREFIXES)


# Bundle the three tools so the agent can use them. The SDK calls a bundle
# of custom tools an "MCP server", but this one runs inside this script, with
# no network involved. The agent sees each tool as mcp__teensy__<name>.
teensy = create_sdk_mcp_server(name="teensy", version="0.1.0", tools=[read, grep, bash])


async def can_use_tool(
    tool_name: str, input_data: dict, context: ToolPermissionContext
) -> PermissionResultAllow | PermissionResultDeny:
    """The gatekeeper: runs before every tool call that isn't pre-approved.

    read and grep are pre-approved (see allowed_tools below), so in practice
    this only sees bash. For each command:
      safe      -> allow it right away
      blocked   -> refuse it, with a reason the model sees so it can adjust
      approval  -> print the command, wait for you to type y, then allow
                   or refuse
    The agent simply waits while you decide, then carries on.

    input() blocks while it waits for you to type, so it runs on a side
    thread. `context` carries extra details from the SDK; it isn't needed here.
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


# The agent's settings:
#   model          which Claude model does the thinking
#   system_prompt  standing instructions sent with every request
#   cwd            the folder the agent works in
#   mcp_servers    the tool bundle from above
#   allowed_tools  tools that run without asking (read and grep only look,
#                  never change). bash is left off on purpose so every bash
#                  call goes through can_use_tool. Pre-approving a tool does
#                  not hide the SDK's other built-in tools.
#   can_use_tool   the gatekeeper above
#   max_turns      stop after 10 rounds, in case the agent gets stuck in a loop
# Unverified: the SDK may need streaming input (ClaudeSDKClient) for
# can_use_tool to be called.
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
    """Take a question from the command line, run the agent, print the answer.

    query() runs the whole agent loop for you: ask Claude, run whatever
    tools it picks, send back the results, and repeat until it answers.
    The `async for` doesn't drive that loop; it just watches the messages
    the loop produces as they happen:
      - AssistantMessage: something Claude said. Keep the latest text,
        because the last thing it says is the final answer.
      - ResultMessage: the loop is done; num_turns says how many rounds it took.
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


# Run main() only when this file is executed directly, not when another
# file (like ref_server.py) imports it to reuse the tools.
# asyncio.run() starts the async machinery and runs main() to completion.
if __name__ == "__main__":
    asyncio.run(main())
