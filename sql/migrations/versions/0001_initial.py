"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-05-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")

    op.create_table(
        "player_master",
        sa.Column("player_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("position", sa.String(8)),
        sa.Column("draft_year", sa.Integer),
        sa.Column("draft_pick", sa.Integer),
        sa.Column("team", sa.String(64)),
        sa.Column("dob", sa.DateTime),
        sa.Column("br_slug", sa.String(32), unique=True),
    )
    op.create_index("ix_player_master_name", "player_master", ["name"])

    op.create_table(
        "card_master",
        sa.Column("card_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("year", sa.Integer, nullable=False),
        sa.Column("manufacturer", sa.String(32), nullable=False),
        sa.Column("set", sa.String(64), nullable=False),
        sa.Column("card_number", sa.String(16), nullable=False),
        sa.Column("parallel", sa.String(64), nullable=False, server_default="Base"),
        sa.Column("print_run", sa.Integer),
        sa.Column("player_id", sa.Integer, sa.ForeignKey("player_master.player_id")),
        sa.Column("is_rookie", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("has_auto", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("has_patch", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("is_one_of_one", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.UniqueConstraint(
            "year",
            "manufacturer",
            "set",
            "card_number",
            "parallel",
            name="uq_card_master_identity",
        ),
    )
    op.create_index("ix_card_master_player", "card_master", ["player_id"])

    op.create_table(
        "cardladder_spec_map",
        sa.Column("cl_spec_id", sa.String(64), primary_key=True),
        sa.Column("card_id", sa.Integer, sa.ForeignKey("card_master.card_id"), nullable=False),
    )

    op.create_table(
        "tx_raw",
        sa.Column("raw_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("raw_title", sa.Text, nullable=False),
        sa.Column("raw_price", sa.Numeric(14, 2)),
        sa.Column("raw_currency", sa.String(8), server_default="USD"),
        sa.Column("sold_at", sa.DateTime(timezone=True)),
        sa.Column("external_id", sa.String(128)),
        sa.Column("raw_json", sa.JSON, server_default="{}"),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("source", "external_id", name="uq_tx_raw_source_extid"),
    )
    op.create_index("ix_tx_raw_source", "tx_raw", ["source"])
    op.create_index("ix_tx_raw_sold_at", "tx_raw", ["sold_at"])
    op.create_index("ix_tx_raw_external_id", "tx_raw", ["external_id"])

    op.create_table(
        "tx_clean",
        sa.Column("tx_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "raw_id", sa.Integer, sa.ForeignKey("tx_raw.raw_id"), nullable=False, unique=True
        ),
        sa.Column("card_id", sa.Integer, sa.ForeignKey("card_master.card_id")),
        sa.Column("slab_grader", sa.String(8)),
        sa.Column("slab_grade", sa.Numeric(4, 1)),
        sa.Column("cert_number", sa.String(32)),
        sa.Column("price_usd", sa.Numeric(14, 2), nullable=False),
        sa.Column("sold_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fee_estimate", sa.Numeric(14, 2)),
        sa.Column("parser_confidence", sa.Numeric(4, 3), server_default="0"),
        sa.Column("parser_method", sa.String(16), nullable=False),
    )
    op.create_index("ix_tx_clean_card_id", "tx_clean", ["card_id"])
    op.create_index("ix_tx_clean_sold_at", "tx_clean", ["sold_at"])
    op.create_index("ix_tx_clean_cert_number", "tx_clean", ["cert_number"])

    op.create_table(
        "parse_failures",
        sa.Column("raw_id", sa.Integer, sa.ForeignKey("tx_raw.raw_id"), primary_key=True),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column(
            "attempted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "pop_snapshots",
        sa.Column("snapshot_date", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("card_id", sa.Integer, sa.ForeignKey("card_master.card_id"), primary_key=True),
        sa.Column("grader", sa.String(8), primary_key=True),
        sa.Column("grade", sa.Numeric(4, 1), primary_key=True),
        sa.Column("pop_count", sa.Integer, nullable=False),
    )
    op.execute("SELECT create_hypertable('pop_snapshots', 'snapshot_date', if_not_exists => TRUE)")


def downgrade() -> None:
    op.drop_table("pop_snapshots")
    op.drop_table("parse_failures")
    op.drop_table("tx_clean")
    op.drop_table("tx_raw")
    op.drop_table("cardladder_spec_map")
    op.drop_table("card_master")
    op.drop_table("player_master")
