"""The Sandbox interface: what a tool needs from "the place where code lives
and commands run", and nothing more.

Tools call these methods instead of Python's file and subprocess functions,
so the backend (local disk, in-memory filesystem, cloud VM) can change
without touching any tool. Keep it small: anything added here, every
backend has to support forever.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol


@dataclass
class ExecResult:
    """What you get back after running a command.

    stdout:     the text the command printed (or its error message)
    exit_code:  0 means it worked; any other number means it failed
    """

    stdout: str
    exit_code: int


class Sandbox(Protocol):
    """The job description for a sandbox backend.

    Any class with these attributes and methods counts as a Sandbox, no
    inheritance needed.

    Identity:
      type               which backend this is ("local", later "just-bash",
                         "cloud"); shown in logs and the system prompt
      working_directory  the base path tools resolve relative paths against

    Required capabilities (all async, even when a backend is synchronous
    underneath, because the cloud backend really is async and one signature
    for every backend keeps the tools simple):
      read_file(path)    return a file's text
      exec(command)      run a shell command; never raises, returns ExecResult
      stop()             shut down; doing nothing is fine

    Optional capabilities, which only some backends have:
      expires_at         when the sandbox times out (None if it never does)
      snapshot()         save its state. A Protocol can't mark a method as
                         optional, so it's left out here; check for it with
                         getattr(sandbox, "snapshot", None) before calling.
    """

    type: str
    working_directory: str
    expires_at: float | None

    async def read_file(self, path: str) -> str: ...

    async def exec(self, command: str) -> ExecResult: ...

    async def stop(self) -> None: ...


# A lifecycle hook: an async function that takes the sandbox and returns
# nothing.
Hook = Callable[[Sandbox], Awaitable[None]]


@dataclass
class SandboxLifecycle:
    """Optional steps that run around a sandbox. Any of them can be left out.

      after_start  right after the sandbox is ready: setup such as
                   configuring git, installing dependencies, copying .env
      before_stop  right before shutdown: save anything important, e.g.
                   commit uncommitted work or take a snapshot
      on_timeout   called by the backend itself when its time limit is
                   reached (cloud only; used in Module 7)

    Empty for local: SandboxLifecycle() has all three set to None, so
    nothing extra runs. Callers check a hook is set before calling it.
    """

    after_start: Hook | None = None
    before_stop: Hook | None = None
    on_timeout: Hook | None = None
