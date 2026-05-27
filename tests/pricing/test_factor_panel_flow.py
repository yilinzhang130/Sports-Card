from datetime import date

import pytest

from sportscards.flows.factor_panel import factor_panel_flow


@pytest.mark.usefixtures("migrated_db")
def test_factor_panel_flow_runs_and_returns_count():
    result = factor_panel_flow(as_of=date(2026, 5, 27))
    assert "rows_written" in result
    assert isinstance(result["rows_written"], int)
