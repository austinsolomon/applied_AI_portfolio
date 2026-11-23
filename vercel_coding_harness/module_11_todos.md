# Module 11 TODOs: event layer for approval (from lesson 8.2)

Lesson 8.2 (Approval Config) is concept-only. It splits approval into two
layers and defers the second one to Module 11's event bus. Build this when
Module 11 starts, in both apps.

## The two layers

| Layer | Answers | Set by | Status |
| --- | --- | --- | --- |
| Config (`ApprovalConfig`: interactive / background / delegated) | Who decides? | Whoever runs the harness (developer, CI, parent agent) | Built (lesson 2.3) |
| Events (`tool_call` interceptors) | Which policies apply? | The project (`.env` is protected, build dir is read-only, ...) | **To build in Module 11** |

The event fires after the config decision and before the tool runs: defense in
depth. Example: interactive mode lets the human approve a command, and a
`tool_call` rule still blocks any write to `.env`, in every mode.

## Steps (from the lesson)

1. [ ] Build a small typed event emitter in the harness (Module 11's event bus).
2. [ ] Emit a `tool_call` event from the agent loop before each tool runs, with
       `toolName` and `input`.
3. [ ] Let subscribers return `{ block: true, reason }`, modify the input, or
       pass through.
4. [ ] Wire one subscriber that blocks writes to a hardcoded file as a smoke
       test.

Sketch from the lesson:

```ts
harness.on("tool_call", async (event) => {
  const { toolName, input } = event;
  if (toolName === "write" && input.path.endsWith(".env")) {
    return { block: true, reason: "Cannot modify .env files" };
  }
  if (toolName === "bash") {
    event.input.command = `sandbox-exec -p '(deny default)' ${input.command}`;
  }
  return { block: false };
});
```

## Which layer fits which case

| Use case | Layer |
| --- | --- |
| CI run, auto-approve everything | Config (`background`) |
| Subagent inheriting trust from parent | Config (`delegated`) |
| Block writes to specific files | Events (file-level rule) |
| Wrap commands in an OS-level sandbox | Events (modifies the input) |
| Project-specific safety rules | Events (per-project extension) |

## Notes for our two apps

- **Smoke test target:** the lesson's example blocks `write` to `.env`, but we
  have no `write` tool yet. Until one exists, block a `bash` command pattern
  (for example anything touching `.env`) or a `read` of `.env`.
- **Python (Claude Agent SDK):** the SDK already has a native event layer:
  hooks (for example a `PreToolUse` hook that can deny a call or change its
  input). By the project rule (use the Agent SDK's native feature where one
  exists), that is the Python implementation, rather than a hand-built
  emitter. Verify the hook API against the installed `claude-agent-sdk`.
- **Already close:** `python-agent/server.py`'s `can_use_tool` runs on every
  tool call, after the mode decision and before the tool runs, and can deny.
  It is a single built-in interceptor, not a subscribable bus, and cannot
  modify input yet.
- **Where TS emits it:** in the AI SDK loop, the natural seam is a wrapper
  around each tool's `execute` (or the agent's per-step hooks), so every tool
  call passes through `harness.emit("tool_call", ...)` first.

## Optional exercise from 8.2: risk-scored auto-approval

`riskScore(command)` from 0 to 100 (disk write +30, network +20, delete +50,
config change +40, read-only 0). Below a threshold (for example 40) auto-
approve, above it ask the user; log every auto-approval with its score; add a
`--risk-threshold` flag. Open question from the lesson: score `rm -rf /tmp/test`
differently from `rm -rf /` without relying only on keywords.
