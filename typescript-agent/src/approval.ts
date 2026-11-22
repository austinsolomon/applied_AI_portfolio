// Approval policy: decides WHETHER a bash command may run.
import { z } from "zod";

// Read-only commands. In interactive mode these run without asking.
export const SAFE_PREFIXES = [
  "ls", "cat", "echo", "pwd", "which", "find",
  "head", "tail", "wc", "git log", "git status", "git diff",
];

// Shell tricks that never pass a prefix match: chaining (; & |), command
// substitution (` $), redirects (< >), and find's -exec/-delete. Without
// this, "ls; rm -rf ~" would pass because it starts with "ls".
const FORBIDDEN = /[;&|`$<>]|\s-(exec|execdir|delete|ok)\b/;

// True if the command is one of the prefixes as a whole word ("ls" and
// "ls -la" match "ls"; "lsof" doesn't) and contains no shell tricks.
// The course uses a bare startsWith, which lets both of those through.
function matchesAny(command: string, prefixes: string[]): boolean {
  const cmd = command.trim();
  if (FORBIDDEN.test(cmd)) return false;
  return prefixes.some((p) => cmd === p || cmd.startsWith(p + " "));
}

// The approval policy as data. "mode" is the discriminant: TypeScript only
// lets you touch `trust` after checking mode === "delegated". Defined as a
// Zod schema so a config loaded from a file or env var gets validated.
//   interactive  local use: safe-list commands run, everything else blocked
//   background   CI / automation: no human to ask, everything runs
//   delegated    subagents: only the commands the parent hands down run
export const ApprovalConfigSchema = z.discriminatedUnion("mode", [
  z.object({ mode: z.literal("interactive") }),
  z.object({ mode: z.literal("background") }),
  z.object({ mode: z.literal("delegated"), trust: z.array(z.string()) }),
]);
export type ApprovalConfig = z.infer<typeof ApprovalConfigSchema>;

// true = needs approval (so it's blocked), false = may run.
export type RequiresApproval = (input: { command: string }) => boolean;

// Turn a config into the yes/no function createBashTool uses.
export function createApproval(config: ApprovalConfig): RequiresApproval {
  return ({ command }) => {
    if (config.mode === "background") return false;
    if (config.mode === "delegated") return !matchesAny(command, config.trust);
    return !matchesAny(command, SAFE_PREFIXES);
  };
}

// Pick the mode for a CLI run, e.g.
//   APPROVAL_MODE=background bun run index.ts . "..."
//   APPROVAL_MODE=delegated APPROVAL_TRUST="git status,pwd" bun run index.ts . "..."
// Defaults to interactive. Bad values fail loudly via the schema.
export function approvalFromEnv(): ApprovalConfig {
  return ApprovalConfigSchema.parse({
    mode: process.env.APPROVAL_MODE ?? "interactive",
    trust: (process.env.APPROVAL_TRUST ?? "").split(",").map((t) => t.trim()).filter(Boolean),
  });
}
