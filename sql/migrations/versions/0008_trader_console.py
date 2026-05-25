"""trader console: overrides, holdings, model_run_log, audit_log"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portfolio_overrides",
        sa.Column("card_id", sa.Integer, sa.ForeignKey("card_master.card_id"), primary_key=True),
        sa.Column("target_weight_pct", sa.Numeric(8, 5), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("set_by", sa.String(64), nullable=False, server_default="ui"),
        sa.Column(
            "set_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_table(
        "portfolio_holdings",
        sa.Column("holding_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("card_id", sa.Integer, sa.ForeignKey("card_master.card_id"), nullable=False),
        sa.Column("cert_number", sa.String(32)),
        sa.Column("slab_grader", sa.String(16)),
        sa.Column("slab_grade", sa.Numeric(3, 1)),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acquired_cost_usd", sa.Numeric(12, 2), nullable=False),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="held"),
        sa.Column("sold_at", sa.DateTime(timezone=True)),
        sa.Column("sold_proceeds_usd", sa.Numeric(12, 2)),
    )
    op.create_index("ix_holdings_card", "portfolio_holdings", ["card_id"])
    op.create_index("ix_holdings_status", "portfolio_holdings", ["status"])
    op.create_table(
        "model_run_log",
        sa.Column("run_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("params_json", sa.JSON, nullable=False, server_default="{}"),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("summary_json", sa.JSON),
        sa.Column("error", sa.Text),
    )
    op.create_index("ix_runlog_action_status", "model_run_log", ["action", "status"])
    op.create_table(
        "audit_log",
        sa.Column("audit_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.JSON, nullable=False),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_index("ix_runlog_action_status", table_name="model_run_log")
    op.drop_table("model_run_log")
    op.drop_index("ix_holdings_status", table_name="portfolio_holdings")
    op.drop_index("ix_holdings_card", table_name="portfolio_holdings")
    op.drop_table("portfolio_holdings")
    op.drop_table("portfolio_overrides")
