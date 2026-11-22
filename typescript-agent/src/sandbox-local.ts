// The local backend: the same readFileSync / execSync calls as before, now
// hidden behind the Sandbox interface. Deliberately boring. It proves the
// interface works and is the baseline every other backend is compared to.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { execSync } from "node:child_process";
import type { Sandbox } from "./sandbox";

export function createLocalSandbox(dir: string): Sandbox {
  return {
    type: "local",
    workingDirectory: dir,
    readFile: async (p) => readFileSync(resolve(dir, p), "utf-8"),
    // exec never throws: a failing command comes back as a result with a
    // non-zero exitCode, because tools expect a result object, not an error.
    exec: async (command) => {
      try {
        const stdout = execSync(command, { cwd: dir, encoding: "utf-8", timeout: 30_000 });
        return { stdout, exitCode: 0 };
      } catch (e: any) {
        return {
          stdout: e.stdout || e.stderr || e.message || "",
          exitCode: e.status ?? 1,
        };
      }
    },
    // Nothing to clean up: the disk and child_process outlive the agent.
    stop: async () => {},
  };
}
