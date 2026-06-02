"""card identity candidates"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "card_identity_candidates",
        sa.Column("raw_id", sa.Integer, sa.ForeignKey("tx_raw.raw_id"), primary_key=True),
        sa.Column("canonical_key", sa.String(256), nullable=False),
        sa.Column("player_name", sa.String(128)),
        sa.Column("manufacturer", sa.String(32)),
        sa.Column("year", sa.Integer),
        sa.Column("set", sa.String(64)),
        sa.Column("subset", sa.String(64)),
        sa.Column("card_number", sa.String(32)),
        sa.Column("parallel", sa.String(128)),
        sa.Column("print_run", sa.Integer),
        sa.Column("is_rookie", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("has_auto", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("has_patch", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("slab_grader", sa.String(8)),
        sa.Column("slab_grade", sa.Numeric(4, 1)),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("needs_review", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("evidence_json", sa.JSON, nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_card_identity_candidates_canonical_key",
        "card_identity_candidates",
        ["canonical_key"],
    )
    op.create_index(
        "ix_card_identity_candidates_player_name",
        "card_identity_candidates",
        ["player_name"],
    )
    op.create_index(
        "ix_card_identity_candidates_key_grade",
        "card_identity_candidates",
        ["canonical_key", "slab_grader", "slab_grade"],
    )


def downgrade() -> None:
    op.drop_index("ix_card_identity_candidates_key_grade", table_name="card_identity_candidates")
    op.drop_index("ix_card_identity_candidates_player_name", table_name="card_identity_candidates")
    op.drop_index(
        "ix_card_identity_candidates_canonical_key",
        table_name="card_identity_candidates",
    )
    op.drop_table("card_identity_candidates")
