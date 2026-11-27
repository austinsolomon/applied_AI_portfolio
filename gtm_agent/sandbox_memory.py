"""The throwaway backend: the agent works on a disposable copy of the project.

The course builds this lesson's backend on just-bash, a JavaScript library
with a simulated bash and a copy-on-write filesystem. There's no Python
version, so this backend gets the same promise a simpler way:

  - at start, the project is copied into a temporary folder
  - reads, commands and writes all happen in that copy
  - stop() deletes the copy

Your real project folder is never modified, whatever the agent writes or
deletes. Unlike just-bash, commands are real (your system's grep, git, ...),
so they behave exactly as they do on your machine. The trade-off is the copy
up front; heavy folders (.venv, node_modules) are skipped to keep it quick.
"""

import asyncio
import shutil
import tempfile
from pathlib import Path

from sandbox import ExecResult
from sandbox_local import LocalSandbox

# Folders not copied: large, regenerable, and never what the agent needs.
SKIP = shutil.ignore_patterns(".venv", "node_modules", "__pycache__")


class TempCopySandbox:
    """A Sandbox that runs everything inside a temporary copy of the project.

    working_directory is still the real project path, because that's what the
    system prompt should name. The actual work is handed to a LocalSandbox
    pointed at the copy, so there's no duplicated file/command code here.
    """

    type = "temp-copy"
    expires_at: float | None = None

    def __init__(self, directory: Path, copy_root: Path, temp_dir: Path):
        self.working_directory = str(directory)
        self._copy = LocalSandbox(copy_root)
        self._temp_dir = temp_dir

    async def read_file(self, path: str) -> str:
        """Read from the copy, never the original."""
        return await self._copy.read_file(path)

    async def exec(self, command: str) -> ExecResult:
        """Run inside the copy, so writes and deletes only affect the copy."""
        return await self._copy.exec(command)

    async def stop(self) -> None:
        """Delete the temporary copy, and with it everything the agent wrote."""
        shutil.rmtree(self._temp_dir, ignore_errors=True)


async def create_temp_copy_sandbox(directory: str | Path) -> TempCopySandbox:
    """Copy the project into a new temp folder and return a sandbox over it.

    Async because copying takes a moment; the copy runs on a side thread.
    """
    source = Path(directory).resolve()
    temp_dir = Path(tempfile.mkdtemp(prefix="teensy-"))
    copy_root = temp_dir / "project"
    await asyncio.to_thread(shutil.copytree, source, copy_root, ignore=SKIP, symlinks=True)
    return TempCopySandbox(source, copy_root, temp_dir)
