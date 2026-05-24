"""Pairwise CatBoost ranker — Silver-style PRISM design.

Pairwise framing avoids the "regress-to-the-training-mean" failure of
absolute-target gradient boosting on long-tail outcomes (Luka, Jokic).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

MODEL_VERSION = "prism_v1"
MODEL_PATH = Path("models/prism_v1.cbm")

TRAIN_YEARS = range(2010, 2023)
VALID_YEARS = range(2023, 2025)


def train_pairwise_model(
    X: pd.DataFrame,
    y: pd.Series,
    groups: pd.Series,
    train_years: range = TRAIN_YEARS,
    valid_years: range = VALID_YEARS,
    iterations: int = 500,
) -> Any:
    """Fit a CatBoost ranker with ``PairLogit`` loss, group_id = draft_year.

    Splits by ``groups``: 2010-2022 train, 2023-2024 holdout. If the holdout
    is empty (e.g. tests with synthetic 2018 data only) it is skipped.
    """
    from catboost import CatBoostRanker, Pool

    train_mask = groups.isin(train_years)
    valid_mask = groups.isin(valid_years)
    if not train_mask.any():
        logger.warning(
            "no rows fall in train_years=%s; training on ALL rows with NO validation "
            "(reported concordance will be in-sample)",
            list(train_years),
        )
        train_mask = pd.Series(True, index=groups.index)
        valid_mask = pd.Series(False, index=groups.index)

    train_pool = Pool(
        data=X.loc[train_mask].values,
        label=y.loc[train_mask].values,
        group_id=groups.loc[train_mask].values,
    )
    eval_pool = None
    if valid_mask.any():
        eval_pool = Pool(
            data=X.loc[valid_mask].values,
            label=y.loc[valid_mask].values,
            group_id=groups.loc[valid_mask].values,
        )

    model = CatBoostRanker(
        loss_function="PairLogit",
        iterations=iterations,
        depth=4,
        learning_rate=0.05,
        verbose=False,
        allow_writing_files=False,
        random_seed=42,
    )
    model.fit(train_pool, eval_set=eval_pool)
    return model


def predict_scores(model: Any, X: pd.DataFrame) -> np.ndarray:
    return np.asarray(model.predict(X.values))


def save_model(model: Any, path: Path = MODEL_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    model.save_model(str(path))
    logger.info("saved model → %s", path)
    return path


def load_model(path: Path = MODEL_PATH) -> Any:
    from catboost import CatBoostRanker

    model = CatBoostRanker()
    model.load_model(str(path))
    return model


def concordance(scores: np.ndarray, y: np.ndarray, groups: np.ndarray) -> float:
    """Fraction of within-group pairs where score ordering matches target ordering."""
    score_a = np.asarray(scores)
    y_a = np.asarray(y)
    g_a = np.asarray(groups)
    agree = 0
    total = 0
    for g in np.unique(g_a):
        idx = np.where(g_a == g)[0]
        for i in range(len(idx)):
            for j in range(i + 1, len(idx)):
                a, b = idx[i], idx[j]
                if y_a[a] == y_a[b]:
                    continue
                total += 1
                if (y_a[a] - y_a[b]) * (score_a[a] - score_a[b]) > 0:
                    agree += 1
    return agree / total if total else float("nan")
