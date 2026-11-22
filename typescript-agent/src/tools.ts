// The agent's tools, each built by a factory that takes a Sandbox. Tools
// only ever call sandbox.readFile / sandbox.exec, never Node's fs or
// child_process, so swapping the backend never touches this file. The
// model-facing contract (description, schema, caps) is unchanged.
import { tool } from "ai";
import { z } from "zod";
import type { Sandbox } from "./sandbox";
import type { RequiresApproval } from "./approval";

const MAX_LINES = 500;
const MAX_MATCHES = 50;
// bash keeps the LAST 5,000 characters: build errors, test failures and
// stack traces print at the end of the output.
const MAX_BASH_CHARS = 5000;

// Wrap a value in single quotes for the shell, escaping any ' inside, so
// grep's pattern/glob/path can't break out of the command string.
const shellQuote = (s: string) => `'${s.replace(/'/g, `'\\''`)}'`;

export function createReadTool(sandbox: Sandbox) {
  return tool({
    description: `Read a file from the project. Returns numbered lines.

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
  - Read lines 40-60 of a file: path "index.ts" offset 40 limit 21`,
    inputSchema: z.object({
      path: z.string().describe("File path relative to working directory"),
      offset: z.number().optional().describe("Start line (1-indexed)"),
      limit: z.number().optional().describe("Max lines to return"),
    }),
    execute: async ({ path: filePath, offset, limit }) => {
      const content = await sandbox.readFile(filePath);
      let lines = content.split("\n");

      if (offset) lines = lines.slice(offset - 1);
      if (limit) lines = lines.slice(0, limit);

      const truncated = lines.length > MAX_LINES;
      if (truncated) lines = lines.slice(0, MAX_LINES);

      const numbered = lines.map((l, i) => `${(offset || 1) + i}: ${l}`);
      return truncated
        ? numbered.join("\n") + `\n... (truncated at ${MAX_LINES} lines)`
        : numbered.join("\n");
    },
  });
}

export function createGrepTool(sandbox: Sandbox) {
  return tool({
    description: `Search file contents using regex. Returns matching lines with file paths.

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
  - Find imports of a package: pattern "from 'express'" glob "*.ts"`,
    inputSchema: z.object({
      pattern: z.string().describe("Regex pattern to search for"),
      path: z.string().optional().describe("Directory to search (default: working dir)"),
      glob: z.string().optional().describe("File glob filter, e.g. '*.ts'"),
    }),
    execute: async ({ pattern, path: searchPath, glob: globFilter }) => {
      // Runs inside the sandbox, so paths are relative to its working directory.
      const cmd =
        `grep -rn --exclude-dir=node_modules --exclude-dir=.venv --exclude-dir=.git ` +
        `--include=${shellQuote(globFilter || "*")} -E ${shellQuote(pattern)} ${shellQuote(searchPath || ".")} 2>/dev/null`;
      const { stdout, exitCode } = await sandbox.exec(cmd);

      // grep exit codes: 0 = matches, 1 = no matches (a normal result),
      // 2+ = a real error such as a bad regex.
      if (exitCode === 1) return "No matches found.";
      if (exitCode > 1) return `grep failed (exit ${exitCode}). Check the regex and path.`;

      const lines = stdout.trim().split("\n").filter(Boolean);
      const truncated = lines.length > MAX_MATCHES;
      const result = truncated ? lines.slice(0, MAX_MATCHES) : lines;
      return truncated
        ? result.join("\n") + `\n... (${lines.length} total, showing first ${MAX_MATCHES})`
        : result.join("\n") || "No matches found.";
    },
  });
}

// The bash tool, built from two injected pieces:
//   sandbox           WHERE commands run
//   requiresApproval  WHETHER a command may run (from createApproval)
// The gate lives inside execute, so a blocked command comes back to the
// model as a plain string it can read and adapt to.
export function createBashTool(sandbox: Sandbox, requiresApproval: RequiresApproval) {
  return tool({
    description: `Execute a shell command in the working directory.

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
  - Run the test suite: command "bun test"`,
    inputSchema: z.object({
      command: z.string().describe("Shell command to execute"),
    }),
    execute: async ({ command }) => {
      if (requiresApproval({ command })) {
        return `Blocked: "${command}" requires approval.`;
      }
      const { stdout, exitCode } = await sandbox.exec(command);
      const output = stdout || "(no output)";
      // Every cap says it was applied, so the model knows output is missing
      // and can narrow the command instead of acting on a partial result.
      const capped =
        output.length > MAX_BASH_CHARS
          ? output.slice(-MAX_BASH_CHARS) + `\n... (truncated, showing last ${MAX_BASH_CHARS} chars)`
          : output;
      return exitCode !== 0 ? `Exit ${exitCode}: ${capped}` : capped;
    },
  });
}
