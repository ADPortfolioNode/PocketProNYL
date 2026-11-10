"""Render the training overfit check as a matplotlib PNG.

Mirrors services/tuning_chart.py (same dark theme, lock, and per-game
overlay): accuracy by validation round, the kept-weights validation score
against the untouched holdout, the gap measured against the 2-point line,
and the warning reason in words. The data comes from game_tuner.status(),
whose metrics_by_game holds the last tune result (round_metrics plus all
honest overfit fields) after _post_train_optimize runs.
"""

from __future__ import annotations

import textwrap
import threading
from io import BytesIO
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from prediction.metrics.overfit_gate import overfit_warning_sentence
from services.tuning_chart import chart_status_for_game

_RENDER_LOCK = threading.Lock()

_BG = "#10182b"
_PANEL = "#151f36"
_GRID = "#273552"
_INK = "#dbe9ff"
_MUTED = "#b7c6df"
_COPPER = "#d38b52"
_BLUE = "#6ea8fe"
_GREEN = "#85e3b0"
_GOLD = "#f4d58d"
_RED = "#e36b6b"


def _as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def render_training_chart(status: dict[str, Any], game: str | None = None) -> bytes:
    """Return a PNG of the training validation/holdout gap for one game."""
    view = chart_status_for_game(status, game)
    metrics = view.get("tuning_metrics") or {}
    rounds = metrics.get("round_metrics") or []

    run_points = [
        (
            _as_float(item.get("round")),
            _as_float(item.get("held_accuracy_percent") or item.get("accuracy_percent")),
            _as_float(item.get("accuracy_percent")),
        )
        for item in rounds
    ]
    run_points = [point for point in run_points if point[0] is not None and point[1] is not None]
    xs = [point[0] for point in run_points]
    held = [point[1] for point in run_points]
    per_round = [point[2] for point in run_points if point[2] is not None]

    validation = _as_float(metrics.get("validation_accuracy_percent"))
    kept = _as_float(metrics.get("kept_validation_accuracy_percent"))
    holdout = _as_float(metrics.get("holdout_accuracy_percent"))
    gap = _as_float(metrics.get("overfit_gap_percent"))
    boundary = _as_float(metrics.get("overfit_boundary_percent")) or 2.0
    margin = _as_float(metrics.get("holdout_noise_margin_percent"))
    significant = bool(metrics.get("overfit_gap_significant"))
    holdout_n = metrics.get("holdout_evaluated_draws")
    reason = overfit_warning_sentence(metrics)
    active_game = str(
        game or view.get("current_game") or view.get("latest_result", {}).get("game") or "all games"
    )

    with _RENDER_LOCK:
        figure, (axis, footer) = plt.subplots(
            2,
            1,
            figsize=(7.6, 3.85),
            dpi=132,
            gridspec_kw={"height_ratios": [3.15, 0.85]},
        )
        figure.patch.set_facecolor(_BG)
        for ax in (axis, footer):
            ax.set_facecolor(_PANEL)
            for spine in ax.spines.values():
                spine.set_color(_GRID)

        if xs or holdout is not None:
            if xs and per_round:
                axis.plot(xs, per_round, color=_COPPER, linewidth=0.9, alpha=0.35, label="Per-round (online)")
            if xs:
                axis.plot(
                    xs, held, color=_GOLD, linewidth=2.2, marker="o", markersize=4.5,
                    label="Held accuracy after each round",
                )
            if holdout is not None:
                axis.axhline(holdout, color=_BLUE, linewidth=1.4, label=f"Holdout {holdout:.1f}%")
                axis.axhline(
                    holdout + boundary, color=_RED, linestyle="--", linewidth=1.0,
                    label=f"Overfit boundary {holdout + boundary:.1f}% (holdout + {boundary:.1f} pp)",
                )
            if kept is not None:
                axis.axhline(
                    kept, color=_GREEN, linestyle="--", linewidth=1.2,
                    label=f"Kept validation {kept:.1f}%",
                )
            if validation is not None:
                axis.axhline(
                    validation, color=_COPPER, linestyle=":", linewidth=1.1, alpha=0.9,
                    label=f"Best validation round {validation:.1f}% (optimistic)",
                )
            if kept is not None and holdout is not None and xs:
                axis.fill_between(
                    [xs[0], xs[-1]], holdout, kept,
                    color=_RED if significant else _GREEN, alpha=0.15,
                    label=f"Gap {gap:.1f} pp" if gap is not None else "Gap",
                )
            values = [
                *held, *per_round,
                holdout or 0, kept or 0, validation or 0,
                (holdout + boundary) if holdout is not None else 0, 1,
            ]
            axis.set_ylim(0, min(100, max(max(values) * 1.15, 10)))
            axis.set_xlim(1, max(xs[-1], 2)) if xs else axis.set_xlim(0, 1)
        else:
            axis.set_xlim(0, 1)
            axis.set_ylim(0, 1)
            axis.text(
                0.5, 0.52,
                "RUN TRAINING TO PLOT THE VALIDATION / HOLDOUT GAP",
                color=_INK, fontsize=9, fontweight="bold",
                ha="center", va="center", transform=axis.transAxes,
            )
            axis.text(
                0.5, 0.34,
                "The gap is measured on the weights actually kept, and must clear\n"
                "both the 2-point line and the holdout's own noise before it is overfit.",
                color=_MUTED, fontsize=7.5,
                ha="center", va="center", transform=axis.transAxes,
            )

        axis.set_ylabel("Accuracy %", color=_MUTED, fontsize=8)
        axis.tick_params(colors=_MUTED, labelsize=7)
        axis.grid(True, color=_GRID, linewidth=0.6, alpha=0.7)
        axis.set_axisbelow(True)
        title_bits = [active_game.upper(), "TRAINING OVERFIT CHECK"]
        if gap is not None:
            title_bits.append(f"GAP {gap:+.1f} PP")
            title_bits.append("REAL" if significant else "WITHIN NOISE")
        axis.set_title("   ".join(title_bits), color=_INK, fontsize=9, loc="left", pad=8)
        if xs or holdout is not None:
            legend = axis.legend(
                loc="upper right", fontsize=6.5, framealpha=0.35,
                facecolor=_BG, edgecolor=_GRID, labelcolor=_MUTED,
            )
            for text in legend.get_texts():
                text.set_color(_MUTED)

        footer.set_xlim(0, 1)
        footer.set_ylim(0, 1)
        footer.set_xticks([])
        footer.set_yticks([])
        for spine in footer.spines.values():
            spine.set_visible(False)
        numbers = []
        if gap is not None:
            numbers.append(f"GAP {gap:+.1f} PP")
        numbers.append(f"BOUNDARY {boundary:.1f} PP")
        if margin is not None:
            numbers.append(f"HOLDOUT NOISE MARGIN {margin:.1f} PP")
        if holdout_n is not None:
            numbers.append(f"HOLDOUT {int(holdout_n)} DRAWS")
        footer.text(
            0, 0.72, "   ·   ".join(numbers) if numbers else "NO HOLDOUT SCORED YET",
            color=_MUTED, fontsize=7, transform=footer.transAxes,
        )
        footer.text(
            0, 0.30,
            "\n".join(textwrap.wrap(reason or "No overfit warning.", width=110)),
            color=_RED if significant else _MUTED, fontsize=7,
            transform=footer.transAxes, va="top",
        )

        figure.tight_layout(pad=0.7)
        image = BytesIO()
        figure.savefig(image, format="png", transparent=False, facecolor=figure.get_facecolor())
        plt.close(figure)
        image.seek(0)
        return image.getvalue()
