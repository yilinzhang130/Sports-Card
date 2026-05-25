from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
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
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_grading_ev_ev_per_dollar", "ev_per_dollar"),)
