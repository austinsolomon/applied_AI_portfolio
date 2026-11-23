// Verification gates: the check commands this project actually has, in the
// order to run them. Read from package.json through the sandbox.
//   typecheck  first, because it fails fastest ("typecheck"/"type-check"
//              script, else `npx tsc --noEmit` when TypeScript is installed)
//   lint, test next
//   build      last, because it is the slowest
// A missing or unreadable package.json means no gates; the prompt then tells
// the agent its verification is limited.
import type { Sandbox } from "./sandbox";

export async function discoverGates(sandbox: Sandbox): Promise<string[]> {
  try {
    const pkg = JSON.parse(await sandbox.readFile("package.json"));
    const scripts = pkg.scripts ?? {};
    const gates: string[] = [];

    if (scripts.typecheck || scripts["type-check"]) {
      gates.push("npm run typecheck");
    } else if (pkg.devDependencies?.typescript || pkg.dependencies?.typescript) {
      gates.push("npx tsc --noEmit");
    }
    if (scripts.lint) gates.push("npm run lint");
    if (scripts.test) gates.push("npm test");
    if (scripts.build) gates.push("npm run build");

    return gates;
  } catch {
    return [];
  }
}
