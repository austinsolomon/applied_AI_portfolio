"""Approval policy: decides WHETHER a bash command may run."""

import os
import re
from typing import Annotated, Callable, Literal

from pydantic import BaseModel, Field, TypeAdapter

# Read-only commands. In interactive mode these run without asking.
SAFE_PREFIXES = [
    "ls", "cat", "echo", "pwd", "which", "find",
    "head", "tail", "wc", "git log", "git status", "git diff",
]

# Shell tricks that never pass a prefix match: chaining (; & |), command
# substitution (` $), redirects (< >), and find's -exec/-delete. Without
# this, "ls; rm -rf ~" would pass because it starts with "ls".
FORBIDDEN = re.compile(r"[;&|`$<>]|\s-(exec|execdir|delete|ok)\b")


def matches_any(command: str, prefixes: list[str]) -> bool:
    """Is this command one of the allowed prefixes?

    "Prefix" means the whole word: "ls" and "ls -la" match "ls", but "lsof"
    does not. A command containing a forbidden shell trick never matches,
    even if it starts with an allowed word.
    """
    cmd = command.strip()
    if FORBIDDEN.search(cmd):
        return False
    return any(cmd == p or cmd.startswith(p + " ") for p in prefixes)


class Interactive(BaseModel):
    """Local use: safe-list commands run, everything else is blocked."""

    mode: Literal["interactive"] = "interactive"


class Background(BaseModel):
    """CI and automation: there's no human to ask, so every command runs."""

    mode: Literal["background"] = "background"


class Delegated(BaseModel):
    """Subagents: only the commands the parent agent hands down may run.

    `trust` exists only on this mode. A read-only explorer might get
    ["pwd", "find .", "git status"]; a test runner might get ["bun test"].
    """

    mode: Literal["delegated"] = "delegated"
    trust: list[str]


# The approval policy: exactly one of the three modes above. This is a
# "discriminated union": the `mode` field says which one it is, and
# Pydantic uses it to pick the right class when parsing raw data, e.g.
#   {"mode": "delegated", "trust": ["pwd"]}  ->  Delegated(trust=["pwd"])
#   {"mode": "delegated"}                    ->  error: trust is required
# Because the policy is data rather than code, it can come from a config
# file or an env var, or be passed down to a subagent.
ApprovalConfig = Annotated[Interactive | Background | Delegated, Field(discriminator="mode")]

# A function that answers "does this command need approval?"
# True means it's blocked; False means it may run.
RequiresApproval = Callable[[str], bool]


def create_approval(config: ApprovalConfig) -> RequiresApproval:
    """Turn an approval policy into the yes/no function the bash tool uses.

    `match` checks which class config is, and `case Delegated(trust=trust)`
    also pulls the trust list out in the same step.
    """

    def requires_approval(command: str) -> bool:
        match config:
            case Background():
                return False
            case Delegated(trust=trust):
                return not matches_any(command, trust)
            case _:
                return not matches_any(command, SAFE_PREFIXES)

    return requires_approval


def approval_from_env() -> ApprovalConfig:
    """Read the approval mode for a command-line run from env vars.

    APPROVAL_MODE picks the mode (default "interactive"); APPROVAL_TRUST is a
    comma-separated list used by delegated mode. TypeAdapter lets Pydantic
    validate against the union, so a typo like APPROVAL_MODE=bckground fails
    with a clear error instead of silently picking a mode. The extra `trust`
    key is ignored by the modes that don't have one.
    """
    trust = [t.strip() for t in os.environ.get("APPROVAL_TRUST", "").split(",") if t.strip()]
    return TypeAdapter(ApprovalConfig).validate_python(
        {"mode": os.environ.get("APPROVAL_MODE", "interactive"), "trust": trust}
    )
