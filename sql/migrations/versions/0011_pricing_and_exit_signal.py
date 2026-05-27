"""pricing: trade_targets, exit_signal, portfolio_holdings extensions"""

import contextlib

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portfolio_holdings",
        sa.Column("entry_factor_decile", sa.SmallInteger, nullable=True),
    )
    op.add_column(
        "portfolio_holdings",
        sa.Column("entry_liquidity_tier", sa.String(1), nullable=True),
    )

    op.create_table(
        "trade_targets",
        sa.Column("card_id", sa.Integer, sa.ForeignKey("card_master.card_id"), nullable=False),
        sa.Column("as_of_date", sa.Date, nullable=False),
        sa.Column("fair_value", sa.Numeric(12, 2), nullable=False),
        sa.Column("bid_max", sa.Numeric(12, 2), nullable=False),
        sa.Column("sell_target", sa.Numeric(12, 2), nullable=False),
        sa.Column("stop_loss", sa.Numeric(12, 2), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("half_spread_pct", sa.Numeric(6, 4), nullable=False),
        sa.Column("liquidity_margin_pct", sa.Numeric(6, 4), nullable=False),
        sa.PrimaryKeyConstraint("card_id", "as_of_date"),
    )
    if op.get_bind().dialect.name == "postgresql":
        with contextlib.suppress(Exception):
            op.execute(
                "SELECT create_hypertable('trade_targets', 'as_of_date', if_not_exists => TRUE)"
            )

    op.create_table(
        "exit_signal",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer, "sqlite"),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column(
            "holding_id",
            sa.Integer,
            sa.ForeignKey("portfolio_holdings.holding_id"),
            nullable=False,
        ),
        sa.Column("rule_triggered", sa.Text, nullable=False),
        sa.Column("recommended_action", sa.Text, nullable=False),
        sa.Column("as_of_date", sa.Date, nullable=False),
        sa.Column("notes", sa.Text),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "holding_id",
            "rule_triggered",
            "as_of_date",
            name="uq_exit_signal_holding_rule_day",
        ),
    )
    # Partial index on unresolved signals — PostgreSQL only
    if op.get_bind().dialect.name == "postgresql":
        op.create_index(
            "ix_exit_signal_unresolved",
            "exit_signal",
            ["resolved_at"],
            postgresql_where=sa.text("resolved_at IS NULL"),
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.drop_index("ix_exit_signal_unresolved", table_name="exit_signal")
    op.drop_table("exit_signal")
    op.drop_table("trade_targets")
    op.drop_column("portfolio_holdings", "entry_liquidity_tier")
    op.drop_column("portfolio_holdings", "entry_factor_decile")
