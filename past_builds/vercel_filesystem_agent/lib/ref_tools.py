# Python equivalent of lib/tools.ts (reference only, never executed).
# Vercel's Python Sandbox SDK method names are approximate.

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    # Type-only import, same as `import type { Sandbox }`
    from vercel.sandbox import AsyncSandbox


# Zod schema -> Pydantic model: the shape the model must fill in
class BashInput(BaseModel):
    command: str = Field(description="The bash command to execute")
    args: list[str] = Field(description="Arguments to pass to the command")


def create_bash_tool(sandbox: "AsyncSandbox") -> dict:
    # Closure: execute() remembers which sandbox it belongs to
    async def execute(command: str, args: list[str]) -> dict:
        result = await sandbox.run_command(command, args, capture_output=True)
        return {
            "stdout": result.stdout.decode(),
            "stderr": result.stderr.decode(),
            "exitCode": result.exit_code,
        }

    # tool({...}) -> a plain dict with the same three parts
    return {
        "description": """
            Execute bash commands to explore transcript and instruction files.
            Examples (not exhaustive): ls, cat, less, head, tail, grep
        """,
        "input_schema": BashInput.model_json_schema(),
        "input_model": BashInput,
        "execute": execute,
    }
