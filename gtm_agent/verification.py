"""Verification gates: the check commands this project actually has, in run order.

Read from package.json through the sandbox:
  typecheck  first, because it fails fastest ("typecheck"/"type-check" script,
             else `npx tsc --noEmit` when TypeScript is installed)
  lint, test next
  build      last, because it is the slowest
A missing or unreadable package.json means no gates; the prompt then tells the
agent its verification is limited.
"""

import json

from sandbox import Sandbox


async def discover_gates(sandbox: Sandbox) -> list[str]:
    """Return the project's verification commands, or [] if there is no package.json."""
    try:
        pkg = json.loads(await sandbox.read_file("package.json"))
    except (OSError, ValueError):
        return []
    scripts = pkg.get("scripts", {})
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    gates: list[str] = []

    if "typecheck" in scripts or "type-check" in scripts:
        gates.append("npm run typecheck")
    elif "typescript" in deps:
        gates.append("npx tsc --noEmit")
    if "lint" in scripts:
        gates.append("npm run lint")
    if "test" in scripts:
        gates.append("npm test")
    if "build" in scripts:
        gates.append("npm run build")
    return gates
