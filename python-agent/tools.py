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

from claude_agent_sdk import tool
from pydantic import BaseModel, Field

from approval import RequiresApproval
from sandbox import Sandbox

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
