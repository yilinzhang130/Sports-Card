"""repeat_sales_index hypertable

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-24

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
        "repeat_sales_index",
        sa.Column("period_start", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("sport", sa.String(8), primary_key=True),
        sa.Column("bucket", sa.String(8), primary_key=True),
        sa.Column("grade_tier", sa.String(8), primary_key=True),
        sa.Column("era", sa.String(8), primary_key=True),
        sa.Column("index_value", sa.Numeric(14, 4)),
        sa.Column("n_pairs", sa.Integer, nullable=False, server_default="0"),
        sa.Column("se", sa.Numeric(8, 5)),
    )
    op.create_index(
        "ix_rsi_lookup",
        "repeat_sales_index",
        ["sport", "grade_tier", "era", "bucket"],
    )
    op.execute(
        "SELECT create_hypertable('repeat_sales_index', 'period_start', "
        "if_not_exists => TRUE)"
    )


def downgrade() -> None:
    op.drop_index("ix_rsi_lookup", table_name="repeat_sales_index")
    op.drop_table("repeat_sales_index")
