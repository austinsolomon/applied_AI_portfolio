"""apify.py — the sourcing provider: an Apify actor run, start to finish.

THE GAME LESSON THIS MIRRORS (external_levels/apify_trawler). You fire a shot at
a dock post; the boat steams out, works its patch with the net down for a while,
and comes back low in the water; only then does the haul land on the quay where
you can walk over and collect it. The four beats are OUT / WORK / BACK / LAND.

That is not decoration — it is the actual shape of the Apify API, and it is the
single most common thing a beginner gets wrong:

    OUT   POST /v2/acts/{actor}/runs            -> 201, {data:{id, status:"READY"}}
    WORK  GET  /v2/actor-runs/{runId}            -> poll until status is terminal
    BACK  status == "SUCCEEDED", defaultDatasetId is now populated
    LAND  GET  /v2/datasets/{dsId}/items?format=json  -> the records

A scrape is ASYNCHRONOUS. The POST returns almost immediately and it returns
NOTHING USEFUL — no leads, just a run id. Code that expects leads from the POST
is the equivalent of standing on the quay waiting for the boat that has not left
yet. Hence the poll loop, hence the boat.

BILLING NOTE (worth saying out loud in a repo somebody paid for): the run is
billed the moment it starts, whether or not you ever read the dataset. Aborting
your Python process does not abort the actor — call the abort endpoint. The mock
below is free, which is why it is the default.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from .base import ProviderError, announce, env_key

APIFY_BASE = "https://api.apify.com/v2"

# Apify's terminal run states. TIMED-OUT and ABORTED are terminal too, and a
# poll loop that only watches for SUCCEEDED spins forever on them — a real bug
# people hit, so the set is explicit rather than `!= "RUNNING"`.
TERMINAL = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}


class ApifyActorProvider:
    """Live Apify client. Stdlib only — no SDK, so nothing phones home at import."""

    live = True
    name = "apify"

    def __init__(self, token: str, *, base: str = APIFY_BASE,
                 poll_interval: float = 2.0, timeout_s: float = 300.0,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.token = token
        self.base = base.rstrip("/")
        self.poll_interval = poll_interval
        self.timeout_s = timeout_s
        # Injected so tests can drive the poll loop without real waiting. The
        # loop's LOGIC is what deserves testing; its patience is not.
        self._sleep = sleep

    # -------------------------------------------------------------- transport --
    def _request(self, method: str, path: str, body: dict | None = None) -> Any:
        url = f"{self.base}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:  # 401/402/404 all land here
            raise ProviderError(f"apify {method} {path} -> HTTP {e.code}: "
                                f"{e.read()[:300]!r}") from e
        except urllib.error.URLError as e:
            raise ProviderError(f"apify {method} {path} unreachable: {e.reason}") from e
        return json.loads(raw) if raw else None

    # ------------------------------------------------------------ the four beats --
    def start_run(self, actor_id: str, run_input: dict[str, Any]) -> str:
        """OUT — fire the request, get a run id back. No leads here. Ever."""
        # Actor ids contain a slash ("apify/website-content-crawler"); the API
        # wants it tilde-escaped in the path.
        actor_path = actor_id.replace("/", "~")
        res = self._request("POST", f"/acts/{actor_path}/runs", run_input)
        run_id = (res or {}).get("data", {}).get("id")
        if not run_id:
            raise ProviderError(f"apify start returned no run id: {res!r}")
        return run_id

    def poll_run(self, run_id: str) -> dict[str, Any]:
        """WORK + BACK — wait for a terminal status, return the run object."""
        deadline = time.monotonic() + self.timeout_s
        while True:
            run = (self._request("GET", f"/actor-runs/{run_id}") or {}).get("data", {})
            status = run.get("status")
            if status in TERMINAL:
                if status != "SUCCEEDED":
                    raise ProviderError(f"apify run {run_id} ended {status}")
                return run
            if time.monotonic() > deadline:
                # We do NOT abort the run for the caller: the run may still be
                # useful and aborting is a billing decision, not ours to make.
                raise ProviderError(
                    f"apify run {run_id} still {status} after {self.timeout_s}s; "
                    f"it is still running and still billing — abort it in the console"
                )
            self._sleep(self.poll_interval)

    def dataset_items(self, dataset_id: str, limit: int = 1000) -> list[dict[str, Any]]:
        """LAND — the haul. Paginated because a real crawl overruns one page."""
        items: list[dict[str, Any]] = []
        offset = 0
        page = min(limit, 1000)
        while len(items) < limit:
            batch = self._request(
                "GET", f"/datasets/{dataset_id}/items?clean=true&format=json"
                       f"&offset={offset}&limit={page}") or []
            if not batch:
                break
            items.extend(batch)
            offset += len(batch)
            if len(batch) < page:
                break
        return items[:limit]

    def run_actor(self, actor_id: str, run_input: dict[str, Any],
                  limit: int = 1000) -> list[dict[str, Any]]:
        """All four beats. This is what source.py calls."""
        run_id = self.start_run(actor_id, run_input)
        run = self.poll_run(run_id)
        ds = run.get("defaultDatasetId")
        if not ds:
            raise ProviderError(f"apify run {run_id} succeeded with no dataset")
        return self.dataset_items(ds, limit=limit)


class MockApifyActorProvider:
    """Free stand-in that keeps the ASYNC SHAPE, because the shape is the lesson.

    It really does hand out a run id, really does report RUNNING for a couple of
    polls before SUCCEEDED, and only then produces items. A mock that returned
    the rows from `run_actor()` in one hop would let a learner write code that
    breaks the moment a token is pasted in — the exact failure the trawler level
    exists to prevent.
    """

    live = False
    name = "apify(mock)"

    def __init__(self, fixture: list[dict[str, Any]], *, polls_before_done: int = 2,
                 sleep: Callable[[float], None] = lambda _s: None) -> None:
        self.fixture = fixture
        self.polls_before_done = polls_before_done
        self._sleep = sleep
        self._polls: dict[str, int] = {}
        self.calls: list[tuple[str, Any]] = []  # test-visible transcript

    def start_run(self, actor_id: str, run_input: dict[str, Any]) -> str:
        self.calls.append(("start", actor_id))
        run_id = f"mockrun-{len(self._polls) + 1}"
        self._polls[run_id] = 0
        return run_id

    def poll_run(self, run_id: str) -> dict[str, Any]:
        while self._polls.get(run_id, 0) < self.polls_before_done:
            self._polls[run_id] += 1
            self.calls.append(("poll", "RUNNING"))
            self._sleep(0)
        self.calls.append(("poll", "SUCCEEDED"))
        return {"id": run_id, "status": "SUCCEEDED", "defaultDatasetId": f"ds-{run_id}"}

    def dataset_items(self, dataset_id: str, limit: int = 1000) -> list[dict[str, Any]]:
        self.calls.append(("items", dataset_id))
        return self.fixture[:limit]

    def run_actor(self, actor_id: str, run_input: dict[str, Any],
                  limit: int = 1000) -> list[dict[str, Any]]:
        run_id = self.start_run(actor_id, run_input)
        run = self.poll_run(run_id)
        return self.dataset_items(run["defaultDatasetId"], limit=limit)


def get_apify_provider(fixture: list[dict[str, Any]] | None = None,
                       token: str | None = None):
    """APIFY_TOKEN set -> live actor runs. Unset -> the mock trawler."""
    name, value = ("APIFY_TOKEN", token) if token else env_key("APIFY_TOKEN")
    announce("apify", "APIFY_TOKEN", bool(value))
    if value:
        return ApifyActorProvider(value)
    return MockApifyActorProvider(fixture or [])
