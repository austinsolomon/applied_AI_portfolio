"""The agent's tools, each built by a factory that takes a Sandbox.

Tools only ever call sandbox.read_file and sandbox.exec, never Python's file
or subprocess functions directly, so swapping the backend never touches this
file. What the model sees (descriptions, schemas, output caps) is unchanged.

Each factory defines the tool function inside itself, so the function
remembers the sandbox it was given. `tool(name, description, schema)(fn)` is
the @tool decorator written as a plain call, which is needed because the
function is created inside the factory.
"""

import shlex
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Literal

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
    tool,
)
from pydantic import BaseModel, Field

from approval import Delegated, RequiresApproval, create_approval
from sandbox import Sandbox
from telemetry import TokenBudget

# Output caps. Tool output goes straight into the model's context, and huge
# outputs waste tokens and bury the useful part, so long results are cut off
# with a note saying so.
MAX_LINES = 500
MAX_MATCHES = 50
# bash keeps the LAST 5,000 characters: build errors, test failures and stack
# traces print at the end of the output.
MAX_BASH_CHARS = 5000


def text_result(text: str) -> dict:
    """Wrap text in the content-block format the SDK expects from a tool."""
    return {"content": [{"type": "text", "text": text}]}


class ReadInput(BaseModel):
    """The arguments the model must send when it calls the read tool.

    Pydantic does two jobs here. model_json_schema() turns this class into
    the schema the model is shown, and model_validate() checks what the model
    actually sent. `int | None = None` means the argument is optional.
    """

    path: str = Field(description="File path relative to working directory")
    offset: int | None = Field(default=None, description="Start line (1-indexed)")
    limit: int | None = Field(default=None, description="Max lines to return")


READ_DESCRIPTION = """Read a file from the project. Returns numbered lines.

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
  - Read lines 40-60 of a file: path "index.ts" offset 40 limit 21"""


def create_read_tool(sandbox: Sandbox):
    """Build the read tool: open one file through the sandbox, return numbered lines.

    1. Check the model's arguments against ReadInput.
    2. Ask the sandbox for the file's text.
    3. If offset/limit were given, keep only that slice of lines.
    4. Cut off anything past MAX_LINES and say so.
    5. Number each line ("12: const x = 1") so the model can refer to them.
    """

    async def read(args: dict) -> dict:
        inp = ReadInput.model_validate(args)
        lines = (await sandbox.read_file(inp.path)).split("\n")

        if inp.offset:
            lines = lines[inp.offset - 1:]
        if inp.limit:
            lines = lines[:inp.limit]

        truncated = len(lines) > MAX_LINES
        if truncated:
            lines = lines[:MAX_LINES]

        numbered = [f"{(inp.offset or 1) + i}: {line}" for i, line in enumerate(lines)]
        text = "\n".join(numbered)
        if truncated:
            text += f"\n... (truncated at {MAX_LINES} lines)"
        return text_result(text)

    return tool("read", READ_DESCRIPTION, ReadInput.model_json_schema())(read)


class GrepInput(BaseModel):
    """The arguments for the grep tool: what to search for, and where.

    Only `pattern` is required. `path` narrows the search to a subfolder,
    and `glob` narrows it to certain file types, such as "*.ts".
    """

    pattern: str = Field(description="Regex pattern to search for")
    path: str | None = Field(default=None, description="Directory to search (default: working dir)")
    glob: str | None = Field(default=None, description="File glob filter, e.g. '*.ts'")


GREP_DESCRIPTION = """Search file contents using regex. Returns matching lines with file paths.

WHEN TO USE: finding patterns across multiple files, locating function definitions,
  searching for imports, finding TODOs or error messages.

WHEN NOT TO USE: reading a known file (use read instead).
  Running commands (use bash instead).

DO NOT USE FOR: reading files (use read), listing directories (use bash).

USAGE: pattern is an extended regex string. glob filters by file name
  (default: all files). node_modules, .venv and .git are skipped.
  Results are capped at 50 matches.

EXAMPLES:
  - Find all TODO comments: pattern "TODO" glob "*.ts"
  - Find function definitions: pattern "function \\w+" glob "*.ts"
  - Find imports of a package: pattern "from 'express'" glob "*.ts\""""


def create_grep_tool(sandbox: Sandbox):
    """Build the grep tool: search every file under a folder, inside the sandbox.

    The search is a `grep -rn` command run through sandbox.exec:
      -r  search subfolders too       -n  include line numbers
      --exclude-dir  skip node_modules, .venv and .git (huge, never useful)
      --include      only files matching the glob
      -E             extended regex syntax
    Since it now runs as one shell string, shlex.quote wraps the pattern,
    glob and path in safe quotes so odd characters can't break the command.

    grep's exit code says what happened: 0 = matches, 1 = no matches (a
    normal result), 2 or more = a real error such as a bad regex. More than
    MAX_MATCHES results are cut off, with the total count reported.
    """

    async def grep(args: dict) -> dict:
        inp = GrepInput.model_validate(args)
        command = (
            "grep -rn --exclude-dir=node_modules --exclude-dir=.venv --exclude-dir=.git "
            f"--include={shlex.quote(inp.glob or '*')} -E {shlex.quote(inp.pattern)} "
            f"{shlex.quote(inp.path or '.')} 2>/dev/null"
        )
        result = await sandbox.exec(command)

        if result.exit_code == 1:
            return text_result("No matches found.")
        if result.exit_code > 1:
            return text_result(f"grep failed (exit {result.exit_code}). Check the regex and path.")

        lines = [line for line in result.stdout.strip().split("\n") if line]
        if not lines:
            text = "No matches found."
        elif len(lines) > MAX_MATCHES:
            text = "\n".join(lines[:MAX_MATCHES]) + f"\n... ({len(lines)} total, showing first {MAX_MATCHES})"
        else:
            text = "\n".join(lines)
        return text_result(text)

    return tool("grep", GREP_DESCRIPTION, GrepInput.model_json_schema())(grep)


class BashInput(BaseModel):
    """The single argument for the bash tool: the command to run, as one string."""

    command: str = Field(description="Shell command to execute")


BASH_DESCRIPTION = """Execute a shell command in the working directory.

WHEN TO USE: running build commands, installing packages, running tests,
  git operations, directory listings.

WHEN NOT TO USE: reading file contents (use read instead).
  Searching for patterns (use grep instead).

DO NOT USE FOR: reading files (use read), searching code (use grep).

USAGE: command is a single shell string. Commands not approved by the
  approval policy are blocked and return a clear error message. 30s timeout.
  Output is capped at the last 5000 characters.

EXAMPLES:
  - List files: command "ls -la"
  - Check git status: command "git status"
  - Run the test suite: command "bun test\""""


def create_bash_tool(sandbox: Sandbox, requires_approval: RequiresApproval):
    """Build the bash tool from two pieces you pass in.

      sandbox            WHERE commands run
      requires_approval  WHETHER a command may run (from create_approval)

    When the model calls bash:
      1. Check the model's argument.
      2. Ask requires_approval. If it says True, return a "Blocked" message
         instead of running anything; the model reads it and adapts.
      3. Otherwise run the command with sandbox.exec().
      4. Cap the output at its last MAX_BASH_CHARS characters, with a note
         saying so, so the model knows output is missing and can narrow the
         command instead of acting on a partial result.
      5. Report it, prefixed with "Exit N:" if it failed, so the model can
         tell failure apart from empty output.
    """

    async def bash(args: dict) -> dict:
        inp = BashInput.model_validate(args)

        if requires_approval(inp.command):
            return text_result(f'Blocked: "{inp.command}" requires approval.')

        result = await sandbox.exec(inp.command)
        output = result.stdout or "(no output)"
        if len(output) > MAX_BASH_CHARS:
            output = output[-MAX_BASH_CHARS:] + f"\n... (truncated, showing last {MAX_BASH_CHARS} chars)"
        if result.exit_code != 0:
            return text_result(f"Exit {result.exit_code}: {output}")
        return text_result(output)

    return tool("bash", BASH_DESCRIPTION, BashInput.model_json_schema())(bash)


class AskUserInput(BaseModel):
    """The arguments for askUser: one question and two to four answer options."""

    question: str = Field(description="The question to ask the user")
    options: list[str] = Field(min_length=2, max_length=4, description="Two to four options for the user to pick from")


ASK_USER_DESCRIPTION = """Ask the user a multiple-choice question.
WHEN TO USE: scoping ambiguous tasks, choosing between approaches,
  resolving a missing detail before acting.
WHEN NOT TO USE: you already have enough context to proceed.
DO NOT USE FOR: rhetorical questions or progress updates."""


def create_ask_user_tool():
    """Build askUser: a multiple-choice question for the user.

    It does not wait for an answer. It prints the question (so the user sees
    it) and returns the same text (so the model sees it in its history).
    "(Awaiting user response.)" tells the model the question is still open,
    so it ends its turn and the user answers in the next message. The system
    prompt's "Handling Ambiguity" section decides when the model uses it.
    The tool is registered as "askUser" because the prompt refers to it by
    that name.
    """

    async def ask_user(args: dict) -> dict:
        inp = AskUserInput.model_validate(args)
        formatted = "\n".join(f"{i + 1}. {option}" for i, option in enumerate(inp.options))
        print(f"\nQuestion: {inp.question}\n{formatted}\n", flush=True)
        return text_result(f'Asked: "{inp.question}"\nOptions:\n{formatted}\n\n(Awaiting user response.)')

    return tool("askUser", ASK_USER_DESCRIPTION, AskUserInput.model_json_schema())(ask_user)


class TodoInput(BaseModel):
    """The arguments for todo: an action, plus a description (add) or an id (start/complete)."""

    action: Literal["add", "start", "complete", "list"]
    description: str | None = None
    id: str | None = None


TODO_DESCRIPTION = """Manage a task list for multi-step work.
WHEN TO USE: tasks with 3+ steps, multiple files, or dependencies between
  changes. Plan once, then track progress as you go.
WHEN NOT TO USE: single-file fixes, simple questions, exploratory reads.
DO NOT USE FOR: status updates to the user (just answer them directly)."""


def create_todo_tool():
    """Build todo: a task list for multi-step work.

    Items move pending -> in_progress -> completed, and only ONE item may be
    in progress at a time: `start` is refused while another item is active,
    which keeps the agent finishing one thing before starting the next. The
    list lives inside this tool, so each agent run (each call to this
    function) starts with an empty list.
    """
    todos: list[dict] = []

    async def todo(args: dict) -> dict:
        inp = TodoInput.model_validate(args)
        if inp.action == "add":
            item = {"id": uuid.uuid4().hex[:8], "description": inp.description or "(unnamed)", "state": "pending"}
            todos.append(item)
            return text_result(f"Added: [{item['id']}] {item['description']}")
        if inp.action == "start":
            active = next((t for t in todos if t["state"] == "in_progress"), None)
            if active:
                return text_result(f"Already working on: [{active['id']}] {active['description']}. Complete it first.")
            item = next((t for t in todos if t["id"] == inp.id), None)
            if not item:
                return text_result(f"No todo with id {inp.id}.")
            item["state"] = "in_progress"
            return text_result(f"Started: [{item['id']}] {item['description']}")
        if inp.action == "complete":
            item = next((t for t in todos if t["id"] == inp.id), None)
            if not item:
                return text_result(f"No todo with id {inp.id}.")
            item["state"] = "completed"
            return text_result(f"Completed: [{item['id']}] {item['description']}")
        return text_result("\n".join(f"[{t['state']}] {t['id']}: {t['description']}" for t in todos) or "No todos.")

    return tool("todo", TODO_DESCRIPTION, TodoInput.model_json_schema())(todo)


# ---- Subagents (Module 6) ----
#
# The parent agent delegates through one `task` tool. Each call starts a
# fresh subagent (its own query() run, with its own context window), so the
# parent receives the subagent's final answer, not every file it read on the
# way. Each role fixes its own tools, model and turn budget. Neither role
# can ask the user anything; that stays with the parent.

# Explorer: read-only research. Haiku because exploration is reading and
# summarizing; 5 turns, so larger questions get split into several tasks.
EXPLORER_MODEL = "claude-haiku-4-5"
EXPLORER_TURNS = 5

# Executor: implementation, on Haiku like the explorer. The parent (Sonnet)
# does the planning and hands over precise instructions, so the executor
# only has to carry them out; 15 turns. Its bash runs in delegated mode: only the
# commands on the trust list run, everything else is blocked. It gets its
# own bash rather than the parent's, because no human answers its prompts.
EXECUTOR_MODEL = "claude-haiku-4-5"
EXECUTOR_TURNS = 15
EXECUTOR_TRUST = ["npm test", "npm run build", "npx tsc"]


def subagent_options(sandbox: Sandbox, name: str, model: str, prompt: str, tools: list, turns: int) -> ClaudeAgentOptions:
    """Agent settings for one subagent run.

    The subagent's tools are bundled into their own in-process MCP server
    (named after the role) and pre-approved; the approval policy lives
    inside the bash tool itself. tools=[], setting_sources=[] and
    strict_mcp_config=True keep the SDK's built-in tools and your local
    Claude Code setup out, exactly as for the parent.
    """
    server = create_sdk_mcp_server(name=name, version="0.1.0", tools=tools)
    return ClaudeAgentOptions(
        model=model,
        system_prompt=prompt,
        cwd=sandbox.working_directory,
        tools=[],
        setting_sources=[],
        strict_mcp_config=True,
        mcp_servers={name: server},
        allowed_tools=[f"mcp__{name}__{t.name}" for t in tools],
        max_turns=turns,
        include_partial_messages=True,
    )


def build_explorer(sandbox: Sandbox, read_tool, grep_tool) -> ClaudeAgentOptions:
    """Explorer settings: read and grep only, Haiku, 5 turns."""
    prompt = (
        "You are an explorer agent. Investigate and report back concisely.\n"
        f"Working directory: {sandbox.working_directory}"
    )
    return subagent_options(sandbox, "explorer", EXPLORER_MODEL, prompt, [read_tool, grep_tool], EXPLORER_TURNS)


def build_executor(sandbox: Sandbox, read_tool, grep_tool) -> ClaudeAgentOptions:
    """Executor settings: read, grep and a delegated-mode bash, Sonnet, 15 turns."""
    executor_bash = create_bash_tool(sandbox, create_approval(Delegated(trust=EXECUTOR_TRUST)))
    prompt = (
        "You are an executor agent. Follow instructions precisely.\n"
        f"Working directory: {sandbox.working_directory}\n"
        "Do NOT ask questions. Do NOT explore beyond what's needed. Execute the task."
    )
    return subagent_options(
        sandbox, "executor", EXECUTOR_MODEL, prompt, [read_tool, grep_tool, executor_bash], EXECUTOR_TURNS
    )


# A listener for subagent events (the web UI's Subagents panel). Each event
# is a dict with a "kind":
#   start  the run began: role, model, the parent's instructions
#   tool   one tool call finished: name, input, start of the output
#   usage  one step finished: its cost (priced like the parent's steps)
#   end    the run finished: status, steps, total cost, start of the answer
SubagentListener = Callable[[dict], Awaitable[None]]


def result_text(block: ToolResultBlock) -> str:
    """The plain text of a tool result (a string, or a list of text blocks)."""
    content = block.content
    if isinstance(content, list):
        content = "\n".join(part.get("text", "") for part in content if isinstance(part, dict))
    return str(content or "")


async def run_subagent(
    role: str, model: str, options: ClaudeAgentOptions, description: str, on_event: SubagentListener | None = None
) -> str:
    """Run one subagent and format its answer as "[role: N steps]".

    Keeps the subagent's latest text, since its last message is its answer.
    Errors come back as text, because the parent expects a string from every
    tool and an exception would break its loop. If the subagent hits its
    turn limit (the SDK raises an error), whatever it had written so far is
    still returned.

    While it runs, each step is priced with the role's model (TokenBudget)
    and every tool call and step is reported to on_event, if given. Tool
    calls are remembered by id until their result arrives, so each "tool"
    event carries both the input and the output. The stderr line shows what
    happened inside the subagent, which the parent's own trace never shows.
    """
    run_id, started = str(uuid.uuid4()), time.monotonic()
    budget = TokenBudget(model)
    calls: dict[str, ToolUseBlock] = {}
    text, turns, status = "", 0, "done"

    async def emit(event: dict) -> None:
        if on_event:
            await on_event({"id": run_id, **event})

    await emit({"kind": "start", "role": role, "model": model, "description": description})
    try:
        async for message in query(prompt=description, options=options):
            if isinstance(message, StreamEvent) and message.event.get("type") == "message_delta":
                step = budget.record(message.event.get("usage", {}))
                await emit({"kind": "usage", "step": step.step, "cost": step.cost, "costWithout": step.cost_without})
            elif isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        text = block.text
                    elif isinstance(block, ToolUseBlock):
                        calls[block.id] = block
            elif isinstance(message, UserMessage) and isinstance(message.content, list):
                for block in message.content:
                    if isinstance(block, ToolResultBlock) and block.tool_use_id in calls:
                        use = calls.pop(block.tool_use_id)
                        await emit({
                            "kind": "tool", "toolName": use.name.split("__")[-1],
                            "input": use.input, "output": result_text(block)[:300],
                        })
            elif isinstance(message, ResultMessage):
                turns = message.num_turns
                # The CLI reports some failures (e.g. an invalid API key) as
                # ordinary text with is_error set, not as an exception.
                if message.is_error:
                    status, text = "error", f"{role} error: {text or message.subtype}"
    except Exception as error:
        if not text:
            status, text = "error", f"{role} error: {error}"
        else:
            text += f"\n(stopped early: {error})"
    cost = budget.total_cost()
    cost_without = sum(s.cost_without or 0 for s in budget.steps) if cost is not None else None
    await emit({
        "kind": "end", "status": status, "steps": turns or len(budget.steps), "cost": cost,
        "costWithout": cost_without, "durationMs": int((time.monotonic() - started) * 1000), "answer": text[:400],
    })
    detail = text if status == "error" else f"answer {len(text)} chars"
    print(f"[{role}] {status} · {turns or len(budget.steps)} steps · cost ${cost or 0:.4f} · {detail}", file=sys.stderr, flush=True)
    if status == "error":
        return text
    return f"[{role}: {turns} steps]\n{text}" if text else f"(no response from {role})"


class TaskInput(BaseModel):
    """The arguments for the task tool: what to do, and which role does it."""

    description: str = Field(description="Task instructions for the subagent")
    subagent_type: Literal["explorer", "executor"] = Field(default="explorer", description="Subagent role")


TASK_DESCRIPTION = """Delegate work to a subagent.
Explorer (default): read-only research with Haiku. Use for searching across files,
  understanding patterns, and gathering context.
Executor: implementation with Haiku and delegated bash. Use for focused
  changes with explicit instructions and a known verification step.

WHEN TO USE: research across many files (explorer), bulk implementation (executor).
WHEN NOT TO USE: ambiguous requirements (use askUser), architectural decisions
  (the parent decides).
DO NOT USE FOR: single-step tasks the parent can do directly."""


def create_task_tool(sandbox: Sandbox, read_tool, grep_tool, on_event: SubagentListener | None = None):
    """Build the task tool: a router that picks the role, builds it, runs it.

    The subagents reuse the parent's read and grep tools, which already
    point at the parent's sandbox. Adding a role is one build_* function
    and one branch here. on_event is optional: pass a listener to watch
    subagent runs live (the web UI does).

    Spawn permissions (not enforced yet): once subagents can call `task`
    themselves, a map such as
        {"orchestrator": ["explorer", "executor"], "executor": ["explorer"], "explorer": []}
    limits which roles each role may start, checked at the top of task().
    """

    async def task(args: dict) -> dict:
        inp = TaskInput.model_validate(args)
        if inp.subagent_type == "executor":
            options, model = build_executor(sandbox, read_tool, grep_tool), EXECUTOR_MODEL
        else:
            options, model = build_explorer(sandbox, read_tool, grep_tool), EXPLORER_MODEL
        return text_result(await run_subagent(inp.subagent_type, model, options, inp.description, on_event))

    return tool("task", TASK_DESCRIPTION, TaskInput.model_json_schema())(task)
