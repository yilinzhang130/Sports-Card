"""player_events table for catalyst event overlay

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-25

Holds catalyst events (injuries, awards, playoff results, transactions, etc)
keyed by player. Downstream catalyst-score features read from this table.
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
        "player_events",
        sa.Column("event_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "player_id",
            sa.Integer,
            sa.ForeignKey("player_master.player_id"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("event_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "event_payload",
            sa.JSON,
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "player_id",
            "event_type",
            "event_date",
            name="uq_player_events_dedupe",
        ),
    )
    op.create_index(
        "ix_player_events_player_date",
        "player_events",
        ["player_id", "event_date"],
    )
    op.create_index("ix_player_events_event_date", "player_events", ["event_date"])


def downgrade() -> None:
    op.drop_index("ix_player_events_event_date", table_name="player_events")
    op.drop_index("ix_player_events_player_date", table_name="player_events")
    op.drop_table("player_events")
