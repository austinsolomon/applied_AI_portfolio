# Python equivalent of lib/agent.ts (reference only, never executed).
# The AI SDK's ToolLoopAgent has no exact Python twin, so a minimal one is
# written out using the Anthropic SDK. Vercel's Python Sandbox SDK method
# names are approximate.

import json
import os
from pathlib import Path

import anthropic
from vercel.sandbox import AsyncSandbox

from .ref_tools import create_bash_tool

# Course uses 'anthropic/claude-opus-4.6' via AI Gateway (needs paid gateway credits).
# We call Anthropic directly instead; the client reads ANTHROPIC_API_KEY from the env.
MODEL = "claude-sonnet-5-5"
client = anthropic.AsyncAnthropic()

INSTRUCTIONS = """
You are a helpful assistant that answers questions about customer calls. Use bashTool to explore the files and find relevant information pertaining to the user's query. Using the information you find, craft a response for the user and output it as text.
"""


class ToolLoopAgent:
    # Ask the model -> run any tools it requests -> feed results back ->
    # repeat until it replies with plain text (or max_steps is hit).
    def __init__(self, model: str, instructions: str, tools: dict, max_steps: int = 20):
        self.model = model
        self.instructions = instructions
        self.tools = tools
        self.max_steps = max_steps

    async def stream(self, prompt: str):
        messages = [{"role": "user", "content": prompt}]
        tool_defs = [
            {"name": name, "description": t["description"], "input_schema": t["input_schema"]}
            for name, t in self.tools.items()
        ]

        for _ in range(self.max_steps):
            response = await client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=self.instructions,
                tools=tool_defs,
                messages=messages,
            )
            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for block in response.content:
                if block.type == "text":
                    yield {"type": "text", "text": block.text}
                elif block.type == "tool_use":
                    tool = self.tools[block.name]
                    args = tool["input_model"](**block.input)  # validate, like Zod
                    yield {"type": "tool-call", "toolName": block.name, "input": block.input}
                    output = await tool["execute"](**args.model_dump())
                    yield {"type": "tool-result", "toolName": block.name, "output": output}
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(output),
                    })

            # No tool calls requested -> the model gave its final answer
            if response.stop_reason != "tool_use":
                return
            messages.append({"role": "user", "content": tool_results})


# Copy lib/calls/*.md from the server's disk into the sandbox at calls/
async def load_sandbox_files(sandbox: AsyncSandbox) -> None:
    calls_dir = Path(os.getcwd()) / "lib" / "calls"
    for file in os.listdir(calls_dir):
        buffer = (calls_dir / file).read_bytes()
        await sandbox.write_files([{"path": f"calls/{file}", "content": buffer}])


# Python has no top-level await, so the module-level setup in TS becomes
# an async factory that the server calls once at startup.
async def create_agent() -> ToolLoopAgent:
    sandbox = await AsyncSandbox.create()
    await load_sandbox_files(sandbox)
    return ToolLoopAgent(
        model=MODEL,
        instructions=INSTRUCTIONS,
        tools={"bashTool": create_bash_tool(sandbox)},
    )
