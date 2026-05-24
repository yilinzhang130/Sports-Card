"""tx_mispricing table for hedonic model residuals

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-24

Chained after 0002 (player_stardom_score, from Phase 3) which landed on main
before this branch. Phase 2A (repeat-sales) is still on a separate branch and
will need its own renumber when it merges.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
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
