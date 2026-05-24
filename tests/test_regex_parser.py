"""Regression tests for the regex parser.

The MVP gate requires ≥95% accuracy on 100 hand-labeled titles.
This file ships with a 5-title seed in tests/fixtures/titles_sample.yaml;
expand to 100 before claiming Phase 1 done.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from sportscards.parse.regex_parser import parse_title

FIXTURES = Path(__file__).parent / "fixtures" / "titles_sample.yaml"


def load_cases() -> list[dict]:
    return yaml.safe_load(FIXTURES.read_text())


@pytest.mark.parametrize("case", load_cases())
def test_parse_known_titles(case: dict) -> None:
    title = case["title"]
    expect = case["expect"]
    parsed = parse_title(title)
    for k, v in expect.items():
        actual = getattr(parsed, k)
        if isinstance(v, float):
            assert actual == Decimal(str(v)), f"{title!r} field {k!r}: {actual!r} != {v!r}"
        else:
            assert actual == v, f"{title!r} field {k!r}: {actual!r} != {v!r}"


def test_low_quality_title_has_low_confidence() -> None:
    result = parse_title("rare card lebron lot of 5")
    assert result.confidence < Decimal("0.5")
