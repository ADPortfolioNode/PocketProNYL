"""Stacking meta-learner, odd-K bootstrap majority vote, and out-of-fold checks."""

from __future__ import annotations

import warnings

import numpy as np

from prediction.chain.features import FeatureBuilder

DEFAULT_C = 0.5


class _ConstantModel:
    def __init__(self, rate: float):
        self.rate = float(rate)

    def predict_proba(self, X):
        rate = np.full(len(X), self.rate)
        return np.vstack([1 - rate, rate]).T


def fit_meta(X: np.ndarray, y: np.ndarray, C: float = DEFAULT_C):
    """L2 logistic regression on standardized features (falls back to the base rate)."""
    if len(y) == 0 or y.min() == y.max():
        return _ConstantModel(float(y.mean()) if len(y) else 0.0)
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    model = make_pipeline(StandardScaler(), LogisticRegression(C=float(C), max_iter=300))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model.fit(X, y)
    return model


def stack_rows(builder: FeatureBuilder, positions: list[int]) -> tuple[np.ndarray, np.ndarray]:
    X = np.vstack([builder.features_at(p) for p in positions])
    y = np.concatenate([builder.labels_at(p) for p in positions])
    return X, y


def training_positions(builder: FeatureBuilder, j: int, window: int) -> list[int]:
    start = max(builder.min_history, j - int(window))
    return list(range(start, j))


def bootstrap_vote(builder: FeatureBuilder, j: int, *, votes: int, window: int, seed: int,
                   C: float = DEFAULT_C) -> dict:
    """Fit ``votes`` (odd) meta-learners on bootstrap resamples of training draws and vote.

    Resampling is by whole draw (all numbers of a draw stay together). Every
    learner sees only positions ``< j``. Returns per-number vote counts and the
    mean predicted probability.
    """
    if votes < 3 or votes % 2 == 0:
        raise ValueError("votes must be an odd number >= 3")
    positions = training_positions(builder, j, window)
    k = builder.rules.primary_count
    size = builder.size
    if len(positions) < 10:
        means = builder.posterior_means_at(j)
        return {"votes": np.zeros(size), "mean_prob": means, "learners": 0, "positions": len(positions)}
    rng = np.random.default_rng([int(seed), int(j), int(votes)])
    X_pred = builder.features_at(j)
    counts = np.zeros(size)
    probs = np.zeros(size)
    for _ in range(votes):
        sample = list(rng.choice(positions, size=len(positions), replace=True))
        X, y = stack_rows(builder, sample)
        model = fit_meta(X, y, C=C)
        p = model.predict_proba(X_pred)[:, 1]
        probs += p
        counts[np.argsort(-p, kind="stable")[:k]] += 1
    return {"votes": counts, "mean_prob": probs / votes, "learners": votes, "positions": len(positions)}


def majority_ranking(vote: dict) -> list[int]:
    """Offsets ranked by (votes, mean probability); majority numbers come first."""
    counts = np.asarray(vote["votes"], dtype=float)
    probs = np.asarray(vote["mean_prob"], dtype=float)
    return sorted(range(len(counts)), key=lambda u: (-counts[u], -probs[u], u))


def forward_oof_log_loss(builder: FeatureBuilder, positions: list[int], folds: int = 5,
                         C: float = DEFAULT_C) -> dict:
    """Out-of-fold (forward-chaining) log loss of the meta-learner vs the chance base rate.

    Positions are split into ``folds`` consecutive blocks; each block is scored by
    a model fit only on earlier positions. If the meta-learner has found no real
    signal, its OOF log loss will not beat the base rate.
    """
    positions = sorted(positions)
    if len(positions) < folds * 4:
        return {"status": "insufficient_data", "positions": len(positions)}
    blocks = np.array_split(np.asarray(positions), folds)
    p0 = builder.p0
    losses, base_losses, rows = [], [], 0
    eps = 1e-9
    for index in range(1, folds):
        train = [int(p) for block in blocks[:index] for p in block]
        test = [int(p) for p in blocks[index]]
        X, y = stack_rows(builder, train)
        model = fit_meta(X, y, C=C)
        Xt, yt = stack_rows(builder, test)
        pt = np.clip(model.predict_proba(Xt)[:, 1], eps, 1 - eps)
        losses.append(-(yt * np.log(pt) + (1 - yt) * np.log(1 - pt)).sum())
        base_losses.append(-(yt * np.log(p0) + (1 - yt) * np.log(1 - p0)).sum())
        rows += len(yt)
    meta = float(sum(losses) / rows)
    base = float(sum(base_losses) / rows)
    return {
        "status": "ok",
        "folds_scored": folds - 1,
        "rows": rows,
        "oof_log_loss": round(meta, 6),
        "chance_log_loss": round(base, 6),
        "improvement": round(base - meta, 6),
        "beats_chance": meta < base,
    }
