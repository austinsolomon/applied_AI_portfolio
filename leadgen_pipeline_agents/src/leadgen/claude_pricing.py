"""Dollar cost of Claude API calls, from the token counts each response reports.

Prices are US dollars per million tokens (Claude API list prices). Cache writes cost 1.25 times
the input price and cache reads 0.1 times. Haiku 5.5's listed price applies to prompts up to
100,000 tokens, far above anything this project sends.
"""

from dataclasses import dataclass
from decimal import Decimal

TOKENS_PER_MILLION = Decimal(1_000_000)
CACHE_WRITE_MULTIPLIER = Decimal("1.25")
CACHE_READ_MULTIPLIER = Decimal("0.1")


@dataclass(frozen=True)
class ModelPrice:
    input_per_million: Decimal
    output_per_million: Decimal


MODEL_PRICES: dict[str, ModelPrice] = {
    "claude-opus-5-5": ModelPrice(Decimal("4.00"), Decimal("20.00")),
    "claude-sonnet-5-5": ModelPrice(Decimal("2.00"), Decimal("10.00")),
    "claude-haiku-5-5": ModelPrice(Decimal("0.10"), Decimal("0.50")),
}


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    output_tokens: int


def price_for(model: str) -> ModelPrice:
    try:
        return MODEL_PRICES[model]
    except KeyError:
        known_models = ", ".join(sorted(MODEL_PRICES))
        raise ValueError(f"no price on file for model {model!r}; known: {known_models}") from None


def call_cost_usd(model: str, usage: TokenUsage) -> Decimal:
    """Cost of one call: uncached input, cache writes, cache reads and output, at list prices."""
    price = price_for(model)
    input_cost = price.input_per_million * (
        usage.input_tokens
        + CACHE_WRITE_MULTIPLIER * usage.cache_creation_input_tokens
        + CACHE_READ_MULTIPLIER * usage.cache_read_input_tokens
    )
    output_cost = price.output_per_million * usage.output_tokens
    return (input_cost + output_cost) / TOKENS_PER_MILLION
