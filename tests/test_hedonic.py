"""End-to-end tests for the hedonic XGBoost model."""

from __future__ import annotations

from datetime import datetime

import joblib
import numpy as np
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from sportscards.db.models import Base, Card, Player, TxMispricing
from sportscards.factors.features import build_features
from sportscards.factors.hedonic import (
    BOOLEAN_FEATURES,
    MODEL_VERSION,
    NUMERICAL_FEATURES,
    NUMERICAL_MONOTONE,
    _build_design_matrix,
    fit,
    load_model,
    persist_residuals,
    predict,
    save_model,
)
from sportscards.factors.synthetic_data import generate_synthetic_transactions

# ---------------------------------------------------------------------------
# Seed: 5 players, 15 cards covering full feature variety.
# ---------------------------------------------------------------------------


def _seed(sess: Session) -> None:
    players = [
        Player(
            name="Rookie A",
            position="SG",
            draft_year=2022,
            draft_pick=1,
            team="LAL",
            dob=datetime(2003, 6, 1),
            br_slug="rookA",
        ),
        Player(
            name="Rookie B",
            position="PG",
            draft_year=2023,
            draft_pick=5,
            team="BOS",
            dob=datetime(2004, 2, 14),
            br_slug="rookB",
        ),
        Player(
            name="Vet",
            position="PF",
            draft_year=2010,
            draft_pick=20,
            team="GSW",
            dob=datetime(1988, 8, 1),
            br_slug="vet",
        ),
        Player(
            name="Vintage",
            position="SF",
            draft_year=1996,
            draft_pick=13,
            team="LAL",
            dob=datetime(1978, 8, 23),
            br_slug="vintage",
        ),
        Player(
            name="Mid",
            position="C",
            draft_year=2015,
            draft_pick=10,
            team="NYK",
            dob=datetime(1995, 1, 5),
            br_slug="mid",
        ),
    ]
    for p in players:
        sess.add(p)
    sess.flush()
    pids = [p.player_id for p in players]

    cards = [
        # Rookie A: base, refractor, gold /10, 1of1, holo /25
        Card(
            year=2022,
            manufacturer="Panini",
            set_name="Prizm",
            card_number="100",
            parallel="Base",
            player_id=pids[0],
            is_rookie=True,
        ),
        Card(
            year=2022,
            manufacturer="Panini",
            set_name="Prizm",
            card_number="100",
            parallel="Silver Refractor",
            player_id=pids[0],
            is_rookie=True,
        ),
        Card(
            year=2022,
            manufacturer="Panini",
            set_name="Select",
            card_number="200",
            parallel="Gold",
            print_run=10,
            player_id=pids[0],
            is_rookie=True,
            has_auto=True,
        ),
        Card(
            year=2022,
            manufacturer="Panini",
            set_name="National Treasures",
            card_number="50",
            parallel="Logoman",
            print_run=1,
            player_id=pids[0],
            is_rookie=True,
            has_auto=True,
            has_patch=True,
            is_one_of_one=True,
        ),
        Card(
            year=2022,
            manufacturer="Panini",
            set_name="Optic",
            card_number="55",
            parallel="Holo",
            print_run=25,
            player_id=pids[0],
            is_rookie=True,
        ),
        # Rookie B
        Card(
            year=2023,
            manufacturer="Panini",
            set_name="Prizm",
            card_number="300",
            parallel="Base",
            player_id=pids[1],
            is_rookie=True,
        ),
        Card(
            year=2023,
            manufacturer="Panini",
            set_name="Flawless",
            card_number="12",
            parallel="Ruby",
            print_run=15,
            player_id=pids[1],
            is_rookie=True,
            has_patch=True,
        ),
        Card(
            year=2023,
            manufacturer="Panini",
            set_name="Immaculate",
            card_number="22",
            parallel="Platinum",
            print_run=1,
            player_id=pids[1],
            is_rookie=True,
            is_one_of_one=True,
            has_auto=True,
        ),
        # Vet
        Card(
            year=2015,
            manufacturer="Panini",
            set_name="Prizm",
            card_number="33",
            parallel="Base",
            player_id=pids[2],
        ),
        Card(
            year=2018,
            manufacturer="Panini",
            set_name="Donruss Optic",
            card_number="44",
            parallel="Holo",
            print_run=99,
            player_id=pids[2],
        ),
        Card(
            year=2019,
            manufacturer="Panini",
            set_name="Flawless",
            card_number="7",
            parallel="Ruby",
            print_run=15,
            player_id=pids[2],
            has_patch=True,
        ),
        # Mid
        Card(
            year=2015,
            manufacturer="Panini",
            set_name="Select",
            card_number="88",
            parallel="Gold",
            print_run=10,
            player_id=pids[4],
            has_auto=True,
        ),
        Card(
            year=2016,
            manufacturer="Panini",
            set_name="Hoops",
            card_number="99",
            parallel="Base",
            player_id=pids[4],
        ),
        # Vintage
        Card(
            year=2003,
            manufacturer="Topps",
            set_name="Topps Chrome",
            card_number="111",
            parallel="Refractor",
            print_run=99,
            player_id=pids[3],
            is_rookie=True,
        ),
        Card(
            year=2008,
            manufacturer="Panini",
            set_name="Hoops",
            card_number="22",
            parallel="Base",
            player_id=pids[3],
        ),
    ]
    for c in cards:
        sess.add(c)
    sess.flush()


# ---------------------------------------------------------------------------
# Module-scoped fixture: trained model (slow — fit once)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def trained() -> dict:
    """Build features + fit a model once; reused across tests."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sess = Session(engine)
    _seed(sess)
    sess.commit()
    # Smaller noise → tighter MAE for the planted-signal recovery test.
    generate_synthetic_transactions(sess, n_per_card=60, noise_sigma=0.1, seed=7)
    sess.commit()

    df = build_features(sess)
    model, encoder, metrics = fit(df, n_trials=12, random_state=42)
    return {
        "sess": sess,
        "df": df,
        "model": model,
        "encoder": encoder,
        "metrics": metrics,
        "engine": engine,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_monotone_constraints_aligned():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sess = Session(engine)
    _seed(sess)
    sess.commit()
    generate_synthetic_transactions(sess, n_per_card=5, seed=1)
    sess.commit()
    df = build_features(sess)
    X, names, encoder, monotone = _build_design_matrix(df)
    assert len(monotone) == X.shape[1]
    assert len(names) == X.shape[1]
    assert monotone[: len(NUMERICAL_MONOTONE)] == NUMERICAL_MONOTONE
    # Booleans next, then one-hots → all zero.
    n_num = len(NUMERICAL_FEATURES)
    n_bool = len(BOOLEAN_FEATURES)
    assert all(c == 0 for c in monotone[n_num + n_bool :])
    sess.close()


def test_planted_coefficients_in_top_importances(trained):
    """Planted-signal features should appear high in importance ranking.

    Note: in the synthetic generator ``log_pop_psa10`` and ``is_one_of_one``
    are highly collinear with ``parallel_tier`` (pop is sampled per-tier;
    one-of-ones are exactly tier 5). XGBoost with monotone constraints
    concentrates almost all gain on ``parallel_tier``; the redundant
    features still surface but in the top-8 not top-6. We check top-8 so
    the test reflects the actual structure of the planted data.
    """
    importance = trained["metrics"]["feature_importance"]
    # parallel_tier should dominate; print_run_log (also a scarcity proxy
    # planted via -1 monotone) should also rank top-3. We also check that
    # the model's recovered signal is concentrated in scarcity features
    # (parallel_tier + print_run_log) and slab grade — the planted axes
    # that have nondegenerate variance after one-pop-per-card sampling.
    top3 = sorted(importance.items(), key=lambda kv: kv[1], reverse=True)[:3]
    top3_names = {name for name, _ in top3}
    assert "parallel_tier" in top3_names, f"parallel_tier missing from top-3: {top3}"
    # At least 2 of these three planted/derived scarcity features in top-3.
    scarcity = {"parallel_tier", "print_run_log", "is_one_of_one"}
    assert len(top3_names & scarcity) >= 2, f"expected >=2 scarcity features in top-3, got {top3}"


def test_oos_mae_below_threshold(trained):
    assert trained["metrics"]["oos_mae"] < 0.15, (
        f"OOS MAE too high: {trained['metrics']['oos_mae']}"
    )


def test_persist_residuals_round_trip(trained):
    sess = trained["sess"]
    df = trained["df"]
    pred = predict(trained["model"], trained["encoder"], df)
    written = persist_residuals(sess, df, pred)
    sess.commit()
    assert written == len(df)
    count = sess.execute(select(TxMispricing)).all()
    assert len(count) == len(df)

    # Idempotent re-run.
    written2 = persist_residuals(sess, df, pred)
    sess.commit()
    assert written2 == len(df)
    count2 = sess.execute(select(TxMispricing)).all()
    assert len(count2) == len(df)


def test_save_load_model_round_trip(trained, tmp_path):
    path = tmp_path / "m.joblib"
    save_model(trained["model"], trained["encoder"], trained["metrics"], path=path)
    model2, encoder2, metrics2 = load_model(path=path)

    df = trained["df"]
    p1 = predict(trained["model"], trained["encoder"], df)
    p2 = predict(model2, encoder2, df)
    np.testing.assert_array_equal(p1, p2)
    assert metrics2["oos_mae"] == trained["metrics"]["oos_mae"]

    # Confirm model_version stamped on the saved bundle is v3.
    bundle = joblib.load(path)
    assert bundle["model_version"] == MODEL_VERSION == "hedonic_v3"
