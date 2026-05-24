"""tx_mispricing table for hedonic model residuals

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-24

Note on slot number: the Phase 2B spec called for revision "0003" to leave room
for a Phase 2A migration, but Phase 2A is being developed in parallel on a
separate branch and has not merged yet. Taking the next available slot (0002)
keeps the alembic history linear here. If Phase 2A lands first with its own
0002, this migration will need to be renumbered to 0003 and its down_revision
updated accordingly during the merge.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
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
