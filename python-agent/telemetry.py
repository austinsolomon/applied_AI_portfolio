"""Token budget: prices every step and tracks what the session would have
cost without caching.

Each step reports four token counts:
  uncached     input processed at the full price
  cache_read   input read back from the prompt cache (about 0.1x price)
  cache_write  input written to the cache this step (1.25x price)
  output       tokens the model generated

The Claude Code CLI under the Agent SDK does its own context management:
it caches automatically and compacts instead of pruning per step. So the
comparison figure here is "without caching": every input token at the
full input price. There is no pruning to add back.
"""

from dataclasses import asdict, dataclass, field

# USD per million tokens. Cache write is the 5-minute TTL rate.
PRICES = {
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0, "cache_write": 1.25, "cache_read": 0.1},
    "claude-sonnet-5-5": {"input": 2.0, "output": 10.0, "cache_write": 2.5, "cache_read": 0.1},
}


def usd(tokens: int, per_million: float) -> float:
    """Price a token count at a per-million-token rate."""
    return tokens * per_million / 1_000_000


@dataclass
class StepUsage:
    """One step's token counts and cost. Costs are None for unpriced models."""

    step: int
    model: str
    uncached: int
    cache_read: int
    cache_write: int
    output: int
    cost: float | None
    cost_without: float | None

    def to_event(self) -> dict:
        """The step as the browser's "data-usage" event (same field names as the TS server)."""
        d = asdict(self)
        return {
            "step": d["step"], "model": d["model"], "uncached": d["uncached"],
            "cacheRead": d["cache_read"], "cacheWrite": d["cache_write"], "output": d["output"],
            "prunedTokens": 0, "cost": d["cost"], "costWithout": d["cost_without"],
        }


@dataclass
class TokenBudget:
    """Prices steps as they finish and keeps the session totals."""

    model: str
    steps: list[StepUsage] = field(default_factory=list)

    def record(self, usage: dict) -> StepUsage:
        """Price one step from the API's usage block (a "message_delta" event).

        The API's input_tokens excludes cached tokens; cache reads and writes
        are reported in their own fields.
        """
        price = PRICES.get(self.model)
        uncached = usage.get("input_tokens", 0)
        cache_read = usage.get("cache_read_input_tokens", 0)
        cache_write = usage.get("cache_creation_input_tokens", 0)
        output = usage.get("output_tokens", 0)
        cost = cost_without = None
        if price:
            cost = (
                usd(uncached, price["input"]) + usd(cache_read, price["cache_read"])
                + usd(cache_write, price["cache_write"]) + usd(output, price["output"])
            )
            cost_without = usd(uncached + cache_read + cache_write, price["input"]) + usd(output, price["output"])
        step = StepUsage(len(self.steps), self.model, uncached, cache_read, cache_write, output, cost, cost_without)
        self.steps.append(step)
        return step

    def total_cost(self) -> float | None:
        if self.model not in PRICES:
            return None
        return sum(s.cost or 0 for s in self.steps)

    def summary(self) -> str:
        """One line for the end of a run: cost, comparison cost, and savings."""
        cost = self.total_cost()
        if cost is None:
            return f"Session: {len(self.steps)} steps · cost n/a (no price for {self.model})"
        without = sum(s.cost_without or 0 for s in self.steps)
        saved = f" · saved {round((1 - cost / without) * 100)}%" if without else ""
        return f"Session: {len(self.steps)} steps · cost ${cost:.4f} · without caching ${without:.4f}{saved}"


def format_step(s: StepUsage, running: float | None) -> str:
    """One line per step: token counts, the step's cost, and the running total."""
    fmt = lambda n: "n/a" if n is None else f"${n:.4f}"  # noqa: E731
    return (
        f"Step {s.step}: {s.uncached} uncached, {s.cache_read} cached, {s.cache_write} written, "
        f"{s.output} output · step {fmt(s.cost)} · running {fmt(running)}"
    )
