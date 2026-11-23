# Post-course TODOs (python-agent)

Work to come back to after finishing the course, for the Python app (Claude
Agent SDK). Each item names the file it touches, how to test it, and what
"done" looks like.

1. Sandbox lifecycle (Module 7)
2. Surfaces: run the same agent everywhere (Slack bot first)
3. Related review items (noted during Modules 5-11)
4. Event bus and extensions (Module 11), including the telemetry extension

---

## 1. Sandbox lifecycle (Module 7)

Module 7 of the course (Sandbox Lifecycle) is concept-only: no lesson ships code.
This file is the plan to return to after the course, for the Python app
(Claude Agent SDK). Each item names the file it touches, how to test it, and
what "done" looks like.

**Where things stand today**

- `sandbox.py`: `Sandbox` Protocol has `expires_at` (always `None`) and an
  optional `snapshot()` (not implemented by any backend). `SandboxLifecycle`
  has `after_start`, `before_stop`, `on_timeout`, all `None` by default.
- `index.py`: `start_sandbox()` runs `after_start`; `stop_sandbox()` runs
  `before_stop` then `stop()`, called from `main()`'s `finally`.
- `server.py`: the sandbox is created once at import and never stopped (no
  shutdown hook).
- Backends: `LocalSandbox` (never expires, nothing to snapshot) and
  `TempCopySandbox` (copy deleted on `stop()`). No cloud backend.

Most items below only become meaningful with a cloud backend. The ones marked
**(local now)** can be built and tested against the local or temp-copy backend
first, which the course recommends: add the gates before the cloud sandbox.

---

### 7.1 State machine

States: `provisioning -> active -> hibernating -> hibernated`, with restore
going back to `active`. Two timers: hard expiry (provider, cannot be extended)
and the inactivity window (ours, default 5 minutes).

- [ ] **(local now)** Add a `state` field to the `Sandbox` Protocol and set it
      in each backend (`local` is always `active`; `temp-copy` goes
      `provisioning` while copying, then `active`, then gone on `stop()`).
- [ ] **(local now)** Add `last_activity_at` and a `record_activity(kind)`
      function that only updates it for real work.
      Counts: `user_message`, `tool_call`, `fs_change`.
      Ignored: status polling, reconnect probes, health checks, billing reads.
- [ ] **(local now)** Call `record_activity("tool_call")` from every tool in
      `tools.py` (read, grep, bash, task) and `record_activity("user_message")`
      when a prompt arrives (`main()` and `server.py` `/api`).
- [ ] `INACTIVITY_WINDOW = 5 * 60` constant; decide where it lives
      (`sandbox.py` or a new `lifecycle.py`).
- [ ] Auto-snapshot at 80% of hard expiry while `active` (the lesson's
      "less-wrong" default).
- [ ] Warn the user before hard expiry (web UI banner + CLI stderr line). Pick
      the threshold (for example 10 minutes left).
- [ ] Show the sandbox state and time-to-expiry in the web UI dashboard.

**Test:** unit-test `record_activity` with each event kind (polling must not
move `last_activity_at`). With a fake clock, check that inactivity triggers
after 5 minutes and that a tool call resets it.

**Done when:** the activity rules are covered by tests, and a status poll
provably never resets the inactivity timer.

---

### 7.2 Snapshot and restore

Snapshot freezes the filesystem and returns an id; restore creates a new
sandbox from it. Running processes and memory are not saved. On some providers
(Vercel Sandbox) the snapshot also shuts the original down.

- [ ] **Hazard 1, snapshot already in progress:** keep the in-flight task and
      return it on a second call.
      ```python
      self._snapshot_task: asyncio.Task | None = None
      async def snapshot(self):
          if self._snapshot_task is None:
              self._snapshot_task = asyncio.create_task(self._do_snapshot())
          try:
              return await self._snapshot_task
          finally:
              self._snapshot_task = None
      ```
- [ ] **Hazard 2, restore while a sandbox is already running:**
      `attach_or_restore(session_id, snapshot_id)` looks for an active sandbox
      first and only restores when none exists.
- [ ] **(local now) Hazard 3, double stop:** a `_stopped` flag so a second
      `stop()` returns immediately. Add it to `LocalSandbox` and
      `TempCopySandbox` (the temp copy already tolerates it through
      `rmtree(..., ignore_errors=True)`, but make it explicit).
- [ ] Re-run `after_start` after every restore (dependencies and env may have
      changed since the snapshot).
- [ ] Decide how to invalidate old snapshots when the project changes (new
      commit, new package, new env var).
- [ ] Optional (course exercise, on a separate branch): a local `snapshot()`
      using `git stash push` that returns the stash ref as the snapshot id.
      Only tracked files are saved, so it is a sketch, not a real snapshot.

**Agent SDK features to evaluate here (not yet verified):**

- [ ] `resume` / `session_id` / `fork_session`: resume or branch the
      conversation from a saved session (agent history, not files).
- [ ] `enable_file_checkpointing`: record file changes so they can be rolled
      back (closest built-in to a filesystem snapshot).
- [ ] `session_store`: where sessions are kept, and whether a restored sandbox
      can be paired with a resumed session.

**Test:** call `snapshot()` twice concurrently and assert one underlying
snapshot ran; call `stop()` twice and assert no error and one close; call
`attach_or_restore` with an active sandbox and assert no new sandbox was
created.

**Done when:** all three hazards have a guard and a test.

---

### 7.3 Durable workflows

A timer inside the process (`asyncio.create_task`, `setTimeout`) disappears
when a serverless function ends or the app redeploys, so the sandbox keeps
running and billing. A durable workflow engine saves its progress between
sleeps and resumes on any instance.

- [ ] Pick the engine for Python: Temporal (Python SDK) is the most direct
      equivalent of the course's Vercel Workflow; also consider a hosted queue
      with delayed jobs. The Agent SDK has no durable-workflow feature.
- [ ] Write the loop: `sleep(30)` -> `check_status` step -> if idle past the
      window: `snapshot` step, `stop` step, end -> if expired: end.
- [ ] Side effects (status check, snapshot, stop) go in retryable steps
      (Temporal activities).
- [ ] Wire it to the hooks: `after_start` starts the workflow for the sandbox
      id; `before_stop` signals it to finish.
- [ ] The workflow calls back through the same `Sandbox` interface
      (`snapshot()`, `stop()`), not provider APIs directly.
- [ ] Until then (long-running process only): `server.py` needs a FastAPI
      shutdown hook that runs `stop_sandbox(sandbox)`, so a temp-copy sandbox
      is deleted when the server exits.

**Test:** run the workflow locally against a fake sandbox with a 1-minute
inactivity window; restart the worker mid-sleep and confirm the workflow
resumes and hibernates the sandbox.

**Done when:** a sandbox is hibernated after the inactivity window even if the
process that started it has exited.

Cost reference from the lesson (example rate $0.02/min, 4h expiry): no
lifecycle $4.80 per session vs inactivity hibernation about $0.50.

---

### 7.4 Hard-won lessons (production gotchas)

Each caused real outages, cost spikes or lost work. Combinations are the
expensive ones (for example a stale handle plus polling-counts-as-activity
means paying for a sandbox you cannot reach). Fix all five.

- [ ] **Stale handle after reconnect:** probe before use; restore if the probe
      fails.
      ```python
      probe = await sandbox.exec("echo probe")
      if probe.exit_code != 0 or probe.stdout.strip() != "probe":
          sandbox = await restore(last_snapshot_id)
      ```
      Hook: `after_start` (or a `reconnect()` path).
- [ ] **Stale expiry data:** fetch `expires_at` fresh from the provider before
      every lifecycle decision; cache it for display only. Never pass a value
      derived from a cached expiry back to a provider API.
      Hook: `before_stop`, `on_timeout`, the auto-snapshot check.
- [ ] **Polling resets inactivity:** only `user_message`, `tool_call`,
      `fs_change` count (see 7.1). Status pings, health checks, reconnect
      probes and billing reads never reset the timer.
      Hook: the tool wrapper / `record_activity`.
- [ ] **Auto-resume loops:** auto-restore only on the initial entry; later
      reconnects attach to the active sandbox; any other state waits or fails.
      Never chain transitions automatically.
      ```python
      if is_initial_entry and sandbox.state == "hibernated":
          await restore(sandbox.snapshot_id)
      ```
      Hook: `after_start` / `attach_or_restore`.
- [ ] **State divergence:** the provider API is the source of truth; our
      database and the browser cache are copies. Everything shown in the UI is
      derived from a fresh provider read.
      Hook: dashboard state endpoint.
- [ ] Optional (course exercise): a `--chaos` flag that injects one failure
      per session (kill a command mid-run, return a stale handle on reconnect,
      force cache/provider divergence, skip a status update). Run the full
      agent loop under it; the first thing that breaks is the missing gate.

**Test:** one test per gotcha that reproduces the failure against a fake
sandbox, then passes with the gate in place. Then the chaos run.

**Done when:** all five gates exist in the lifecycle hooks, each with a test,
and a chaos run completes without a duplicate sandbox, a hang, or unbounded
runtime.

---

## 2. Surfaces: run the same agent everywhere (Slack bot first)

From lesson 10.1, "The CLI is a thin wrapper": almost nothing in the CLI file is
about CLI concerns. The agent, the tools, the prompt and the sandbox are
reusable; the CLI parts are a few lines of argument parsing and a signal
handler. A different surface (web server, Slack bot, VS Code extension) should
change only those few lines, and everything below them stays put.

Goal: test the agent in each surface, Slack bot first, and use that as the
proof that the separation is clean.

### 2.0 Prerequisite: make the surfaces actually thin (do this first)

**Audit against lesson 10.3 (current state):**

- Portable already: everything under `src/` (TS) and the matching Python
  modules is surface-neutral: sandbox + backends, tools, approval, system
  prompt, verification discovery, context policy and telemetry. Surface code
  is confined to its files (streaming/SIGINT in `index.*`; SSE, buttons and
  panels in `server.*` + `public/index.html`); subagent events reach the
  browser through an optional listener, so tools don't know a browser exists.
- Not portable yet:
  1. No single place builds the agent. `index.*` and `server.*` each assemble
     one (TS: two `new ToolLoopAgent({... stopWhen, prepareStep, onStepEnd})`;
     Python: two `ClaudeAgentOptions(...)` with the same model, prompt,
     isolation flags, turns). A Slack bot would be a third copy.
  2. Importing the CLI file has side effects: it parses CLI arguments,
     creates the sandbox, discovers gates and builds the CLI's agent. The web
     server imports it, so its working folder comes from CLI argument parsing
     and it pays for an agent it never uses.
  3. Sandbox lifetime is tied to the CLI process: one sandbox created at
     import, shared by every web request, never stopped by the server. The
     lesson's model is one session per conversation, each with its own
     sandbox and its own cleanup.

**Target design:**

```
src/agent.ts  (python-agent/agent.py)   the only place an agent is assembled
  createSession({ cwd, sandbox: "local" | "just-bash" | "temp-copy", model, approval, hooks })
    -> { sandbox, agent, stop() }
    hooks (all optional):
      onStep(usage)          per-step tokens/cost   (token budget, footers)
      onSubagent(event)      start/tool/usage/end   (Subagents panel, thread lines)
      askApproval(request)   ask a human            (stdin, web buttons, Block Kit)
      onAskUser(question)    show options           (terminal list, buttons)

index.ts   CLI   parse args -> createSession -> agent.stream -> stdout/stderr -> stop on exit/Ctrl-C
server.ts  Web   per conversation -> createSession(hooks -> SSE, askApproval -> buttons) -> stream
slack_bot  Slack per thread -> createSession(hooks -> thread messages, askApproval -> Block Kit) -> stream
```

- [ ] Create `src/agent.ts` / `agent.py` with `createSession` / `create_session`:
      sandbox from the name, lifecycle `after_start`, project facts (git
      branch, AGENTS.md, gates, skills), tool registry (Module 11), system
      prompt, context policy, token budget, and `stop()` that runs
      `before_stop` + `sandbox.stop()`.
- [ ] Move CLI-only code out of module scope in `index.*` (argument parsing,
      SIGINT, rendering) so importing nothing from it is needed by others.
- [ ] Rewrite `server.*` to create one session per conversation (keyed by
      conversation id), stop it when the conversation ends or goes idle, and
      stop all sessions on server shutdown.
- [ ] Each surface ends up about 20-60 lines: input, rendering, how approval
      is asked, when the session starts and stops.
- [ ] Boundary tests: no surface file imports another surface; `agent.*` and
      `src/` have no surface-specific branches; adding the Slack bot changes
      nothing in `agent.*` or `src/`.
- [ ] Re-run the CLI checks and both web servers to confirm behavior is
      unchanged.

### 2.1 Slack bot (priority)

Stack: Slack Bolt for Python (`slack_bolt`) in Socket Mode, so it runs locally
with no public URL. Each Slack thread is one conversation.

**Setup**
- [ ] Create a Slack app (from a manifest), enable Socket Mode.
- [ ] Bot token scopes: `app_mentions:read`, `chat:write`, `im:history`,
      `im:read`, `im:write` (and `channels:history` if it should read channel
      threads).
- [ ] Add `SLACK_BOT_TOKEN` (xoxb-) and `SLACK_APP_TOKEN` (xapp-, Socket Mode)
      to the root `.env`; add `slack_bolt` to `requirements.txt`.

**Behavior**
- [ ] Trigger: `@TeensyCode <question>` in a channel, or a direct message.
- [ ] Reply in the thread: post a "working..." message, then update it
      (`chat.update`) as text streams in, throttled (for example every 1-2 s)
      to stay inside Slack's rate limits.
- [ ] Tool activity: a compact line per tool call in the thread (or one
      updating status message), not every token.
- [ ] Approval: commands off the safe list post Approve / Deny buttons (Block
      Kit). The button handler resolves the waiting `can_use_tool` future,
      the same pattern as `server.py`'s `/api/approve`.
- [ ] `askUser`: show the options as buttons; the click becomes the next
      user message in the thread.
- [ ] Follow-up turns: a reply in the same thread continues the conversation
      (Agent SDK `resume` / `session_id` keyed by `thread_ts`).
- [ ] Subagents and cost: post a short footer per answer (steps, cost,
      subagent runs) from the same telemetry the web dashboard uses.

**Safety (a Slack bot is reachable by other people)**
- [ ] Default to `--sandbox=temp-copy` so nothing in the real folder changes.
- [ ] Never run in background approval mode from Slack.
- [ ] Allowlist which channels and users can trigger it.
- [ ] One sandbox per conversation; stop it when the thread goes idle (this is
      where Section 1's inactivity window becomes real).

**Test plan**
- [ ] Mention in a test channel: read-only question answered in the thread.
- [ ] Ambiguous request: `askUser` buttons appear, clicking one continues.
- [ ] Unsafe command: Approve/Deny buttons; Deny leaves files untouched.
- [ ] Delegation: "Delegate to an explorer..." shows the subagent footer.
- [ ] Two threads at once: separate conversations, separate sandboxes.
- [ ] Restart the bot mid-thread: the next reply resumes (or says it can't).

**Done when:** the Slack bot uses the shared agent module unchanged, and the
only new code is `slack_bot.py` (events, rendering, buttons).

### 2.2 Other surfaces (after Slack)

- [x] CLI: built (Modules 1-10).
- [x] Web: built (`server.py` + `public/index.html`). Gaps from 10.3 are in
      Section 3 (persistence, follow-up turns, resumable streams).
- [ ] VS Code extension (or another editor surface): same agent module, a
      webview for output, the editor's own diff view for changes.
- [ ] For the TypeScript app: Vercel's Chat SDK targets several chat platforms
      (Slack, Teams, Discord, ...) from one codebase; worth evaluating there.

---

## 3. Related review items (noted during Modules 5-11)

- Pruning default `before-last-3-messages` loops on multi-file tasks (TS);
  only 10+ finished in the threshold test. Python has no per-step pruning.
- `addCacheControl` never clears old markers, so breakpoints pile up past
  Anthropic's limit of 4 (TS).
- Python subagents are hand-built `query()` runs; evaluate the Agent SDK's
  native `agents` / `AgentDefinition` instead.
- Executor cannot edit files until a write/edit tool exists; the reviewer role
  (6.4 exercise) depends on it.
- Concurrent executors share one sandbox: add a lock around executor runs, or
  give each executor its own temp-copy sandbox.
- askUser (8.1) is the course's version: it returns "(Awaiting user response.)"
  and the agent ends its turn. The Agent SDK has a built-in `AskUserQuestion`
  tool that pauses and is answered through `can_use_tool`; evaluate switching
  to it (and clickable options in the web UI).
- Module 9 findings:
  - Interactive mode blocks the discovered gates (`npx tsc --noEmit` is not on
    the safe list), so 9.3's "run the gates in order" cannot succeed in the
    default CLI mode. Consider adding discovered gates to the safe list.
  - TS pruning (last 3 messages) makes the agent forget todo ids and re-add
    items. Python (no per-step pruning) did not.
  - `todo complete` works without `start` (course code), so the single-active
    rule only holds if the agent calls `start`.
  - Claude Code has a built-in `TodoWrite` tool (disabled by `tools=[]`);
    evaluate it as the Python production choice.
- Module 10 findings:
  - Python Ctrl-C takes several seconds to finish: cancelling query() waits for
    the SDK to close the Claude Code CLI subprocess and the in-flight request.
    It does stop the sandbox and delete the temp copy. Consider a timeout.
  - 10.1's optional `--session=<id>` (save/load messages to resume a
    conversation): the Agent SDK's `resume` / `session_id` covers this natively.
  - 10.3 web surface gaps in our servers: conversations are kept in memory
    only (lost on restart), each new prompt starts a fresh conversation (no
    follow-up turns), and streams cannot be resumed after closing the tab.
- Module 11 findings:
  - The Agent SDK has native skills (SKILL.md folders loaded through its
    settings); evaluate them against our `skills.py` + `loadSkill`.
  - TS loaded the same skill twice in one run (pruning dropped the first
    result).
  - The lesson's `deploy` example tool was not added (it would let the agent
    run `vercel deploy`); the custom-tool seam was proven with a `now` tool in
    a test script instead.

---

## 4. Event bus and extensions (Module 11)

Lesson 11.3 is concept-only. The detailed plan for the `tool_call` approval
layer (from lesson 8.2) is in `../module_11_todos.md`. This section covers
the rest of the bus, its example extensions, and the telemetry extension.

**Python note:** by the project rule, build on the Agent SDK's native hooks
(`PreToolUse`, `PostToolUse`, `PreCompact`, session start/end) rather than a
hand-built emitter. The TypeScript app gets a small emitter (a
`Map<event, handler[]>` and an `emit` that runs handlers in order).

### 4.1 The bus

- [ ] Five events: `session_start`, `tool_call`, `tool_result`,
      `session_before_compact`, `session_shutdown`.
- [ ] Handler results: nothing (pass through), `{ block: true, reason }`
      (stop the call; the reason goes back to the model as the tool result),
      `{ modify }` (later handlers and the harness see the changed data).
- [ ] Order rule: handlers run in registration order; the first `block`
      stops the chain; each `modify` is visible to the handlers after it.
- [ ] Lifecycle hooks from Module 4 (`after_start`, `before_stop`) become the
      first `session_start` / `session_shutdown` subscribers.
- [ ] Start with a logging extension only; add blocking extensions after the
      bus is proven (the lesson's advice).

### 4.2 The lesson's four example extensions

- [ ] Log every tool call (`tool_call`, pass through).
- [ ] Block writes to protected files such as `.env` (`tool_call`, block).
      We have no `write` tool yet, so first target a `bash` command or a
      `read` of `.env`.
- [ ] Inject a safety instruction before compaction
      (`session_before_compact`, modify). Python: the Agent SDK's `PreCompact`
      hook. TypeScript prunes instead of compacting, so this applies only if
      compaction is added.
- [ ] Auto-commit uncommitted work on shutdown (`session_shutdown`). This is
      the `before_stop` hook from Section 1, generalized.

**Test:** one test per extension, plus a chain test where two `tool_call`
handlers both return `block` and the first reason wins.

### 4.3 Telemetry extension (A/B test agent behavior)

From the lesson: subscribe to `tool_call`, `tool_result` and the per-step
events. Record timestamps, durations and token counts. Append events to a
JSONL file as they happen so a crash doesn't lose the trace. At session end,
generate a one-screen report: total time, tool call counts, slowest tool,
total tokens. Then run the same task twice with different system prompts and
diff the telemetry. Which prompt produced fewer tool calls? Less waste? This
is how you A/B test agent behavior without guessing.

**What we already have to build on:** per-step pricing and session totals
(`telemetry.py` / `src/telemetry.ts`), subagent start/tool/usage/end events,
and the web dashboard. What's missing is a durable trace file, per-tool
durations, and a comparison between runs.

- [ ] **Trace file:** one JSONL file per session, e.g.
      `.teensy/traces/<session-id>.jsonl` (git-ignored). Write and flush one
      line per event as it happens:
      ```json
      {"ts": "...", "event": "tool_call", "tool": "grep", "call_id": "...", "input": {...}}
      {"ts": "...", "event": "tool_result", "tool": "grep", "call_id": "...", "duration_ms": 41, "output_chars": 812, "error": false}
      {"ts": "...", "event": "step", "step": 3, "uncached": 2, "cache_read": 4317, "cache_write": 2659, "output": 417, "cost": 0.0058}
      {"ts": "...", "event": "subagent_end", "role": "explorer", "steps": 2, "cost": 0.0076}
      ```
      Record the session's model, prompt variant id and git commit on the
      first line, so traces can be compared later.
- [ ] **Durations:** time each tool call from `tool_call` to its
      `tool_result` (Python: `PreToolUse` / `PostToolUse` hooks; TS: wrap
      each tool's execute, or use the stream's tool-call / tool-result chunks).
- [ ] **Session-end report** (CLI stderr, and a web panel later):
      total time, steps, tool calls by tool, slowest tool call, total tokens
      (uncached / cached / output), total cost including subagents.
- [ ] **Prompt variants:** a `--prompt-variant <name>` flag (or a prompt file)
      so the same task can run with two system prompts.
- [ ] **Diff script:** `trace_diff.py a.jsonl b.jsonl` prints a side-by-side
      of tool calls, steps, tokens, cost and time, and which run used fewer.
- [ ] **First experiment:** run one task under two prompts (for example with
      and without the 9.2 grep-first bullets) three times each, and compare
      the averages, since a single run is noisy.

**Done when:** a crashed run still leaves a readable trace, every run ends
with the one-screen report, and the diff script answers "which prompt did
less work for the same result."

This is the local precursor to LangSmith: the same events (runs, tool spans,
token usage) become LangSmith traces when the harness moves there.

