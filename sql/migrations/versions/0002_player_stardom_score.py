"""player_stardom_score table

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
        "player_stardom_score",
        sa.Column(
            "player_id",
            sa.Integer,
            sa.ForeignKey("player_master.player_id"),
            primary_key=True,
        ),
        sa.Column("draft_year", sa.Integer, nullable=False),
        sa.Column("model_version", sa.String(32), nullable=False),
        sa.Column("premium", sa.Numeric(6, 4), nullable=False),
        sa.Column("percentile_rank", sa.Numeric(6, 4), nullable=False),
        sa.Column(
            "fit_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_player_stardom_score_draft_year",
        "player_stardom_score",
        ["draft_year"],
    )


def downgrade() -> None:
    op.drop_index("ix_player_stardom_score_draft_year", "player_stardom_score")
    op.drop_table("player_stardom_score")
