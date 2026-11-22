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

    The last two default to None, so they can be left out entirely.
    `field(default_factory=list)` is how a dataclass gives each instance its
    own fresh empty list (a plain `= []` default would be shared).
    """

    working_directory: str
    sandbox_type: str
    tool_names: list[str] = field(default_factory=list)
    git_branch: str | None = None
    project_context: str | None = None


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
      Verification run the checks that exist, never claim a check you didn't
                   run, and report what ran / was blocked / wasn't available.
                   It doesn't make checks pass; it makes the report honest.
                   The bad behaviors are named outright, because models avoid
                   what you name and fill in what you only imply.
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
- Available tools: {", ".join(ctx.tool_names)}""")

    if ctx.git_branch:
        sections.append(f"- Current branch: {ctx.git_branch}")

    sections.append("""
# Guardrails
- Prefer simple, minimal changes
- Search before creating, and reuse existing patterns
- No new dependencies without asking""")

    sections.append("""
# Verification
After making changes, verify your work:
1. Run `npx tsc --noEmit` when TypeScript is present
2. Run lint, test, or build commands only if they exist in this project and are allowed by the current approval mode
3. Report exactly what you ran, what was blocked, and what was unavailable
4. Do NOT inflate partial verification into a blanket success claim

Do NOT claim "tests pass" without running them.
Scope your claims honestly. "Verification was limited because writes were blocked" is honest.
"All tests pass" when you didn't run them is not.""")

    if ctx.project_context:
        sections.append(f"""
# Project Instructions (from AGENTS.md)
{ctx.project_context}""")

    return "\n".join(sections)
