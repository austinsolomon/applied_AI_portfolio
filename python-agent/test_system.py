"""Smallest useful prompt tests: optional sections appear only when set.

Run: .venv/bin/python test_system.py   (or pytest, if installed)
"""

from dataclasses import replace

from system import PromptContext, build_system_prompt

BASE = PromptContext(
    working_directory="/repo",
    sandbox_type="local",
    tool_names=["read", "grep", "bash"],
)


def test_core_sections():
    """Role, sandbox, tool list, Agency and Guardrails are always there."""
    prompt = build_system_prompt(BASE)
    assert "You are a coding agent working in: /repo" in prompt
    assert "Sandbox: local" in prompt
    assert "Available tools: read, grep, bash" in prompt
    assert "# Agency" in prompt
    assert "# Guardrails" in prompt
    assert "# Handling Ambiguity" in prompt
    assert "# Verification" in prompt
    assert 'Do NOT claim "tests pass" without running them.' in prompt
    assert "Search before reading." in prompt
    assert "(no verification commands discovered for this project)" in prompt


def test_verification_lists_gates_in_order():
    prompt = build_system_prompt(replace(BASE, verification_commands=["npx tsc --noEmit", "npm test"]))
    assert "1. `npx tsc --noEmit`\n2. `npm test`" in prompt


def test_git_branch_only_when_set():
    """replace() copies the dataclass with one field changed."""
    assert "Current branch: main" in build_system_prompt(replace(BASE, git_branch="main"))
    assert "Current branch" not in build_system_prompt(BASE)


def test_project_context_only_when_set():
    prompt = build_system_prompt(replace(BASE, project_context="Use tabs."))
    assert "# Project Instructions (from AGENTS.md)\nUse tabs." in prompt
    assert "Project Instructions" not in build_system_prompt(BASE)


def test_skills_only_when_present():
    prompt = build_system_prompt(replace(BASE, skills=[("auth-patterns", "Auth tips")]))
    assert "# Skills" in prompt and "- auth-patterns: Auth tips" in prompt
    assert "# Skills" not in build_system_prompt(BASE)


def test_pure():
    assert build_system_prompt(BASE) == build_system_prompt(BASE)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
