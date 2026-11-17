"""Optional LSTM backend (requires tensorflow)."""

from __future__ import annotations

import numpy as np

from prediction.core.types import Draw, GameRules
from prediction.nn.base import NNBackend


class LSTMBackend(NNBackend):
    """Keras LSTM time-series model. Falls back gracefully if tensorflow is unavailable."""

    def __init__(self, lookback: int = 30, epochs: int = 10):
        self.lookback = lookback
        self.epochs = epochs
        self._model = None
        self._fitted = False
        self._tf = None

    def _load_tf(self):
        if self._tf is not None:
            return self._tf
        try:
            import tensorflow as tf  # type: ignore

            self._tf = tf
            return tf
        except ImportError as exc:
            raise ImportError(
                "tensorflow is required for LSTM backend. Install via: pip install -r requirements-ml.txt"
            ) from exc

    @property
    def is_fitted(self) -> bool:
        return self._fitted and self._model is not None

    def _sequences(self, history: list[Draw], rules: GameRules) -> tuple[np.ndarray, np.ndarray] | None:
        if len(history) < self.lookback + 1:
            return None
        width = rules.primary_count
        X, y = [], []
        for index in range(self.lookback, len(history)):
            window = history[index - self.lookback : index]
            seq = []
            for draw in window:
                row = list(draw.primary[:width])
                while len(row) < width:
                    row.append(0.0)
                seq.append(row)
            target = list(history[index].primary[:width])
            while len(target) < width:
                target.append(0.0)
            X.append(seq)
            y.append(target)
        if not X:
            return None
        return np.array(X, dtype=float), np.array(y, dtype=float)

    def fit(self, history: list[Draw], rules: GameRules) -> None:
        tf = self._load_tf()
        data = self._sequences(history, rules)
        if data is None:
            self._fitted = False
            return
        X, y = data
        l2 = tf.keras.regularizers.l2(1e-4)
        model = tf.keras.Sequential([
            tf.keras.layers.LSTM(32, input_shape=(self.lookback, rules.primary_count),
                                 kernel_regularizer=l2, recurrent_regularizer=l2),
            tf.keras.layers.Dense(rules.primary_count, kernel_regularizer=l2),
        ])
        model.compile(optimizer="adam", loss="mse")
        # Keras validation_split takes the LAST rows (time-ordered), so early stopping
        # never trains on draws after the ones it validates on.
        callbacks = []
        fit_kwargs = {}
        if len(X) >= 40:
            callbacks.append(tf.keras.callbacks.EarlyStopping(monitor="val_loss", patience=2,
                                                              restore_best_weights=True))
            fit_kwargs = {"validation_split": 0.15, "shuffle": False}
        model.fit(X, y, epochs=self.epochs, verbose=0, callbacks=callbacks, **fit_kwargs)
        self._model = model
        self._fitted = True

    def predict_scores(self, history: list[Draw], rules: GameRules) -> list[float]:
        size = rules.primary_universe_size
        if not self.is_fitted or len(history) < self.lookback:
            return [1.0 / size] * size

        width = rules.primary_count
        seq = []
        for draw in history[-self.lookback :]:
            row = list(draw.primary[:width])
            while len(row) < width:
                row.append(0.0)
            seq.append(row)

        pred = self._model.predict(np.array([seq], dtype=float), verbose=0)[0]
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