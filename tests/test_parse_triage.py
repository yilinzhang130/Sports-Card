from datetime import datetime
from decimal import Decimal

import pytest

from reports.app.pages._parse_triage_helpers import apply_manual_correction
from sportscards.db.models import ParseFailure, TxClean, TxRaw
from sportscards.db.session import session_scope


def _seed_failure() -> int:
    with session_scope() as s:
        raw = TxRaw(
            source="ebay",
            raw_title="ambiguous title",
            raw_price=Decimal("100"),
            raw_currency="USD",
            sold_at=datetime(2026, 1, 1),
            raw_json={},
        )
        s.add(raw)
        s.flush()
        s.add(ParseFailure(raw_id=raw.raw_id, reason="ambiguous"))
        raw_id = raw.raw_id
    return raw_id


def test_manual_correction_creates_clean_and_removes_failure(migrated_db):
    raw_id = _seed_failure()
    apply_manual_correction(
        raw_id,
        card_id=1,
        slab_grader="PSA",
        slab_grade=Decimal("10"),
        price_usd=Decimal("100"),
        sold_at=datetime(2026, 1, 1),
        actor="ui",
    )
    with session_scope() as s:
        assert s.query(TxClean).filter_by(raw_id=raw_id).count() == 1
        assert s.query(ParseFailure).filter_by(raw_id=raw_id).count() == 0
        clean = s.query(TxClean).filter_by(raw_id=raw_id).one()
        assert clean.parser_method == "manual"
        assert clean.card_id == 1
        assert float(clean.slab_grade) == 10.0


def test_manual_correction_unknown_raw_raises(migrated_db):
    with pytest.raises(KeyError):
        apply_manual_correction(
            999999,
            card_id=1,
            slab_grader="PSA",
            slab_grade=Decimal("10"),
            price_usd=Decimal("100"),
            sold_at=datetime(2026, 1, 1),
            actor="ui",
        )


def test_parse_triage_page_renders(migrated_db):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file("reports/app/pages/4_🔧_Parse_Triage.py").run()
    assert not at.exception, f"unexpected exception: {at.exception}"
