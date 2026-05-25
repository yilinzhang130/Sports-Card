"""prospect_forecast table for forward-looking pre-draft PRISM scores

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-25

Forward-looking projections for prospects who have not yet been drafted.
Composite PK (player_slug, draft_year, model_version, as_of_date) lets us
snapshot every re-score so we can backtest the model historically.
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
        "prospect_forecast",
        sa.Column("player_slug", sa.String(64), primary_key=True),
        sa.Column("draft_year", sa.Integer, primary_key=True),
        sa.Column("model_version", sa.String(32), primary_key=True),
        sa.Column("as_of_date", sa.Date, primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("premium", sa.Numeric(6, 4)),
        sa.Column("pairwise_score", sa.Numeric(10, 4)),
        sa.Column("consensus_rank", sa.Numeric(5, 2)),
        sa.Column("sources_count", sa.Integer),
        sa.Column("is_underclassman", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("years_until_draft", sa.Integer, nullable=False, server_default="0"),
        sa.Column("prior_league", sa.String(16), nullable=False, server_default="NCAA"),
        sa.Column("n_games_played", sa.Integer),
        sa.Column(
            "fit_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_prospect_forecast_draft_year",
        "prospect_forecast",
        ["draft_year", "as_of_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_prospect_forecast_draft_year", table_name="prospect_forecast")
    op.drop_table("prospect_forecast")
