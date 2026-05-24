"""Tests for the reporting layer."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from sportscards.reports import queries


def test_table_missing_is_raised_when_table_absent():
    """If the target table does not exist, queries raise TableMissing."""
    fake_engine = MagicMock()
    with patch("sportscards.reports.queries.inspect") as mock_inspect:
        mock_inspect.return_value.has_table.return_value = False
        with pytest.raises(queries.TableMissing):
            queries.repeat_sales_index(engine=fake_engine)
