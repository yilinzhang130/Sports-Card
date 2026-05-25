"""grading_ev table for raw → PSA 10 optionality model.

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "grading_ev",
        sa.Column("card_id", sa.Integer, sa.ForeignKey("card_master.card_id"), primary_key=True),
        sa.Column("as_of_date", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("grade_tier", sa.String(16), primary_key=True),
        sa.Column("gem_rate", sa.Numeric(6, 4), nullable=False),
        sa.Column("p10_price", sa.Numeric(14, 2), nullable=False),
        sa.Column("p9_price", sa.Numeric(14, 2), nullable=False),
        sa.Column("cost_to_grade", sa.Numeric(8, 2), nullable=False),
        sa.Column("raw_price", sa.Numeric(14, 2), nullable=True),
        sa.Column("ev", sa.Numeric(14, 2), nullable=False),
        sa.Column("ev_per_dollar", sa.Numeric(8, 4), nullable=True),
        sa.Column("sample_size", sa.Integer, nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_grading_ev_ev_per_dollar", "grading_ev", ["ev_per_dollar"])
    op.execute(
        "SELECT create_hypertable('grading_ev', 'as_of_date', if_not_exists => TRUE)"
    )


def downgrade() -> None:
    op.drop_index("ix_grading_ev_ev_per_dollar", table_name="grading_ev")
    op.drop_table("grading_ev")
