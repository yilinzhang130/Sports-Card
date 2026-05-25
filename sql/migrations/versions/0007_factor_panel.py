"""factor_panel table for momentum + liquidity factors

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-25

Chained after 0006_prospect_forecast which landed on main while this
branch was open.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "factor_panel",
        sa.Column(
            "card_id",
            sa.Integer,
            sa.ForeignKey("card_master.card_id"),
            primary_key=True,
        ),
        sa.Column("as_of_date", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("r30", sa.Numeric(10, 6)),
        sa.Column("r90", sa.Numeric(10, 6)),
        sa.Column("r365", sa.Numeric(10, 6)),
        sa.Column("cs_momentum_pct", sa.Numeric(6, 4)),
        sa.Column("is_hyped", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("sales_count_90d", sa.Integer, nullable=False, server_default="0"),
        sa.Column("dollar_volume_90d", sa.Numeric(14, 2)),
        sa.Column("bid_ask_proxy", sa.Numeric(8, 5)),
        sa.Column("last_sale_recency_days", sa.Integer),
        sa.Column("liquidity_tier", sa.String(1), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_factor_panel_as_of", "factor_panel", ["as_of_date"])
    op.create_index(
        "ix_factor_panel_card_asof", "factor_panel", ["card_id", "as_of_date"]
    )
    # TimescaleDB hypertable on as_of_date (no-op on non-Timescale backends).
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        try:
            op.execute(
                "SELECT create_hypertable('factor_panel', 'as_of_date', "
                "if_not_exists => TRUE, migrate_data => TRUE)"
            )
        except Exception:
            pass


def downgrade() -> None:
    op.drop_index("ix_factor_panel_card_asof", table_name="factor_panel")
    op.drop_index("ix_factor_panel_as_of", table_name="factor_panel")
    op.drop_table("factor_panel")
