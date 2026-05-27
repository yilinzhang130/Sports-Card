from datetime import date

import pytest

from sportscards.flows.pricing_refresh import pricing_refresh_flow


@pytest.mark.usefixtures("migrated_db")
def test_pricing_refresh_flow_writes_targets_and_signals(seeded_holding_for_flow):
    as_of = date(2026, 5, 27)
    result = pricing_refresh_flow(as_of=as_of)
    assert result["targets_written"] >= 1
    assert "signals_written" in result
