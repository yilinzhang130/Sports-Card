from reports.app._components.audit import write_audit
from sportscards.db.models import AuditLog
from sportscards.db.session import session_scope


def test_write_audit_persists_row(migrated_db):
    write_audit("portfolio_override_set", {"card_id": 1, "target_weight_pct": 0.05}, actor="ui")
    with session_scope() as s:
        rows = s.query(AuditLog).all()
        assert len(rows) == 1
        assert rows[0].action == "portfolio_override_set"
        assert rows[0].payload_json["card_id"] == 1
        assert rows[0].actor == "ui"


def test_write_audit_default_actor(migrated_db):
    write_audit("test_action", {"x": 1})
    with session_scope() as s:
        row = s.query(AuditLog).one()
        assert row.actor == "ui"
