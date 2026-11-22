// Smallest useful prompt tests: optional sections appear only when set.
// Run: bun test
import { expect, test } from "bun:test";
import { buildSystemPrompt, type PromptContext } from "./system";

const base: PromptContext = {
  workingDirectory: "/repo",
  sandboxType: "local",
  toolNames: ["read", "grep", "bash"],
};

test("includes role, sandbox, tools, and both core sections", () => {
  const prompt = buildSystemPrompt(base);
  expect(prompt).toContain("You are a coding agent working in: /repo");
  expect(prompt).toContain("Sandbox: local");
  expect(prompt).toContain("Available tools: read, grep, bash");
  expect(prompt).toContain("# Agency");
  expect(prompt).toContain("# Guardrails");
  expect(prompt).toContain("# Handling Ambiguity");
  expect(prompt).toContain("# Verification");
  expect(prompt).toContain('Do NOT claim "tests pass" without running them.');
});

test("gitBranch line only when set", () => {
  expect(buildSystemPrompt({ ...base, gitBranch: "main" })).toContain("Current branch: main");
  expect(buildSystemPrompt(base)).not.toContain("Current branch");
});

test("project instructions only when set", () => {
  expect(buildSystemPrompt({ ...base, projectContext: "Use tabs." })).toContain(
    "# Project Instructions (from AGENTS.md)\nUse tabs.",
  );
  expect(buildSystemPrompt(base)).not.toContain("Project Instructions");
});

test("pure: same context, same prompt", () => {
  expect(buildSystemPrompt(base)).toBe(buildSystemPrompt(base));
});
