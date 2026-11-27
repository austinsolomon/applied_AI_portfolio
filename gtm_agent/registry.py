"""Tool registry: the one place the agent's tool set is assembled.

Built-in tools register through register_builtins; a project adds its own
tools with registry.register(...) or wraps a built-in with wrap_tool, without
editing the harness. The agent's tool bundle and the prompt's tool list both
come from the registry, so they always match.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable
from typing import Any

from claude_agent_sdk import SdkMcpTool

from approval import ApprovalConfig, create_approval
from sandbox import Sandbox
from skills import Skill
from tools import (
    SubagentListener,
    create_ask_user_tool,
    create_bash_tool,
    create_grep_tool,
    create_load_skill_tool,
    create_read_tool,
    create_task_tool,
    create_todo_tool,
)


class ToolRegistry:
    """A dict behind a small interface. It holds no policy: whoever calls
    register decides what gets in. Registering an existing name replaces it."""

    def __init__(self) -> None:
        self._tools: dict[str, SdkMcpTool] = {}

    def register(self, name: str, tool: SdkMcpTool) -> None:
        self._tools[name] = tool

    def get(self, name: str) -> SdkMcpTool | None:
        return self._tools.get(name)

    def list(self) -> list[str]:
        return list(self._tools)

    def entries(self) -> list[tuple[str, SdkMcpTool]]:
        return list(self._tools.items())

    def tools(self) -> list[SdkMcpTool]:
        """The tool objects, ready for create_sdk_mcp_server(tools=...)."""
        return list(self._tools.values())


def create_registry() -> ToolRegistry:
    return ToolRegistry()


def register_builtins(
    registry: ToolRegistry,
    sandbox: Sandbox,
    skills: list[Skill],
    approval: ApprovalConfig,
    on_subagent: SubagentListener | None = None,
) -> None:
    """Register the built-in tools.

    Order matters: task reuses read and grep, so they are registered first.
    approval sets bash's mode for this surface (the CLI uses the
    APPROVAL_MODE setting; the web server uses Background because a human
    approves first). on_subagent is the optional listener for subagent events.
    """
    read = create_read_tool(sandbox)
    grep = create_grep_tool(sandbox)
    registry.register("read", read)
    registry.register("grep", grep)
    registry.register("bash", create_bash_tool(sandbox, create_approval(approval)))
    registry.register("task", create_task_tool(sandbox, read, grep, on_subagent))
    registry.register("askUser", create_ask_user_tool())
    registry.register("todo", create_todo_tool())
    registry.register("loadSkill", create_load_skill_tool(skills))


def wrap_tool(
    base: SdkMcpTool,
    before_execute: Callable[[dict], Awaitable[dict] | dict] | None = None,
    after_execute: Callable[[dict], Awaitable[dict] | dict] | None = None,
) -> SdkMcpTool:
    """Return a new tool that runs the hooks around the base tool's handler.

    before_execute may rewrite the input; after_execute may rewrite the
    result. The base tool is unchanged (dataclasses.replace makes a copy with
    a new handler), so register the wrapped tool under the same name to
    replace what the agent sees.
    """

    async def maybe_await(value: Any) -> Any:
        return await value if isinstance(value, Awaitable) else value

    async def handler(args: dict) -> dict:
        transformed = await maybe_await(before_execute(args)) if before_execute else args
        result = await base.handler(transformed)
        return await maybe_await(after_execute(result)) if after_execute else result

    return dataclasses.replace(base, handler=handler)
