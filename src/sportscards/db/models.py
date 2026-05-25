from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
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


class PlayerEventType(str, Enum):
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
    player_id: Mapped[int] = mapped_column(
        ForeignKey("player_master.player_id"), nullable=False
    )
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
