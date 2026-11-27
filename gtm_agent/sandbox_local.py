"""The local backend: the agent works directly in your real project folder.

Reads use the real files, commands run in the real folder, and any change the
agent makes stays. It wraps the same file reads and subprocess calls the tools
made before Module 4, now behind the Sandbox interface. Because it adds no
new behavior, it serves as the reference: if a tool acts differently on
another backend, the difference comes from that backend.
"""

import asyncio
import subprocess
from pathlib import Path

from sandbox import ExecResult


class LocalSandbox:
    """A Sandbox backed by a folder on your own disk.

    It never expires (expires_at is None) and has no snapshot(), because
    the disk outlives the agent and there's nothing to save.
    """

    type = "local"
    expires_at: float | None = None

    def __init__(self, directory: str | Path):
        self.working_directory = str(Path(directory).resolve())

    async def read_file(self, path: str) -> str:
        """Read a file, with relative paths resolved against the working directory."""
        return (Path(self.working_directory) / path).read_text(encoding="utf-8")

    async def exec(self, command: str) -> ExecResult:
        """Run a shell command inside the working directory.

        Never raises: a failing command, or one killed after 30 seconds
        (reported as exit code 124), still comes back as an ExecResult,
        because tools expect a result, not an exception.

        shell=True because commands arrive as one string. subprocess.run
        blocks until the command finishes, so it runs on a side thread
        (asyncio.to_thread) while the rest of the agent keeps going.
        """
        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                command,
                shell=True,
                cwd=self.working_directory,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            return ExecResult(stdout="timed out after 30s", exit_code=124)
        if proc.returncode != 0:
            return ExecResult(stdout=proc.stdout or proc.stderr, exit_code=proc.returncode)
        return ExecResult(stdout=proc.stdout, exit_code=0)

    async def stop(self) -> None:
        """Nothing to clean up locally."""


def create_local_sandbox(directory: str | Path) -> LocalSandbox:
    """Build a local sandbox rooted at `directory`."""
    return LocalSandbox(directory)
