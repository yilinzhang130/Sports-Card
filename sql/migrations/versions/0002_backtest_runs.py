"""backtest_runs table (Phase 4)

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-25

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
        "backtest_runs",
        sa.Column("run_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("params_json", sa.JSON, nullable=False, server_default="{}"),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("summary_json", sa.JSON, nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_table("backtest_runs")
