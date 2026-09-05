"""Lottery prediction engine facade."""

from __future__ import annotations

import os
from typing import Callable

from prediction.config.loader import GamePredictionConfig, game_rules_from_config, load_game_config
from prediction.core.draw_loader import draws_from_lists, from_chroma
from prediction.core.ensemble import build_ticket
from prediction.core.registry import ensure_plugins_loaded, get_agent_class, get_strategy_class
from prediction.core.types import Draw, PredictionTicket, StrategyOutput
from prediction.learning.weight_updater import WeightUpdater
from prediction.metrics.evaluator import walk_forward_backtest
from prediction.nn.feedforward import FeedforwardNN
from prediction.nn.lstm import LSTMBackend
from prediction.state.weight_store import WeightStore
from services import statistical_strategies


class LotteryPredictionEngine:
    """Orchestrates strategies, agents, ensemble, NN, and weight learning."""

    def __init__(self, state_dir: str | None = None):
        self.weight_store = WeightStore(state_dir)
        self.weight_updater = WeightUpdater(self.weight_store)
        self._nn_cache: dict[str, object] = {}

    def _load_config(self, game: str) -> GamePredictionConfig:
        return load_game_config(game)

    def _collect_outputs(
        self,
        history: list[Draw],
        config: GamePredictionConfig,
    ) -> list[StrategyOutput]:
        rules = game_rules_from_config(config.game)
        outputs: list[StrategyOutput] = []

        for name in config.enabled_strategies:
            cls = get_strategy_class(name)
            if cls is None:
                continue
            params = (config.strategy_params or {}).get(name, {})
            if name == "hot_cold":
                params = {
                    **(config.strategy_params.get("hot_cold") or {}),
                    "hot_window": int(os.environ.get("HOT_WINDOW", params.get("hot_window", 20))),
                }
            instance = cls()
            outputs.append(instance.analyze(history, rules, params=params))

        for name in config.enabled_agents:
            cls = get_agent_class(name)
            if cls is None:
                continue
            params = (config.strategy_params or {}).get(name, {})
            instance = cls()
            outputs.append(instance.score(history, rules, params=params))

        return outputs

    def _get_nn(self, game: str, config: GamePredictionConfig):
        if not config.nn.enabled:
            return None
        if game in self._nn_cache:
            return self._nn_cache[game]

        if config.nn.backend == "lstm":
            backend = LSTMBackend(lookback=config.nn.lookback)
        else:
            backend = FeedforwardNN(
                lookback=config.nn.lookback,
                hidden_layers=config.nn.hidden_layers,
                max_iter=config.nn.max_iter,
            )
        self._nn_cache[game] = backend
        return backend

    def _resolve_weights(self, game: str, config: GamePredictionConfig) -> dict[str, float]:
        state = self.weight_store.load(game, initial_weights=config.ensemble.initial_weights)
        if state.best_weights:
            return state.best_weights
        if state.weights:
            return state.weights
        weights = dict(config.ensemble.initial_weights)
        total = sum(weights.values())
        if total > 0:
            return {k: v / total for k, v in weights.items()}
        return weights

    def predict(
        self,
        game: str,
        history: list[Draw] | None = None,
        limit: int = 500,
        strategy: str | None = None,
    ) -> PredictionTicket:
        config = self._load_config(game)
        rules = game_rules_from_config(game)

        if history is None:
            history = from_chroma(game, limit=limit)
        if len(history) < 2:
            raise ValueError(f"Not enough history for game '{game}' (need >= 2 draws).")

        strategy_name = strategy or "ensemble"

        # Dispatch to statistical strategies if specified
        if strategy_name in ["frequency", "overdue", "hybrid"]:
            if strategy_name == "frequency":
                scores = statistical_strategies.score_frequency(history, rules)
            elif strategy_name == "overdue":
                scores = statistical_strategies.score_overdue(history, rules)
            else: # hybrid
                scores = statistical_strategies.score_hybrid(history, rules)
            
            primary_numbers = statistical_strategies.select_top_numbers(scores, rules)
            
            return PredictionTicket(
                game=game,
                primary=primary_numbers,
                bonus=[],
                strategy_used=strategy_name,
                weights_used={},
                strategy_contributions={},
                metrics={},
            )

        # Default to existing ensemble/RF logic
        outputs = self._collect_outputs(history, config)
        weights = self._resolve_weights(game, config)

        nn_scores = None
        nn_weight = 0.0
        if config.nn.enabled and config.ensemble.enabled:
            nn = self._get_nn(game, config)
            if nn is not None:
                try:
                    nn.fit(history, rules)
                    nn_scores = nn.predict_scores(history, rules)
                    nn_weight = float(config.nn.blend_weight)
                except Exception:
                    # Keep the preferred RNN default usable in the core image,
                    # which intentionally does not install TensorFlow.
                    if config.nn.backend == "lstm":
                        try:
                            nn = FeedforwardNN(
                                lookback=config.nn.lookback,
                                hidden_layers=config.nn.hidden_layers,
                                max_iter=config.nn.max_iter,
                            )
                            nn.fit(history, rules)
                            nn_scores = nn.predict_scores(history, rules)
                            nn_weight = float(config.nn.blend_weight)
                        except Exception:
                            nn_scores = None
                            nn_weight = 0.0
                    else:
                        nn_scores = None
                        nn_weight = 0.0

        ticket = build_ticket(
            game=game,
            outputs=outputs,
            weights=weights,
            rules=rules,
            nn_scores=nn_scores,
            nn_weight=nn_weight,
        )
        ticket.strategy_used = strategy_name
        return ticket

    def update_weights(
        self,
        game: str,
        actual: Draw,
        history: list[Draw] | None = None,
        validation_accuracy: float | None = None,
    ) -> dict:
        """Update ensemble weights after a real draw result."""
        config = self._load_config(game)
        rules = game_rules_from_config(game)
        if history is None:
            history = from_chroma(game, limit=500)
        state = self.weight_store.load(game, initial_weights=config.ensemble.initial_weights)
        if actual.draw_id and state.last_draw_id == actual.draw_id:
            return {"game": game, "weights": state.weights, "updated_at": state.updated_at, "skipped": True}

        # Evaluate picks made before the draw; never let the actual result
        # influence the strategy output being scored.
        prior_history = list(history or [])
        if actual.draw_id:
            prior_history = [draw for draw in prior_history if draw.draw_id != actual.draw_id]
        elif prior_history and prior_history[-1].primary == actual.primary:
            prior_history = prior_history[:-1]
        outputs = self._collect_outputs(prior_history, config)
        state = self.weight_updater.update(
            game=game,
            actual=actual,
            outputs=outputs,
            rules=rules,
            learning_rate=config.ensemble.learning_rate,
            min_weight=config.ensemble.min_weight,
            initial_weights=config.ensemble.initial_weights,
            validation_accuracy=validation_accuracy,
        )
        return {
            "game": game,
            "weights": state.weights,
            "best_weights": state.best_weights,
            "best_validation_accuracy": state.best_validation_accuracy,
            "promoted": bool(state.history and state.history[-1].get("promoted")),
            "updated_at": state.updated_at,
        }

    def backtest(self, game: str, history: list[Draw] | None = None) -> dict:
        """Walk-forward backtest with honest metrics."""
        config = self._load_config(game)
        rules = game_rules_from_config(game)
        if history is None:
            history = from_chroma(game, limit=config.metrics.backtest_window + 50)

        def predict_fn(train_hist: list[Draw]) -> PredictionTicket:
            return self.predict(game, history=train_hist)

        return walk_forward_backtest(
            history=history,
            rules=rules,
            predict_fn=predict_fn,
            window=config.metrics.backtest_window,
            min_draws=config.metrics.min_backtest_draws,
            target_accuracy=config.metrics.target_accuracy,
            verification_rounds=config.metrics.verification_rounds,
            update_fn=lambda actual, train_hist, validation_accuracy: self.update_weights(
                game,
                actual,
                history=train_hist,
                validation_accuracy=validation_accuracy,
            ),
        )


def main():
    """CLI: python -m prediction.engine --game take5 --backtest"""
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Modular lottery prediction engine")
    parser.add_argument("--game", required=True, help="Game key (take5, pick3, ...)")
    parser.add_argument("--backtest", action="store_true", help="Run walk-forward backtest")
    parser.add_argument("--predict", action="store_true", help="Generate next prediction")
    args = parser.parse_args()

    engine = LotteryPredictionEngine()
    if args.backtest:
        result = engine.backtest(args.game)
        print(json.dumps(result, indent=2))
    elif args.predict:
        ticket = engine.predict(args.game)
        print(json.dumps({
            "game": ticket.game,
            "primary": ticket.primary,
            "bonus": ticket.bonus,
            "weights": ticket.weights_used,
        }, indent=2))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()