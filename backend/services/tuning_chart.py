"""Render the live suggestion-tuner graph as a matplotlib PNG."""

from __future__ import annotations

import threading
from io import BytesIO
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

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


def _accuracy_series(history: list[dict[str, Any]]) -> tuple[list[int], list[float], list[float], list[float], list[int]]:
    xs: list[int] = []
    accuracy: list[float] = []
    rolling: list[float] = []
    held_rolling: list[float] = []
    regressions: list[int] = []
    running: list[float] = []
    peak_rolling: float | None = None
    for index, entry in enumerate(history, start=1):
        point = _as_float(entry.get("accuracy_percent"))
        if point is None:
            continue
        xs.append(index)
        accuracy.append(point)
        running.append(point)
        window = running[-12:]
        recorded_rolling = _as_float(entry.get("rolling_accuracy_percent"))
        live = recorded_rolling if recorded_rolling is not None else sum(window) / len(window)
        rolling.append(live)
        peak_rolling = live if peak_rolling is None else max(peak_rolling, live)
        held_rolling.append(peak_rolling)
        if entry.get("draw_regressed") or entry.get("balance_regressed"):
            regressions.append(len(xs) - 1)
    return xs, accuracy, rolling, held_rolling, regressions


def chart_status_for_game(status: dict[str, Any], game: str | None = None) -> dict[str, Any]:
    """Overlay per-game metrics when the caller asked for a specific game."""
    overlay = dict(status or {})
    requested = str(game or "").strip().lower()
    if not requested or requested in {"all", "*"}:
        return overlay
    by_game = overlay.get("metrics_by_game") or {}
    metrics = by_game.get(requested)
    if metrics:
        overlay["tuning_metrics"] = metrics
        overlay["current_game"] = requested
    return overlay


def render_tuning_chart(status: dict[str, Any], game: str | None = None) -> bytes:
    """Return a PNG of accuracy vs iteration, regressions, and game progress."""
    view = chart_status_for_game(status, game)
    metrics = view.get("tuning_metrics") or {}
    history = list(metrics.get("suggestion_history") or [])
    xs, accuracy, rolling, held_rolling, regression_indexes = _accuracy_series(history)

    total = max(int(view.get("games_total") or 0), 0)
    completed = min(max(int(view.get("games_completed") or 0), 0), total) if total else 0
    relative_progress = completed / total if total else (1.0 if str(view.get("status") or "") == "completed" else 0.0)
    state = str(view.get("status") or "idle").upper()
    active_game = str(
        game or view.get("current_game") or view.get("latest_result", {}).get("game") or view.get("game") or "all games"
    )
    task = str(view.get("current_task") or "waiting").replace("_", " ")
    target = _as_float(metrics.get("target_accuracy_percent"))
    best = _as_float(metrics.get("highest_accuracy_percent"))
    best_rolling = _as_float(metrics.get("highest_rolling_accuracy_percent"))
    current = _as_float(
        metrics.get("held_accuracy_percent")
        or metrics.get("accuracy_percent")
        or metrics.get("highest_rolling_accuracy_percent")
    )
    delta = _as_float(metrics.get("delta_percent") if metrics.get("delta_percent") is not None else (view.get("last_run") or {}).get("delta_percent"))
    run_points = [
        (
            _as_float(item.get("round")),
            _as_float(item.get("held_accuracy_percent") or item.get("accuracy_percent")),
        )
        for item in (metrics.get("round_metrics") or metrics.get("run_history") or [])
    ]
    run_points = [(x, y) for x, y in run_points if x is not None and y is not None]
    regressions = int(metrics.get("regression_count") or len(regression_indexes) or 0)
    promotions = int(metrics.get("promotion_count") or 0)
    round_no = metrics.get("verification_round") or view.get("latest_result", {}).get("verification_rounds")

    with _RENDER_LOCK:
        figure, (axis, progress) = plt.subplots(
            2,
            1,
            figsize=(7.6, 3.85),
            dpi=132,
            gridspec_kw={"height_ratios": [3.15, 0.85]},
        )
        figure.patch.set_facecolor(_BG)
        for ax in (axis, progress):
            ax.set_facecolor(_PANEL)
            for spine in ax.spines.values():
                spine.set_color(_GRID)

        if xs:
            axis.plot(xs, accuracy, color=_COPPER, linewidth=0.9, alpha=0.35, label="Per-draw (noisy)")
            axis.plot(xs, rolling, color=_BLUE, linewidth=1.15, alpha=0.55, label="Live rolling (can dip)")
            if held_rolling:
                axis.plot(xs, held_rolling, color=_BLUE, linewidth=2.5, label="Held rolling")
            if target is not None:
                axis.axhline(target, color=_GREEN, linestyle="--", linewidth=1.0, label=f"Target {target:.1f}%")
            if best_rolling is not None:
                axis.axhline(best_rolling, color=_BLUE, linestyle=":", linewidth=1.15, alpha=0.9, label=f"Best rolling {best_rolling:.1f}%")
            if best is not None:
                axis.axhline(best, color=_GOLD, linestyle=":", linewidth=1.1, label=f"Best {best:.1f}%")
            if run_points and xs:
                span = max(xs[-1], 1)
                mapped_x = [max(1, (point[0] / max(run_points[-1][0], 1)) * span) for point in run_points]
                axis.plot(
                    mapped_x,
                    [point[1] for point in run_points],
                    color=_GOLD,
                    linewidth=2.2,
                    marker="o",
                    markersize=4.5,
                    label="Held accuracy after each run",
                )
            if regression_indexes:
                axis.scatter(
                    [xs[i] for i in regression_indexes],
                    [accuracy[i] for i in regression_indexes],
                    color=_RED,
                    s=18,
                    zorder=5,
                    label="Regression rejected",
                )
            ymax = max([
                *accuracy, *rolling, *held_rolling,
                target or 0, best or 0, best_rolling or 0,
                *[point[1] for point in run_points], 1,
            ])
            axis.set_ylim(0, min(100, max(ymax * 1.15, 10)))
            axis.set_xlim(1, max(xs[-1], 2))
        else:
            axis.set_xlim(0, 1)
            axis.set_ylim(0, 1)
            axis.text(
                0.5,
                0.52,
                "RUN TUNING TO PLOT VALIDATED ACCURACY",
                color=_INK,
                fontsize=9,
                fontweight="bold",
                ha="center",
                va="center",
                transform=axis.transAxes,
            )
            axis.text(
                0.5,
                0.34,
                "The relentless tuner rejects weight regressions and keeps iterating.",
                color=_MUTED,
                fontsize=7.5,
                ha="center",
                va="center",
                transform=axis.transAxes,
            )

        axis.set_ylabel("Accuracy %", color=_MUTED, fontsize=8)
        axis.tick_params(colors=_MUTED, labelsize=7)
        axis.grid(True, color=_GRID, linewidth=0.6, alpha=0.7)
        axis.set_axisbelow(True)
        title_bits = [active_game.upper(), state]
        if round_no:
            title_bits.append(f"ROUND {round_no}")
        if current is not None:
            title_bits.append(f"{current:.1f}%")
        if delta is not None:
            title_bits.append(f"{delta:+.1f} PTS")
        axis.set_title("   ".join(title_bits), color=_INK, fontsize=9, loc="left", pad=8)
        if xs:
            legend = axis.legend(
                loc="upper right",
                fontsize=6.5,
                framealpha=0.35,
                facecolor=_BG,
                edgecolor=_GRID,
                labelcolor=_MUTED,
            )
            for text in legend.get_texts():
                text.set_color(_MUTED)

        progress.barh([0], [1], color=_GRID, height=0.28)
        bar_color = _COPPER if state == "RUNNING" else _BLUE
        progress.barh([0], [relative_progress], color=bar_color, height=0.28)
        progress.set_xlim(0, 1)
        progress.set_yticks([])
        progress.set_xticks([0, 0.25, 0.5, 0.75, 1])
        progress.set_xticklabels(["0%", "25%", "50%", "75%", "100%"], color=_MUTED, fontsize=7)
        progress.tick_params(axis="x", length=0, pad=4)
        for spine in progress.spines.values():
            spine.set_visible(False)
        progress.text(
            0,
            0.72,
            f"{task.upper()}   ·   {completed}/{total or '-'} GAMES   ·   {promotions} PROMOTED   ·   {regressions} REGRESSIONS BLOCKED",
            color=_MUTED,
            fontsize=7,
            transform=progress.transAxes,
        )

        figure.tight_layout(pad=0.7)
        image = BytesIO()
        figure.savefig(image, format="png", transparent=False, facecolor=figure.get_facecolor())
        plt.close(figure)
        image.seek(0)
        return image.getvalue()
