# TeensyCode: a coding agent harness, twice

Built while working through Vercel Academy's
[Build Your Own AI Coding Agent Harness](https://vercel.com/academy/build-ai-agent-harness).
The same agent is built in two stacks, kept in sync lesson by lesson.

| | `typescript-agent/` | `python-agent/` |
|---|---|---|
| Stack | Bun, AI SDK v7 (`ToolLoopAgent`), Zod | Claude Agent SDK, Pydantic, FastAPI |
| Agent + tools | `index.ts` | `index.py` |
| System prompt builder | `src/system.ts` | `system.py` |
| Web UI server | `server.ts` | `server.py` |
| Prompt tests | `bun test` | `.venv/bin/python test_system.py` |

Both read `ANTHROPIC_API_KEY` from the root `.env` (each app folder has a
git-ignored `.env` symlink to it). `demo/` holds a small fixture for search tests.

## TypeScript

```bash
cd typescript-agent && bun install
bun run index.ts .. "Find all TODO comments"        # CLI
bun run dev                                          # web UI on :3000
```

## Python

```bash
cd python-agent && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
set -a && . ./.env && set +a
.venv/bin/python index.py .. "Find all TODO comments" # CLI
.venv/bin/python server.py .                         # web UI on :3000
```

## Model

The parent agent runs on Sonnet (`claude-sonnet-5-5`); both subagents (explorer
and executor) run on Haiku (`claude-haiku-4-5`). The parent plans, delegates and
follows the Verification contract (Haiku often skips it); the subagents do
narrow, instruction-driven work. Override the parent per run with `MODEL`, e.g.
`MODEL=claude-haiku-4-5 bun run index.ts .. "..."`.

## Sandbox

Where files are read and commands run. `SANDBOX=local` (default) uses your real
folder. The throwaway backend reads your project but never changes it:
`SANDBOX=just-bash` in TypeScript (in-memory filesystem), `SANDBOX=temp-copy`
in Python (a temp copy deleted on exit).

## Approval modes (CLI)

`APPROVAL_MODE=interactive` (default: safe list only), `background` (everything
runs, for CI), or `delegated` with `APPROVAL_TRUST="git status,pwd"`.
The web UIs ask you with Approve/Deny instead.
