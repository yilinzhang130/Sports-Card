"""Regression tests for the regex parser.

MVP gate: ≥95% of fixtures with positive expectations match exactly.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from sportscards.parse.regex_parser import parse_title

FIXTURES = Path(__file__).parent / "fixtures" / "titles_sample.yaml"
PASS_THRESHOLD = 0.95


def load_cases() -> list[dict]:
    return yaml.safe_load(FIXTURES.read_text())


def check_case(case: dict) -> tuple[bool, str]:
    title = case["title"]
    expect = case["expect"]
    parsed = parse_title(title)

    if "confidence_below" in expect:
        ok = parsed.confidence < Decimal(str(expect["confidence_below"]))
        msg = f"confidence={parsed.confidence}"
        return ok, msg

    for k, v in expect.items():
        actual = getattr(parsed, k)
        if v is None:
            if actual is not None:
                return False, f"{k}: expected None, got {actual!r}"
            continue
        if isinstance(v, (int, float)) and not isinstance(v, bool) and k == "slab_grade":
            if actual != Decimal(str(v)):
                return False, f"{k}: {actual!r} != {v!r}"
            continue
        if actual != v:
            return False, f"{k}: {actual!r} != {v!r}"
    return True, "ok"


def test_fixture_accuracy() -> None:
    """Aggregate: ≥95% of fixtures must pass."""
    cases = load_cases()
    failures: list[tuple[str, str]] = []
    for case in cases:
        ok, msg = check_case(case)
        if not ok:
            failures.append((case["title"], msg))
    total = len(cases)
    passed = total - len(failures)
    rate = passed / total
    if rate < PASS_THRESHOLD:
        details = "\n".join(f"  - {t!r}: {m}" for t, m in failures[:20])
        pytest.fail(
            f"Parser accuracy {rate:.1%} ({passed}/{total}) below {PASS_THRESHOLD:.0%}\n{details}"
        )
    print(f"\nParser accuracy: {rate:.1%} ({passed}/{total})")


@pytest.mark.parametrize("case", load_cases(), ids=lambda c: c["title"][:60])
def test_each_case(case: dict) -> None:
    """Per-title test for granular CI output. Some may xfail; the aggregate gate is authoritative."""
    ok, msg = check_case(case)
    if not ok:
        pytest.xfail(msg)
