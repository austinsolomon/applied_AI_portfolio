// Builds the agent's system prompt from runtime facts instead of a pasted-in
// string. Same context in, same prompt out, no side effects: easy to test,
// and easy to vary per agent (a subagent with fewer tools gets a prompt that
// only lists those tools).

export interface PromptContext {
  workingDirectory: string;
  sandboxType: string; // where commands run: "local" today, a sandbox later
  toolNames: string[]; // the tools actually wired into this agent
  gitBranch?: string; // only when the folder is a git repo
  projectContext?: string; // e.g. the contents of AGENTS.md
}

export function buildSystemPrompt(ctx: PromptContext): string {
  const sections: string[] = [];

  // Role line: who the agent is and where it's working.
  sections.push(`You are a coding agent working in: ${ctx.workingDirectory}`);
  sections.push(`Sandbox: ${ctx.sandboxType}`);

  // Agency: act on the task with tools instead of describing a plan.
  // The two "Prefer"/"Use bash only" lines are kept from lesson 3.1 (the
  // course's 3.2 solution drops them, but its Done-When asks for the same
  // content as before).
  sections.push(`
# Agency
- USE your tools. Read files, search code, run commands, then answer.
- Do NOT explain what you WOULD do. Actually do it.
- Prefer grep for searching, read for viewing files.
- Use bash only for commands that aren't covered by other tools.
- Available tools: ${ctx.toolNames.join(", ")}`);

  if (ctx.gitBranch) {
    sections.push(`- Current branch: ${ctx.gitBranch}`);
  }

  // Guardrails: how to act.
  sections.push(`
# Guardrails
- Prefer simple, minimal changes
- Search before creating, and reuse existing patterns
- No new dependencies without asking`);

  // Handling Ambiguity: models prefer guessing to asking, so the prompt
  // scripts the order: search first (so the question is informed), then ask
  // with askUser, then act. Specific tasks skip straight to acting.
  sections.push(`
# Handling Ambiguity
When the task is ambiguous or has multiple valid approaches:
1. Search the code or docs to gather context first
2. Use askUser to let the user choose. Do NOT guess.
3. Examples: "add auth" -> ask OAuth or JWT; "set up a db" -> ask Postgres or SQLite

Specific tasks (with file paths, line numbers, or precise instructions) do not
need askUser. Act directly.`);

  // Verification: turn "check your work" into a contract. It doesn't make
  // checks pass; it makes the report honest (what ran, what was blocked,
  // what didn't exist). The bad behaviors are named explicitly, because
  // models avoid what you name and fill in what you only imply.
  sections.push(`
# Verification
After making changes, verify your work:
1. Run \`npx tsc --noEmit\` when TypeScript is present
2. Run lint, test, or build commands only if they exist in this project and are allowed by the current approval mode
3. Report exactly what you ran, what was blocked, and what was unavailable
4. Do NOT inflate partial verification into a blanket success claim

Do NOT claim "tests pass" without running them.
Scope your claims honestly. "Verification was limited because writes were blocked" is honest.
"All tests pass" when you didn't run them is not.`);

  if (ctx.projectContext) {
    sections.push(`
# Project Instructions (from AGENTS.md)
${ctx.projectContext}`);
  }

  return sections.join("\n");
}
