// The in-memory backend, built on just-bash: a simulated bash plus a
// copy-on-write filesystem. Reads come from the real project folder; writes
// stay in memory and are thrown away when the sandbox goes. The agent can
// create and delete files freely and your project on disk is never touched.
import { Sandbox as JustBashSandbox } from "just-bash";
import type { Sandbox } from "./sandbox";

// just-bash mounts the project at this virtual path, not at its real path
// and not at "/". Every path passed in must be prefixed with it.
const MOUNT = "/home/user/project";

// Async because just-bash needs a moment to set up its virtual filesystem.
export async function createJustBashSandbox(dir: string): Promise<Sandbox> {
  const jb = await JustBashSandbox.create({ overlayRoot: dir });

  return {
    type: "just-bash",
    workingDirectory: dir,
    readFile: async (p) => jb.readFile(`${MOUNT}/${p}`),
    exec: async (command) => {
      const cmd = await jb.runCommand(command, { cwd: MOUNT });
      const finished = await cmd.wait();
      return {
        stdout: await cmd.output(), // stdout and stderr combined
        exitCode: finished.exitCode,
      };
    },
    // Nothing to clean up: the in-memory filesystem is garbage collected.
    stop: async () => {},
  };
}
