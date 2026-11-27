"""Replay redacted observations; this verifies contracts, not model accuracy.

The observations come from the 2026-10-11 mobile-massage run, so they replay against that
category, kept as a test fixture.
"""

import json
from pathlib import Path

import pytest

from leadgen.category_config import load_category_config
from leadgen.qualification import Judgment, validate_judgment

MASSAGE_CATEGORY = Path(__file__).parent / "fixtures" / "mobile_massage_category.yaml"
CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "phoenix_live_decisions.json").read_text()
)["cases"]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["judgment"]["verdict"])
def test_live_decision_contract(case):
    judgment = Judgment.model_validate(case["judgment"])
    validated = validate_judgment(
        judgment, case["evidence"], load_category_config(MASSAGE_CATEGORY)
    )
    assert validated == judgment
    if judgment.verdict == "qualified":
        assert (
            validate_judgment(judgment, {}, load_category_config(MASSAGE_CATEGORY)).verdict
            == "review"
        )
