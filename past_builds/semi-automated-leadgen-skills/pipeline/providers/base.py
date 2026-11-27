"""base.py — THE PROVIDER RULE, in one place.

EVERY EXTERNAL CALL IN THIS REPO GOES THROUGH A PROVIDER, and every provider has
a mock twin that is selected automatically when its API key is unset. Two reasons,
both load-bearing:

  1. A buyer can `git clone`, `pytest`, and watch the whole pipeline run end to
     end before spending a cent. Nothing about the shape of the output changes
     when they later paste a key in — only the numbers get real.
  2. The test suite never touches the network, so it cannot be flaky, rate
     limited, or expensive. `pytest` with zero env vars is the contract.

HOW SELECTION WORKS. Each provider module exposes a `get_*_provider()` factory
that reads its key from the environment (or from an explicit argument, which is
what the tests use). Key present -> live class. Key absent -> mock class. The
factory logs which one it picked at INFO, because "why is my output fake?" should
never take more than one line of output to answer.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Protocol

log = logging.getLogger("pipeline.providers")


class ProviderError(RuntimeError):
    """A provider failed in a way the stage cannot paper over.

    Deliberately distinct from a MISS. A miss (no data for this lead) is normal
    and is recorded in the ledger as a miss; an error (bad key,
    500, timeout) means the stage should stop rather than silently write records
    with empty columns and report success.
    """


class Provider(Protocol):
    """Structural type every provider satisfies.

    `name` shows up in the cost ledger and the run report, so it must be stable
    and human-readable — it is what a buyer reads when they ask "what did I pay
    for?". `live` is False for every mock, and the runner refuses to claim a
    real run when any provider in the chain is mocked.
    """

    name: str
    live: bool


def load_local_env(path: str | Path = ".env") -> bool:
    """Load a small local ``.env`` file without adding a runtime dependency.

    Existing process environment variables win.  Values are never logged, and
    the parser intentionally supports only the simple ``NAME=value`` form used
    by this repository's ``.env.example``.
    """
    env_path = Path(path)
    if not env_path.exists():
        return False
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name and name.replace("_", "").isalnum():
            os.environ.setdefault(name, value)
    return True


def env_key(*names: str) -> tuple[str | None, str | None]:
    """Return the first (name, value) pair among `names` that is set and non-empty.

    Takes several names so a stage can accept more than one spelling of a
    key. Empty strings count as unset: a `.env`
    copied from `.env.example` has `APIFY_TOKEN=` in it, and treating that as a
    real key would produce a 401 instead of a mock run.
    """
    if os.environ.get("PIPELINE_OFFLINE") == "1":
        return None, None
    for n in names:
        v = os.environ.get(n, "").strip()
        if v:
            return n, v
    return None, None


def announce(kind: str, key_name: str | None, live: bool) -> None:
    """One INFO line per provider so a run is self-explaining."""
    if live:
        log.info("provider %-10s LIVE   (key %s)", kind, key_name)
    else:
        log.info("provider %-10s MOCK   (set %s for live)", kind, key_name or "the key")
