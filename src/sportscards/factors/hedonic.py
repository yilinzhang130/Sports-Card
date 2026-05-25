"""Hedonic fair-value XGBoost model with monotone constraints.

Trains a gradient-boosted regressor on the feature matrix produced by
``sportscards.factors.features.build_features``. Uses Optuna for a small
hyperparameter search with a time-aware holdout, persists per-transaction
residuals (predicted - actual log price) to ``tx_mispricing``, and pickles
the (model, encoder, metrics) bundle to disk.
"""

from __future__ import annotations

import contextlib
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import optuna
import pandas as pd
import xgboost as xgb
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.preprocessing import OneHotEncoder
from sqlalchemy import delete
from sqlalchemy.orm import Session

from sportscards.db.models import TxMispricing

MODEL_VERSION = "hedonic_v3"
MODEL_PATH = Path("models/hedonic_v3.joblib")


# Order matters — `MONOTONE_CONSTRAINTS` is index-aligned with the
# numerical-feature portion. Categorical one-hot columns are appended
# AFTER these, with constraint 0 each, computed at fit time.
NUMERICAL_FEATURES: list[str] = [
    "log_pop_psa10",  # -1: pop up → price down
    "log_pop_psa9_or_better",  # -1: same
    "parallel_tier",  # +1: scarcer → pricier
    "print_run_log",  # -1: more printed → cheaper
    "slab_grade",  #  0
    "player_age_at_sale",  #  0
    "years_since_draft",  #  0
    "draft_pick",  #  0 (lower pick = better player but signal is noisy)
    "catalyst_score",  #  0 (context-dependent; can flip sign)
    "catalyst_score_30d_change",  #  0
]
NUMERICAL_MONOTONE: tuple[int, ...] = (-1, -1, +1, -1, 0, 0, 0, 0, 0, 0)

BOOLEAN_FEATURES: list[str] = [
    "is_rookie",
    "has_auto",
    "has_patch",
    "is_one_of_one",
    "era_modern",
]
BOOLEAN_MONOTONE: tuple[int, ...] = (0, 0, 0, 0, 0)

CATEGORICAL_FEATURES: list[str] = ["set_tier", "team_market", "slab_grader"]


def _build_design_matrix(
    df: pd.DataFrame,
    *,
    encoder: OneHotEncoder | None = None,
) -> tuple[np.ndarray, list[str], OneHotEncoder, tuple[int, ...]]:
    """Return (X, feature_names, fitted_encoder, monotone_constraints)."""
    num_block = df[NUMERICAL_FEATURES].to_numpy(dtype=float)
    bool_block = df[BOOLEAN_FEATURES].to_numpy(dtype=float)

    if encoder is None:
        encoder = OneHotEncoder(handle_unknown="ignore", sparse_output=False)
        cat_block = encoder.fit_transform(df[CATEGORICAL_FEATURES])
    else:
        cat_block = encoder.transform(df[CATEGORICAL_FEATURES])

    cat_names = list(encoder.get_feature_names_out(CATEGORICAL_FEATURES))
    feature_names = NUMERICAL_FEATURES + BOOLEAN_FEATURES + cat_names

    X = np.hstack([num_block, bool_block, cat_block])
    monotone = NUMERICAL_MONOTONE + BOOLEAN_MONOTONE + (0,) * len(cat_names)
    return X, feature_names, encoder, monotone


def fit(
    df: pd.DataFrame,
    *,
    train_end: date | datetime | None = None,
    n_trials: int = 20,
    random_state: int = 42,
) -> tuple[xgb.XGBRegressor, OneHotEncoder, dict[str, Any]]:
    """Train an XGBoost hedonic model with time-aware split + Optuna search.

    Returns (model, fitted_encoder, metrics).
    metrics = {"oos_mae", "oos_r2", "n_train", "n_test",
               "feature_importance": {name: weight}, "best_params": {...}}
    """
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    df = df.sort_values("sold_at").reset_index(drop=True)
    sold_at = pd.to_datetime(df["sold_at"])

    # --- determine train/test cutoff ---
    cutoff = sold_at.quantile(0.8) if train_end is None else pd.Timestamp(train_end)

    # Make comparable (both tz-naive)
    try:
        sold_at_cmp = sold_at.dt.tz_localize(None)
    except (TypeError, AttributeError):
        sold_at_cmp = sold_at
    with contextlib.suppress(TypeError, AttributeError):
        cutoff = cutoff.tz_localize(None)  # type: ignore[union-attr]

    train_mask = (sold_at_cmp < cutoff).to_numpy()  # type: ignore[operator]
    test_mask = (sold_at_cmp >= cutoff).to_numpy()  # type: ignore[operator]

    if train_mask.sum() == 0 or test_mask.sum() == 0:
        # Fallback: chronological 80/20 by index
        n = len(df)
        split_idx = max(1, int(n * 0.8))
        train_mask = np.zeros(n, dtype=bool)
        test_mask = np.zeros(n, dtype=bool)
        train_mask[:split_idx] = True
        test_mask[split_idx:] = True

    # Fit encoder on the FULL df so test-set categorical values are known.
    X_all, feature_names, encoder, monotone = _build_design_matrix(df)
    y_all = df["log_price"].to_numpy(dtype=float)

    X_train, y_train = X_all[train_mask], y_all[train_mask]
    X_test, y_test = X_all[test_mask], y_all[test_mask]

    def objective(trial: optuna.Trial) -> float:
        params = {
            "n_estimators": trial.suggest_int("n_estimators", 200, 800, step=100),
            "max_depth": trial.suggest_int("max_depth", 3, 8),
            "learning_rate": trial.suggest_float("learning_rate", 0.02, 0.2, log=True),
        }
        m = xgb.XGBRegressor(
            **params,
            monotone_constraints=monotone,
            random_state=random_state,
            n_jobs=1,
            tree_method="hist",
            verbosity=0,
        )
        m.fit(X_train, y_train)
        pred = m.predict(X_test)
        return float(mean_absolute_error(y_test, pred))

    study = optuna.create_study(
        direction="minimize",
        sampler=optuna.samplers.TPESampler(seed=random_state),
    )
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)

    best_params = study.best_params

    # Evaluate held-out metrics using a model trained on train only.
    eval_model = xgb.XGBRegressor(
        **best_params,
        monotone_constraints=monotone,
        random_state=random_state,
        n_jobs=1,
        tree_method="hist",
        verbosity=0,
        importance_type="gain",
    )
    eval_model.fit(X_train, y_train)
    test_pred = eval_model.predict(X_test)
    oos_mae = float(mean_absolute_error(y_test, test_pred))
    oos_r2 = float(r2_score(y_test, test_pred))

    # Final refit on full data for production.
    final_model = xgb.XGBRegressor(
        **best_params,
        monotone_constraints=monotone,
        random_state=random_state,
        n_jobs=1,
        tree_method="hist",
        verbosity=0,
        importance_type="gain",
    )
    final_model.fit(X_all, y_all)

    importance = dict(zip(feature_names, final_model.feature_importances_.tolist(), strict=True))

    metrics: dict[str, Any] = {
        "oos_mae": oos_mae,
        "oos_r2": oos_r2,
        "n_train": int(train_mask.sum()),
        "n_test": int(test_mask.sum()),
        "feature_importance": importance,
        "best_params": best_params,
    }
    return final_model, encoder, metrics


def predict(
    model: xgb.XGBRegressor,
    encoder: OneHotEncoder,
    df: pd.DataFrame,
) -> np.ndarray:
    """Predict log price for each row in df."""
    X, _, _, _ = _build_design_matrix(df, encoder=encoder)
    return np.asarray(model.predict(X))


def persist_residuals(
    session: Session,
    df: pd.DataFrame,
    predicted_log_price: np.ndarray,
    *,
    model_version: str = MODEL_VERSION,
) -> int:
    """Write/replace TxMispricing rows for (tx_id, model_version).

    residual = predicted_log_price - actual_log_price
    """
    if len(df) == 0:
        return 0

    tx_ids = [int(t) for t in df["tx_id"].tolist()]
    actual = df["log_price"].to_numpy(dtype=float)
    predicted = np.asarray(predicted_log_price, dtype=float)
    residuals = predicted - actual

    session.execute(
        delete(TxMispricing)
        .where(TxMispricing.model_version == model_version)
        .where(TxMispricing.tx_id.in_(tx_ids))
    )

    for tx_id, pred, resid in zip(tx_ids, predicted, residuals, strict=True):
        session.add(
            TxMispricing(
                tx_id=tx_id,
                model_version=model_version,
                residual=Decimal(str(round(float(resid), 6))),
                predicted_log_price=Decimal(str(round(float(pred), 6))),
            )
        )

    session.flush()
    return len(tx_ids)


def save_model(
    model: xgb.XGBRegressor,
    encoder: OneHotEncoder,
    metrics: dict[str, Any],
    path: Path = MODEL_PATH,
) -> None:
    """Joblib-dump {model, encoder, metrics, model_version}."""
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "model": model,
            "encoder": encoder,
            "metrics": metrics,
            "model_version": MODEL_VERSION,
        },
        path,
    )


def load_model(
    path: Path = MODEL_PATH,
) -> tuple[xgb.XGBRegressor, OneHotEncoder, dict[str, Any]]:
    """Joblib-load the bundle. Raises FileNotFoundError if missing."""
    if not Path(path).exists():
        raise FileNotFoundError(str(path))
    bundle = joblib.load(path)
    return bundle["model"], bundle["encoder"], bundle["metrics"]
