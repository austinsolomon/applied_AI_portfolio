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
  verificationCommands?: string[]; // the project's check commands, in run order
  skills?: { name: string; description: string }[]; // names + one-line descriptions only
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
- Available tools: ${ctx.toolNames.join(", ")}
- Search before reading. Use grep first, then read only what you'll change.
- Don't read files "just in case." Read what you need when you need it.`);

  if (ctx.gitBranch) {
    sections.push(`- Current branch: ${ctx.gitBranch}`);
  }

  // Guardrails: how to act.
  sections.push(`
# Guardrails
- Prefer simple, minimal changes
- Search before creating, and reuse existing patterns
- No new dependencies without asking`);

  // Skills: one line per skill (name and description). The full content
  // stays on disk until the agent calls loadSkill, so listing skills costs a
  // few tokens per skill instead of their whole text on every call.
  if (ctx.skills?.length) {
    const lines = ctx.skills.map((s) => `- ${s.name}: ${s.description}`).join("\n");
    sections.push(`
# Skills
The following skills are available. Call \`loadSkill\` with the name to get full content.
${lines}`);
  }

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

  // Verification: the project's own check commands, in order, plus the
  // rule that keeps reports honest: say exactly what ran and separate
  // failures the agent caused from failures that were already there.
  const gates = ctx.verificationCommands?.length
    ? ctx.verificationCommands.map((c, i) => `${i + 1}. \`${c}\``).join("\n")
    : "(no verification commands discovered for this project)";
  sections.push(`
# Verification
After making changes, verify your work by running these gates in order:
${gates}

Run each gate, capture the output, and report what passed and what didn't.

Distinguish failures you caused from failures that were already there:
- "Ran tsc: passed."
- "Ran npm test: 47 passed, 3 failed. The 3 failures are pre-existing in user.test.ts and unrelated to my changes."

Do NOT claim "tests pass" without running them. Do NOT inflate partial
verification into a blanket success claim.`);

  if (ctx.projectContext) {
    sections.push(`
# Project Instructions (from AGENTS.md)
${ctx.projectContext}`);
  }

  return sections.join("\n");
}
