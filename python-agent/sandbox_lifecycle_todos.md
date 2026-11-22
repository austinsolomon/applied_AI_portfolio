# Sandbox lifecycle TODOs (Module 7)

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

## 7.1 State machine

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

## 7.2 Snapshot and restore

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

## 7.3 Durable workflows

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

## 7.4 Hard-won lessons (production gotchas)

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

## Related items noted during Modules 5-6 (for the same review)

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
