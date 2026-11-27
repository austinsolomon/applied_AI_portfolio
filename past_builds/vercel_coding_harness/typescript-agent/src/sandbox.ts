// The Sandbox interface: everything a tool needs from "the place where code
// lives and commands run", and nothing more. Tools call these methods
// instead of Node APIs, so the backend (local disk, in-memory filesystem,
// cloud VM) can change without touching any tool.
//
// Every method is async, even where the local backend is synchronous under
// the hood, because the cloud backend really is async and one signature for
// all backends keeps the tools simple.
//
// Keep it small: anything added here, every backend must support forever.

export interface Sandbox {
  // Identity: which backend this is (shown in logs and the system prompt),
  // and the base path tools resolve relative paths against.
  type: string;
  workingDirectory: string;

  // Required capabilities.
  readFile(path: string): Promise<string>;
  exec(command: string): Promise<{ stdout: string; exitCode: number }>;
  stop(): Promise<void>; // shut down; a no-op is fine

  // Optional capabilities: only some backends have them (a local sandbox
  // never expires; an in-memory one can't snapshot). Optional members mean
  // backends without them don't need stubs.
  expiresAt?: number;
  snapshot?(): Promise<{ snapshotId: string }>;
}

// Lifecycle hooks: optional steps that run around a sandbox. Each takes the
// sandbox and returns when done. Empty for local; a cloud sandbox would use
// all three.
//   afterStart  right after the sandbox is ready: setup such as configuring
//               git, installing dependencies, copying .env
//   beforeStop  right before shutdown: save anything important, e.g. commit
//               uncommitted work or take a snapshot
//   onTimeout   called by the backend itself when its time limit is reached
//               (cloud only; wired up in Module 7)
export interface SandboxLifecycle {
  afterStart?(sandbox: Sandbox): Promise<void>;
  beforeStop?(sandbox: Sandbox): Promise<void>;
  onTimeout?(sandbox: Sandbox): Promise<void>;
}
