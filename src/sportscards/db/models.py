from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Player(Base):
    __tablename__ = "player_master"

    player_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    position: Mapped[str | None] = mapped_column(String(8))
    draft_year: Mapped[int | None] = mapped_column(Integer)
    draft_pick: Mapped[int | None] = mapped_column(Integer)
    team: Mapped[str | None] = mapped_column(String(64))
    dob: Mapped[datetime | None] = mapped_column(DateTime)
    br_slug: Mapped[str | None] = mapped_column(String(32), unique=True)

    cards: Mapped[list[Card]] = relationship(back_populates="player")


class Card(Base):
    __tablename__ = "card_master"

    card_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    manufacturer: Mapped[str] = mapped_column(String(32), nullable=False)
    set_name: Mapped[str] = mapped_column("set", String(64), nullable=False)
    card_number: Mapped[str] = mapped_column(String(16), nullable=False)
    parallel: Mapped[str] = mapped_column(String(64), default="Base", nullable=False)
    print_run: Mapped[int | None] = mapped_column(Integer)
    player_id: Mapped[int] = mapped_column(ForeignKey("player_master.player_id"))
    is_rookie: Mapped[bool] = mapped_column(Boolean, default=False)
    has_auto: Mapped[bool] = mapped_column(Boolean, default=False)
    has_patch: Mapped[bool] = mapped_column(Boolean, default=False)
    is_one_of_one: Mapped[bool] = mapped_column(Boolean, default=False)

    player: Mapped[Player] = relationship(back_populates="cards")

    __table_args__ = (
        UniqueConstraint(
            "year",
            "manufacturer",
            "set",
            "card_number",
            "parallel",
            name="uq_card_master_identity",
        ),
        Index("ix_card_master_player", "player_id"),
    )


class CardLadderSpecMap(Base):
    __tablename__ = "cardladder_spec_map"

    cl_spec_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("card_master.card_id"), nullable=False)


class TxRaw(Base):
    __tablename__ = "tx_raw"

    raw_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    raw_title: Mapped[str] = mapped_column(Text, nullable=False)
    raw_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    raw_currency: Mapped[str] = mapped_column(String(8), default="USD")
    sold_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    external_id: Mapped[str | None] = mapped_column(String(128), index=True)
    raw_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_tx_raw_source_extid"),)


class TxClean(Base):
    __tablename__ = "tx_clean"

    tx_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    raw_id: Mapped[int] = mapped_column(ForeignKey("tx_raw.raw_id"), nullable=False, unique=True)
    card_id: Mapped[int | None] = mapped_column(ForeignKey("card_master.card_id"), index=True)
    slab_grader: Mapped[str | None] = mapped_column(String(8))
    slab_grade: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    cert_number: Mapped[str | None] = mapped_column(String(32), index=True)
    price_usd: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    sold_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    fee_estimate: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    parser_confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), default=Decimal("0"))
    parser_method: Mapped[str] = mapped_column(String(16))


class CardIdentityCandidate(Base):
    __tablename__ = "card_identity_candidates"

    raw_id: Mapped[int] = mapped_column(ForeignKey("tx_raw.raw_id"), primary_key=True)
    canonical_key: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    player_name: Mapped[str | None] = mapped_column(String(128), index=True)
    manufacturer: Mapped[str | None] = mapped_column(String(32))
    year: Mapped[int | None] = mapped_column(Integer)
    set_name: Mapped[str | None] = mapped_column("set", String(64))
    subset: Mapped[str | None] = mapped_column(String(64))
    card_number: Mapped[str | None] = mapped_column(String(32))
    parallel: Mapped[str | None] = mapped_column(String(128))
    print_run: Mapped[int | None] = mapped_column(Integer)
    is_rookie: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_auto: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_patch: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    slab_grader: Mapped[str | None] = mapped_column(String(8))
    slab_grade: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index(
            "ix_card_identity_candidates_key_grade",
            "canonical_key",
            "slab_grader",
            "slab_grade",
        ),
    )


class ParseFailure(Base):
    __tablename__ = "parse_failures"

    raw_id: Mapped[int] = mapped_column(ForeignKey("tx_raw.raw_id"), primary_key=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    run_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    params_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class PlayerStardomScore(Base):
    """Per-player stardom-premium score from the PRISM-style scouting model.

    `premium` is the model's pairwise percentile minus the draft-slot prior.
    Positive ⇒ the market underprices this prospect; negative ⇒ overprices.
    """

    __tablename__ = "player_stardom_score"

    player_id: Mapped[int] = mapped_column(ForeignKey("player_master.player_id"), primary_key=True)
    model_version: Mapped[str] = mapped_column(String(32), primary_key=True)
    draft_year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    premium: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    percentile_rank: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    fit_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProspectForecast(Base):
    """Forward-looking pre-draft prospect score (PRISM, undrafted prospects).

    Snapshotted by ``as_of_date`` so we can backtest "would we have found
    Wemby in 2022?" later. ``player_slug`` is NOT a foreign key — the player
    may not yet exist in ``player_master`` (they have not been drafted).
    """

    __tablename__ = "prospect_forecast"

    player_slug: Mapped[str] = mapped_column(String(64), primary_key=True)
    draft_year: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_version: Mapped[str] = mapped_column(String(32), primary_key=True)
    as_of_date: Mapped[datetime] = mapped_column(Date, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    premium: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    pairwise_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    consensus_rank: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    sources_count: Mapped[int | None] = mapped_column(Integer)
    is_underclassman: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    years_until_draft: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prior_league: Mapped[str] = mapped_column(String(16), nullable=False, default="NCAA")
    n_games_played: Mapped[int | None] = mapped_column(Integer)
    fit_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_prospect_forecast_draft_year", "draft_year", "as_of_date"),)


class PopSnapshot(Base):
    """TimescaleDB hypertable on snapshot_date."""

    __tablename__ = "pop_snapshots"

    snapshot_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("card_master.card_id"), primary_key=True)
    grader: Mapped[str] = mapped_column(String(8), primary_key=True)
    grade: Mapped[Decimal] = mapped_column(Numeric(4, 1), primary_key=True)
    pop_count: Mapped[int] = mapped_column(Integer, nullable=False)


class TxMispricing(Base):
    __tablename__ = "tx_mispricing"

    tx_id: Mapped[int] = mapped_column(ForeignKey("tx_clean.tx_id"), primary_key=True)
    model_version: Mapped[str] = mapped_column(String(32), primary_key=True)
    residual: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    predicted_log_price: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    fit_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_tx_mispricing_residual", "residual"),)


class PlayerEventType(StrEnum):
    """Allowed `event_type` values for `player_events`.

    Validated in application code, not enforced at the DB level (the column is a
    plain VARCHAR(32) for forward-compat with future event categories).
    """

    # Injuries
    INJURY_OUT = "injury_out"
    INJURY_DTD = "injury_dtd"
    INJURY_RETURN = "injury_return"

    # Playoffs
    PLAYOFF_WIN = "playoff_win"
    PLAYOFF_SERIES_WIN = "playoff_series_win"
    PLAYOFF_FINALS_WIN = "playoff_finals_win"

    # Awards
    MVP = "mvp"
    ROY = "roy"
    DPOY = "dpoy"
    ALL_STAR = "all_star"
    ALL_NBA_1ST = "all_nba_1st"
    ALL_NBA_2ND = "all_nba_2nd"
    ALL_NBA_3RD = "all_nba_3rd"
    HOF = "hof"

    # Transactions
    CALL_UP = "call_up"
    TWO_WAY = "two_way"
    TRADED = "traded"
    SIGNED = "signed"


class PlayerEvent(Base):
    """Catalyst events keyed by player.

    Re-running an ingestor for the same (player, event_type, event_date) is a
    no-op thanks to `uq_player_events_dedupe`.
    """

    __tablename__ = "player_events"

    event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("player_master.player_id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    event_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "player_id",
            "event_type",
            "event_date",
            name="uq_player_events_dedupe",
        ),
        Index("ix_player_events_player_date", "player_id", "event_date"),
        Index("ix_player_events_event_date", "event_date"),
    )


class FactorPanel(Base):
    """Per-card momentum + liquidity factor snapshot. Timescale hypertable on as_of_date."""

    __tablename__ = "factor_panel"

    card_id: Mapped[int] = mapped_column(ForeignKey("card_master.card_id"), primary_key=True)
    as_of_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    r30: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    r90: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    r365: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    cs_momentum_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    is_hyped: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sales_count_90d: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    dollar_volume_90d: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    bid_ask_proxy: Mapped[Decimal | None] = mapped_column(Numeric(8, 5))
    last_sale_recency_days: Mapped[int | None] = mapped_column(Integer)
    liquidity_tier: Mapped[str] = mapped_column(String(1), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_factor_panel_as_of", "as_of_date"),
        Index("ix_factor_panel_card_asof", "card_id", "as_of_date"),
    )


class RepeatSalesIndex(Base):
    """Cert-based repeat-sales index. TimescaleDB hypertable on period_start.

    One row per (period_start, sport, bucket, grade_tier, era).
    """

    __tablename__ = "repeat_sales_index"

    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    sport: Mapped[str] = mapped_column(String(8), primary_key=True)
    bucket: Mapped[str] = mapped_column(String(8), primary_key=True)
    grade_tier: Mapped[str] = mapped_column(String(8), primary_key=True)
    era: Mapped[str] = mapped_column(String(8), primary_key=True)
    index_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    n_pairs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    se: Mapped[Decimal | None] = mapped_column(Numeric(8, 5))

    __table_args__ = (Index("ix_rsi_lookup", "sport", "grade_tier", "era", "bucket"),)


class PortfolioOverride(Base):
    """Trader-Console manual weight overrides per card."""

    __tablename__ = "portfolio_overrides"

    card_id: Mapped[int] = mapped_column(ForeignKey("card_master.card_id"), primary_key=True)
    target_weight_pct: Mapped[Decimal] = mapped_column(Numeric(8, 5), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    set_by: Mapped[str] = mapped_column(String(64), nullable=False, server_default="ui")
    set_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PortfolioHolding(Base):
    """Physical slab holdings tracked by the Trader Console."""

    __tablename__ = "portfolio_holdings"

    holding_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("card_master.card_id"), nullable=False)
    cert_number: Mapped[str | None] = mapped_column(String(32))
    slab_grader: Mapped[str | None] = mapped_column(String(16))
    slab_grade: Mapped[Decimal | None] = mapped_column(Numeric(3, 1))
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    acquired_cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="held")
    sold_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sold_proceeds_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    entry_factor_decile: Mapped[int | None] = mapped_column(SmallInteger)
    entry_liquidity_tier: Mapped[str | None] = mapped_column(String(1))

    __table_args__ = (
        Index("ix_holdings_card", "card_id"),
        Index("ix_holdings_status", "status"),
    )


class ModelRunLog(Base):
    """Log of model/pipeline runs triggered from the Trader Console."""

    __tablename__ = "model_run_log"

    run_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    params_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, server_default="{}")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="running")
    summary_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_runlog_action_status", "action", "status"),)


class AuditLog(Base):
    """Immutable audit trail for all write actions in the Trader Console."""

    __tablename__ = "audit_log"

    audit_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class GradingEv(Base):
    """TimescaleDB hypertable on as_of_date — daily grading optionality EVs."""

    __tablename__ = "grading_ev"

    card_id: Mapped[int] = mapped_column(ForeignKey("card_master.card_id"), primary_key=True)
    as_of_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    grade_tier: Mapped[str] = mapped_column(String(16), primary_key=True)
    gem_rate: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    p10_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    p9_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    cost_to_grade: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    raw_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    ev: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    ev_per_dollar: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    p10_pop: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (Index("ix_grading_ev_ev_per_dollar", "ev_per_dollar"),)


class TradeTargets(Base):
    """Per-card daily pricing targets: fair value, bid ceiling, sell target, stop loss."""

    __tablename__ = "trade_targets"

    card_id: Mapped[int] = mapped_column(ForeignKey("card_master.card_id"), primary_key=True)
    as_of_date: Mapped[date] = mapped_column(Date, primary_key=True)
    fair_value: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    bid_max: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    sell_target: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    stop_loss: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    half_spread_pct: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    liquidity_margin_pct: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)


class ExitSignal(Base):
    """Exit signals triggered by pricing rules for portfolio holdings."""

    __tablename__ = "exit_signal"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    holding_id: Mapped[int] = mapped_column(
        ForeignKey("portfolio_holdings.holding_id"), nullable=False
    )
    rule_triggered: Mapped[str] = mapped_column(Text, nullable=False)
    recommended_action: Mapped[str] = mapped_column(Text, nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "holding_id",
            "rule_triggered",
            "as_of_date",
            name="uq_exit_signal_holding_rule_day",
        ),
    )
