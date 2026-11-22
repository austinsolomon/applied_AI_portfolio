"""Builds the agent's system prompt from runtime facts.

Instead of one pasted-in string, the prompt is put together by a function
from a PromptContext. Same context in, same prompt out, no side effects, so
it's easy to test and easy to vary per agent: a subagent that only gets
read and grep gets a prompt that only lists read and grep.
"""

from dataclasses import dataclass, field


@dataclass
class PromptContext:
    """The runtime facts the prompt depends on.

    working_directory  the folder the agent works in
    sandbox_type       where commands run: "local" today, a sandbox later
    tool_names         the tools actually wired into this agent
    git_branch         the current branch, or None if it's not a git repo
    project_context    project instructions (e.g. AGENTS.md), or None
    verification_commands  the project's check commands, in run order

    The last two default to None, so they can be left out entirely.
    `field(default_factory=list)` is how a dataclass gives each instance its
    own fresh empty list (a plain `= []` default would be shared).
    """

    working_directory: str
    sandbox_type: str
    tool_names: list[str] = field(default_factory=list)
    git_branch: str | None = None
    project_context: str | None = None
    verification_commands: list[str] = field(default_factory=list)


def build_system_prompt(ctx: PromptContext) -> str:
    """Put the system prompt together, section by section.

    Each section is appended to a list, and the list is joined with newlines
    at the end: plain string building, no template engine. The optional
    sections (current branch, project instructions) are only appended when
    their value is set.

      role line    who the agent is, where it's working, where commands run
      Agency       act with the tools instead of describing a plan, and
                   which tools exist
      Guardrails   small changes, reuse what exists, ask before new deps
      Ambiguity    models prefer guessing to asking, so the order is scripted:
                   search first (so the question is informed), then ask with
                   askUser, then act. Specific tasks skip straight to acting.
      Verification the project's own check commands in run order, and the
                   rule that keeps reports honest: say exactly what ran and
                   separate failures the agent caused from ones that were
                   already there.
      Project      extra instructions for this specific project, if any
    """
    sections: list[str] = []

    sections.append(f"You are a coding agent working in: {ctx.working_directory}")
    sections.append(f"Sandbox: {ctx.sandbox_type}")

    sections.append(f"""
# Agency
- USE your tools. Read files, search code, run commands, then answer.
- Do NOT explain what you WOULD do. Actually do it.
- Prefer grep for searching, read for viewing files.
- Use bash only for commands that aren't covered by other tools.
- Available tools: {", ".join(ctx.tool_names)}
- Search before reading. Use grep first, then read only what you'll change.
- Don't read files "just in case." Read what you need when you need it.""")

    if ctx.git_branch:
        sections.append(f"- Current branch: {ctx.git_branch}")

    sections.append("""
# Guardrails
- Prefer simple, minimal changes
- Search before creating, and reuse existing patterns
- No new dependencies without asking""")

    sections.append("""
# Handling Ambiguity
When the task is ambiguous or has multiple valid approaches:
1. Search the code or docs to gather context first
2. Use askUser to let the user choose. Do NOT guess.
3. Examples: "add auth" -> ask OAuth or JWT; "set up a db" -> ask Postgres or SQLite

Specific tasks (with file paths, line numbers, or precise instructions) do not
need askUser. Act directly.""")

    gates = (
        "\n".join(f"{i + 1}. `{c}`" for i, c in enumerate(ctx.verification_commands))
        if ctx.verification_commands
        else "(no verification commands discovered for this project)"
    )
    sections.append(f"""
# Verification
After making changes, verify your work by running these gates in order:
{gates}

Run each gate, capture the output, and report what passed and what didn't.

Distinguish failures you caused from failures that were already there:
- "Ran tsc: passed."
- "Ran npm test: 47 passed, 3 failed. The 3 failures are pre-existing in user.test.ts and unrelated to my changes."

Do NOT claim "tests pass" without running them. Do NOT inflate partial
verification into a blanket success claim.""")

    if ctx.project_context:
        sections.append(f"""
# Project Instructions (from AGENTS.md)
{ctx.project_context}""")

    return "\n".join(sections)
