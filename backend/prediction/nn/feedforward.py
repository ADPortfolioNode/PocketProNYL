"""Feedforward neural network using sklearn MLPRegressor."""

from __future__ import annotations

import numpy as np
from sklearn.neural_network import MLPRegressor

from prediction.core.types import Draw, GameRules
from prediction.nn.base import NNBackend


class FeedforwardNN(NNBackend):
    """Flatten last lookback draws and predict next draw vector."""

    def __init__(self, lookback: int = 30, hidden_layers: list[int] | None = None, max_iter: int = 200,
                 alpha: float = 1e-3, patience: int = 10, validation_fraction: float = 0.15):
        self.lookback = lookback
        self.hidden_layers = hidden_layers or [64, 32]
        self.max_iter = max_iter
        self.alpha = float(alpha)  # L2 penalty
        self.patience = int(patience)
        self.validation_fraction = float(validation_fraction)
        self.best_epoch: int | None = None
        self._model: MLPRegressor | None = None
        self._fitted = False

    @property
    def is_fitted(self) -> bool:
        return self._fitted and self._model is not None

    def _build_matrix(self, history: list[Draw], rules: GameRules) -> tuple[np.ndarray, np.ndarray] | None:
        if len(history) < 2:
            return None

        width = rules.primary_count
        X, y = [], []
        for index in range(1, len(history)):
            start = max(0, index - self.lookback)
            window = history[start:index]
            flat: list[float] = []
            for draw in window[-self.lookback :]:
                row = list(draw.primary[:width])
                while len(row) < width:
                    row.append(0.0)
                flat.extend(row)
            while len(flat) < self.lookback * width:
                flat = [0.0] * width + flat
            flat = flat[-self.lookback * width :]

            target = list(history[index].primary[:width])
            while len(target) < width:
                target.append(0.0)

            X.append(flat)
            y.append(target)

        if not X:
            return None
        return np.array(X, dtype=float), np.array(y, dtype=float)

    def fit(self, history: list[Draw], rules: GameRules) -> None:
        data = self._build_matrix(history, rules)
        if data is None:
            self._fitted = False
            return
        X, y = data
        self._model = MLPRegressor(
            hidden_layer_sizes=tuple(self.hidden_layers),
            max_iter=self.max_iter,
            alpha=self.alpha,
            random_state=42,
        )
        n_val = int(len(X) * self.validation_fraction)
        if n_val < 5 or len(X) - n_val < 10:
            self._model.fit(X, y)
            self._fitted = True
            return
        # Early stopping on the most recent rows (time-ordered, never shuffled):
        # sklearn's built-in early_stopping uses a random split, which leaks the future.
        X_tr, y_tr, X_va, y_va = X[:-n_val], y[:-n_val], X[-n_val:], y[-n_val:]
        best_loss, best_state, best_epoch, stale = float("inf"), None, 0, 0
        for epoch in range(1, self.max_iter + 1):
            self._model.partial_fit(X_tr, y_tr)
            loss = float(np.mean((self._model.predict(X_va) - y_va) ** 2))
            if loss < best_loss - 1e-9:
                best_loss, best_epoch, stale = loss, epoch, 0
                best_state = ([c.copy() for c in self._model.coefs_], [b.copy() for b in self._model.intercepts_])
            else:
                stale += 1
                if stale >= self.patience:
                    break
        if best_state is not None:
            self._model.coefs_, self._model.intercepts_ = best_state
        self.best_epoch = best_epoch
        self._fitted = True

    def predict_scores(self, history: list[Draw], rules: GameRules) -> list[float]:
        size = rules.primary_universe_size
        if not self.is_fitted or not history:
            return [1.0 / size] * size

        width = rules.primary_count
        flat: list[float] = []
        for draw in history[-self.lookback :]:
            row = list(draw.primary[:width])
            while len(row) < width:
                row.append(0.0)
            flat.extend(row)
        while len(flat) < self.lookback * width:
            flat = [0.0] * width + flat
        flat = flat[-self.lookback * width :]

        pred = self._model.predict([flat])[0]  # type: ignore[union-attr]
        scores = [0.0] * size
        for value in pred:
            rounded = int(round(float(value)))
            rounded = max(rules.primary_min, min(rules.primary_max, rounded))
            idx = rounded - rules.primary_min
            if 0 <= idx < size:
                scores[idx] += 1.0

        total = sum(scores)
        if total <= 0:
            return [1.0 / size] * size
        return [s / total for s in scores]