"""tx_mispricing table for hedonic model residuals

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-25

Chained after 0004_backtest_runs (Phase 4 portfolio) which landed on main
while this branch was open.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tx_mispricing",
        sa.Column(
            "tx_id",
            sa.Integer,
            sa.ForeignKey("tx_clean.tx_id"),
            primary_key=True,
        ),
        sa.Column("model_version", sa.String(32), primary_key=True),
        sa.Column("residual", sa.Numeric(10, 6), nullable=False),
        sa.Column("predicted_log_price", sa.Numeric(10, 6), nullable=False),
        sa.Column(
            "fit_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_tx_mispricing_residual", "tx_mispricing", ["residual"])


def downgrade() -> None:
    op.drop_index("ix_tx_mispricing_residual", table_name="tx_mispricing")
    op.drop_table("tx_mispricing")
