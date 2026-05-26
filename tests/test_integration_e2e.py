"""Cross-phase end-to-end integration test.

Wipes the Postgres/TimescaleDB instance, re-runs the full pipeline in
process (seed → fit-hedonic synthetic → factor compute-panel → index
build → portfolio plan) and asserts each step produces non-empty output.

Postgres-only — gated on ``RUN_INTEGRATION=1``. Assumes the docker
compose ``db`` service is running and DATABASE_URL points at it.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import delete, func, select

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_INTEGRATION") != "1",
        reason="requires Postgres/TimescaleDB (set RUN_INTEGRATION=1)",
    ),
]


@pytest.fixture
def fresh_pg(monkeypatch):
    url = os.environ.get(
        "DATABASE_URL", "postgresql+psycopg://sportscards:sportscards@localhost:5433/sportscards"
    )
    monkeypatch.setenv("DATABASE_URL", url)
    import sportscards.db.session as _sess
    from sportscards.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(_sess, "_engine", None)
    monkeypatch.setattr(_sess, "_SessionLocal", None)

    from sportscards.db.models import (
        Card,
        FactorPanel,
        ParseFailure,
        Player,
        PopSnapshot,
        RepeatSalesIndex,
        TxClean,
        TxMispricing,
        TxRaw,
    )

    # Wipe transactional + master tables in FK-safe order.
    with _sess.session_scope() as s:
        s.execute(delete(TxMispricing))
        s.execute(delete(FactorPanel))
        s.execute(delete(RepeatSalesIndex))
        s.execute(delete(PopSnapshot))
        s.execute(delete(ParseFailure))
        s.execute(delete(TxClean))
        s.execute(delete(TxRaw))
        s.execute(delete(Card))
        s.execute(delete(Player))
    yield _sess


def test_full_pipeline_end_to_end(fresh_pg):
    """Seed → hedonic → factor panel → index → portfolio. Each step must do work."""
    sess_mod = fresh_pg

    # 1) Seed players + cards
    from sportscards.master.seed import seed_cards, seed_players

    n_players = seed_players()
    n_cards = seed_cards()
    assert n_players > 0, "seed_players should report >0 inserts on a fresh DB"
    assert n_cards > 0, "seed_cards should report >0 inserts on a fresh DB"

    # 2) Generate synthetic tx + fit hedonic + persist residuals
    from sportscards.factors.features import build_features
    from sportscards.factors.hedonic import fit, persist_residuals, predict
    from sportscards.factors.synthetic_data import generate_synthetic_transactions

    with sess_mod.session_scope() as s:
        n_tx = generate_synthetic_transactions(s)
    assert n_tx > 0, "synthetic generator should write tx rows"

    from sportscards.db.models import TxClean, TxMispricing

    with sess_mod.session_scope() as s:
        # Sanity: repeat-sales cert pairs exist
        repeat_certs = s.execute(
            select(TxClean.cert_number, func.count())
            .where(TxClean.cert_number.is_not(None))
            .group_by(TxClean.cert_number)
            .having(func.count() > 1)
        ).all()
        assert len(repeat_certs) > 0, "synthetic data must contain repeat sales"

        df = build_features(s)
    assert not df.empty
    model_obj, encoder, metrics = fit(df, n_trials=5)
    pred = predict(model_obj, encoder, df)
    with sess_mod.session_scope() as s:
        written = persist_residuals(s, df, pred)
    assert written > 0

    with sess_mod.session_scope() as s:
        n_mispricing = s.execute(select(func.count()).select_from(TxMispricing)).scalar_one()
    assert n_mispricing > 0, "tx_mispricing must be populated"

    # 3) Factor panel — exercises the Timescale bulk-insert fix
    from sportscards.db.models import FactorPanel
    from sportscards.factors.factor_panel import persist_panel

    as_of = datetime.now(tz=UTC).replace(tzinfo=None)
    with sess_mod.session_scope() as s:
        n_panel = persist_panel(s, as_of)
    with sess_mod.session_scope() as s:
        actual_panel = s.execute(select(func.count()).select_from(FactorPanel)).scalar_one()
    assert n_panel > 0, "factor_panel should be populated"
    assert actual_panel == n_panel

    # 4) Repeat-sales index — exercises the synthetic-data repeat-cert fix
    from sportscards.db.models import RepeatSalesIndex
    from sportscards.factors.index_build import build_and_persist

    stats = build_and_persist(sport="NBA", bucket="weekly", replace=True)
    assert sum(stats.values()) > 0, f"index build returned 0 rows for all partitions: {stats}"
    with sess_mod.session_scope() as s:
        n_index = s.execute(select(func.count()).select_from(RepeatSalesIndex)).scalar_one()
    assert n_index > 0

    # 5) Portfolio plan — exercises the load_mispricing schema fix
    import pandas as pd

    from sportscards.portfolio.adapters import (
        load_anchors,
        load_mispricing,
        load_stardom,
    )
    from sportscards.portfolio.construction import (
        AllocationConfig,
        UniverseSnapshot,
        build_portfolio,
    )

    now = pd.Timestamp.utcnow()
    with sess_mod.session_scope() as s:
        anchors = load_anchors(s)
        mispricing = load_mispricing(s, now)
        stardom = load_stardom(s, now)
    assert mispricing is not None and not mispricing.empty, "load_mispricing returned no rows"
    assert set(mispricing.columns) >= {
        "card_id",
        "mispricing_residual",
        "computed_at",
        "sport",
        "parallel_tier",
    }

    positions = build_portfolio(
        UniverseSnapshot(anchors_df=anchors, factor_df=mispricing, prospect_df=stardom),
        AllocationConfig(total_aum_usd=100_000.0),
    )
    assert len(positions) > 0, "portfolio plan produced no positions"
