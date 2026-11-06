"""Dollar cost from token counts, at list prices."""

from decimal import Decimal

import pytest

from leadgen.claude_pricing import TokenUsage, call_cost_usd


def test_cost_counts_input_cache_writes_cache_reads_and_output() -> None:
    usage = TokenUsage(
        input_tokens=1_000_000,
        cache_creation_input_tokens=1_000_000,
        cache_read_input_tokens=1_000_000,
        output_tokens=1_000_000,
    )

    # Opus 5.5: $4 input + $5 cache write (1.25x) + $0.40 cache read (0.1x) + $20 output.
    assert call_cost_usd("claude-opus-5-5", usage) == Decimal("29.40")


def test_small_haiku_call() -> None:
    usage = TokenUsage(
        input_tokens=823, cache_creation_input_tokens=0, cache_read_input_tokens=0, output_tokens=28
    )

    assert call_cost_usd("claude-haiku-5-5", usage) == Decimal("0.0000963")


def test_unknown_model_is_an_error_not_a_free_call() -> None:
    usage = TokenUsage(1, 0, 0, 1)

    with pytest.raises(ValueError, match="no price on file"):
        call_cost_usd("claude-unknown", usage)
