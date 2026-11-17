"""Render live workflow step charts as matplotlib PNGs.

One image per workflow (pipeline, startup, ingest, train, predict). The
frontend refreshes the image every couple of seconds while a flow runs, so
the chart is the live state, not a cached picture. Every label comes from
the same status stores the panels already poll; nothing is invented, and
an empty flow renders an honest empty state instead of fake numbers.
"""

from __future__ import annotations

import threading
import time
from io import BytesIO
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle

_RENDER_LOCK = threading.Lock()

_BG = "#10182b"
_PANEL = "#151f36"
_GRID = "#273552"
_INK = "#dbe9ff"
_MUTED = "#b7c6df"
_CYAN = "#22d3ee"
_VIOLET = "#a78bfa"
_GREEN = "#85e3b0"
_GOLD = "#f4d58d"
_RED = "#e36b6b"

_ACTIVE_WORDS = {"ingesting", "running", "queued", "fetching", "in progress", "in_progress", "training", "tuning", "started"}
_DONE_WORDS = {"completed", "complete", "done", "success", "ready", "trained", "idle_trained"}

# box fill / edge per step status
_BOX = {
    "active": ("#0e3a44", _CYAN, _CYAN),
    "done": ("#12281d", "#2f7d5b", _GREEN),
    "error": ("#2e1a20", "#8a3a46", _RED),
    "ready": ("#17233d", "#3a5a8a", _CYAN),
    "idle": (_PANEL, _GRID, _MUTED),
}

_PIPELINE_META = [
    ("ingest", "Ingest", "NY Open Data draws into ChromaDB"),
    ("agents", "Strategies & Agents", "RF, frequency, overdue, hot/cold, hybrid"),
    ("ensemble", "Ensemble", "Weighted blend of model votes"),
    ("prediction", "Prediction", "Ranked number suggestions"),
]

_GAME_SHORT = {
    "take5": "T5",
    "pick3": "P3",
    "powerball": "PB",
    "megamillions": "MM",
    "pick10": "P10",
    "cash4life": "C4L",
    "numbers": "NUM",
    "win4": "W4",
    "quickdraw": "QD",
    "nylotto": "NYL",
    "millionaireforlife": "MFL",
}


def _as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def _clip(text: Any, limit: int = 110) -> str:
    value = str(text or "").strip()
    if len(value) > limit:
        return value[: limit - 1].rstrip() + "…"
    return value


def _ago(ts: Any) -> str:
    stamp = _as_float(ts)
    if stamp is None:
        return ""
    seconds = max(0, int(time.time() - stamp))
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    return f"{seconds // 3600}h ago"


def _elapsed(started_at: Any) -> str:
    stamp = _as_float(started_at)
    if stamp is None:
        return ""
    seconds = max(0, int(time.time() - stamp))
    if seconds < 60:
        return f"{seconds}s"
    return f"{seconds // 60}m {seconds % 60:02d}s"


def _is_active(value: Any) -> bool:
    return str(value or "").strip().lower() in _ACTIVE_WORDS


def _is_done(value: Any) -> bool:
    return str(value or "").strip().lower() in _DONE_WORDS


def _is_error(value: Any) -> bool:
    return str(value or "").strip().lower() in {"error", "failed", "failure"}


def _fmt_game(game: Any) -> str:
    return str(game or "").replace("_", " ").title() or "—"


# ---------------------------------------------------------------- pipeline
def _derive_pipeline_stages(startup: dict, jobs: dict, predict: dict) -> list[dict]:
    """Python mirror of the frontend deriveStages: same inputs, same words."""
    startup = startup or {}
    jobs = jobs or {}
    predict = predict or {}
    stages: dict[str, dict] = {}

    # Ingest — from the merged startup status (startup + manual ingest).
    status = str(startup.get("status") or "").lower()
    populated = int(startup.get("populated_games") or startup.get("completed_games") or 0)
    total = int(startup.get("total") or len(startup.get("available_games") or []) or 0)
    if _is_active(status):
        rows = int(startup.get("current_game_rows_fetched") or 0)
        rows_total = int(startup.get("current_game_rows_total") or 0)
        current = startup.get("current_game")
        detail = f"{current}{f' · {rows:,} / {rows_total:,} rows' if rows_total else ''}" if current else "Pulling draw history"
        stages["ingest"] = {
            "status": "active",
            "detail": detail,
            "percent": _as_float(startup.get("percent_complete")),
        }
    elif startup.get("stalled"):
        stages["ingest"] = {"status": "error", "detail": "Ingest stalled — no progress"}
    elif _is_error(status):
        stages["ingest"] = {"status": "error", "detail": "Last ingest reported an error"}
    elif populated > 0:
        stages["ingest"] = {
            "status": "done",
            "detail": f"{populated}{f' of {total}' if total else ''} games loaded",
            "percent": (populated / total) * 100 if total else 100.0,
        }
    else:
        stages["ingest"] = {"status": "idle", "detail": "No draw data yet" if startup else "Connecting…"}

    # Strategies & agents — training jobs, then tuning, then a running batch.
    job_rows = list(jobs.values()) if isinstance(jobs, dict) else []
    running = [j for j in job_rows if _is_active(j.get("status"))]
    tuning = startup.get("tuning") or {}
    trained = len([j for j in job_rows if _is_done(j.get("status"))])
    predict_running = str(predict.get("status") or "").lower() == "running"
    if running:
        games = ", ".join(str(j.get("game") or "") for j in running[:3] if j.get("game"))
        stages["agents"] = {"status": "active", "detail": f"Training {games or 'models'}"}
    elif _is_active(tuning.get("status")):
        stages["agents"] = {"status": "active", "detail": f"Tuning {tuning.get('current_game') or tuning.get('game') or 'weights'}"}
    elif predict_running:
        stages["agents"] = {"status": "active", "detail": "Scoring draw history for suggestions"}
    elif any(_is_error(j.get("status")) for j in job_rows):
        stages["agents"] = {"status": "error", "detail": "A strategy run reported an error"}
    elif trained:
        stages["agents"] = {"status": "done", "detail": f"{trained} model{'s' if trained != 1 else ''} trained this session"}
    else:
        stages["agents"] = {
            "status": "ready" if populated > 0 else "idle",
            "detail": "Strategies standing by" if populated > 0 else "Waiting for data",
        }

    # Ensemble + prediction follow the suggestion batch.
    for key in ("ensemble", "prediction"):
        if predict_running:
            stages[key] = {
                "status": "active",
                "detail": "Blending strategy votes" if key == "ensemble" else "Ranking suggestions",
            }
        elif str(predict.get("status") or "").lower() == "error":
            stages[key] = {"status": "error", "detail": "Last suggestion batch failed"}
        elif predict.get("finished_at"):
            ago = _ago(predict.get("finished_at"))
            stages[key] = {
                "status": "done",
                "detail": f"{'Blended' if key == 'ensemble' else 'Suggested'}{f' {ago}' if ago else ''}",
            }
        else:
            stages[key] = {
                "status": "ready" if populated > 0 else "idle",
                "detail": "Ready on request" if populated > 0 else "Waiting for data",
            }
    return [
        {"key": key, "label": label, "hint": hint, **stages.get(key, {"status": "idle", "detail": ""})}
        for key, label, hint in _PIPELINE_META
    ]


# ---------------------------------------------------------------- drawing
def _new_figure():
    figure, ax = plt.subplots(figsize=(9.4, 3.0), dpi=120)
    figure.patch.set_facecolor(_BG)
    ax.set_facecolor(_BG)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    figure.subplots_adjust(left=0.015, right=0.985, top=0.985, bottom=0.015)
    return figure, ax


def _header(ax, title: str, state: str, state_color: str = _MUTED) -> None:
    ax.text(0.01, 0.94, title, color=_INK, fontsize=10, fontweight="bold", ha="left", va="center", transform=ax.transAxes)
    ax.text(0.99, 0.94, state.upper(), color=state_color, fontsize=9, fontweight="bold", ha="right", va="center", transform=ax.transAxes)


def _step_box(ax, x: float, y: float, w: float, h: float, label: str, sub: str, status: str, percent: float | None = None) -> None:
    fill, edge, text_color = _BOX.get(status, _BOX["idle"])
    ax.add_patch(
        FancyBboxPatch(
            (x, y), w, h,
            boxstyle="round,pad=0.002,rounding_size=0.012",
            facecolor=fill, edgecolor=edge,
            linewidth=2.0 if status == "active" else 1.0,
        )
    )
    ax.text(x + w / 2, y + h * 0.68, label, color=_INK, fontsize=9, fontweight="bold", ha="center", va="center", transform=ax.transAxes)
    ax.text(x + w / 2, y + h * 0.34, sub, color=text_color, fontsize=7.5, ha="center", va="center", transform=ax.transAxes)
    if percent is not None:
        frac = max(0.0, min(1.0, percent / 100.0))
        ax.add_patch(Rectangle((x + 0.008, y + 0.045), (w - 0.016) * frac, 0.035, facecolor=_CYAN, edgecolor="none", transform=ax.transAxes))


def _bar(ax, x: float, y: float, w: float, frac: float | None, color: str = _CYAN) -> None:
    ax.add_patch(Rectangle((x, y), w, 0.045, facecolor=_GRID, edgecolor="none", transform=ax.transAxes))
    if frac is not None:
        ax.add_patch(
            Rectangle((x, y), w * max(0.0, min(1.0, frac)), 0.045, facecolor=color, edgecolor="none", transform=ax.transAxes)
        )


def _meta(ax, lines: list[tuple[str, str, float, bool]], y_top: float = 0.44) -> None:
    y = y_top
    for text, color, size, bold in lines:
        if not text:
            continue
        ax.text(0.01, y, _clip(text), color=color, fontsize=size, fontweight="bold" if bold else "normal",
                ha="left", va="center", transform=ax.transAxes)
        y -= 0.13


def _empty(ax, message: str) -> None:
    ax.text(0.5, 0.5, message, color=_MUTED, fontsize=9.5, ha="center", va="center", transform=ax.transAxes)


def _finish(figure) -> bytes:
    image = BytesIO()
    figure.savefig(image, format="png", facecolor=figure.get_facecolor())
    plt.close(figure)
    image.seek(0)
    return image.getvalue()


# ---------------------------------------------------------------- flows
def _render_pipeline(startup: dict, jobs: dict, predict: dict) -> bytes:
    stages = _derive_pipeline_stages(startup or {}, jobs or {}, predict or {})
    active = [s for s in stages if s["status"] == "active"]
    state = "LIVE" if active else ("ERROR" if any(s["status"] == "error" for s in stages) else "IDLE")
    state_color = _CYAN if active else (_RED if state == "ERROR" else _MUTED)
    with _RENDER_LOCK:
        figure, ax = _new_figure()
        _header(ax, "DATA FLOW", state, state_color)
        gap = 0.018
        width = (1.0 - 0.02 - gap * (len(stages) - 1)) / len(stages)
        for index, stage in enumerate(stages):
            x = 0.01 + index * (width + gap)
            _step_box(ax, x, 0.55, width, 0.33, stage["label"], stage["status"].upper(), stage["status"],
                      percent=stage.get("percent") if stage["status"] == "active" else None)
        if active:
            step = active[0]
            position = stages.index(step) + 1
            lines = [
                (f"ACTIVE STEP {position} OF {len(stages)} — {step['label']}", _CYAN, 9.5, True),
                (step.get("detail") or step.get("hint") or "", _INK, 8.5, False),
                (step.get("hint") or "", _MUTED, 8, False),
            ]
        else:
            done = [s for s in stages if s["status"] == "done"]
            lines = [
                ("NO STEP RUNNING", _MUTED, 9.5, True),
                (done[-1]["detail"] if done else "Start ingestion, training, or a suggestion batch to see live steps.", _INK, 8.5, False),
                ("", _MUTED, 8, False),
            ]
        _meta(ax, lines, y_top=0.44)
        figure.text(0.015, 0.02, " · ".join(f"{s['label']}: {s.get('detail') or s['status']}" for s in stages)[:160],
                    color=_MUTED, fontsize=7, ha="left", va="bottom")
        return _finish(figure)


def _render_startup(startup: dict) -> bytes:
    startup = startup or {}
    games = startup.get("games") or {}
    order = list(startup.get("available_games") or list(games.keys()))
    status = str(startup.get("status") or "idle").lower()
    current = startup.get("current_game")
    elapsed = _as_float(startup.get("elapsed_s"))
    done_count = int(startup.get("completed_games") or 0)
    total = int(startup.get("total") or len(order) or 0)
    percent = _as_float(startup.get("percent_complete"))
    with _RENDER_LOCK:
        figure, ax = _new_figure()
        state_color = _CYAN if _is_active(status) else (_GREEN if status == "completed" else (_RED if _is_error(status) else _MUTED))
        _header(ax, "STARTUP INGESTION", status, state_color)
        if not order:
            _empty(ax, "No games configured yet.")
            return _finish(figure)
        gap = 0.008
        width = (0.98 - gap * (len(order) - 1)) / len(order)
        for index, game in enumerate(order):
            row = games.get(game) or {}
            cell_status = str(row.get("status") or "pending").lower()
            if _is_active(cell_status):
                cell = "active"
            elif _is_done(cell_status):
                cell = "done"
            elif _is_error(cell_status):
                cell = "error"
            else:
                cell = "idle"
            fill, edge, _ = _BOX[cell]
            x = 0.01 + index * (width + gap)
            ax.add_patch(FancyBboxPatch((x, 0.62), width, 0.26, boxstyle="round,pad=0.001,rounding_size=0.008",
                                        facecolor=fill, edgecolor=edge, linewidth=1.5 if cell == "active" else 0.8))
            ax.text(x + width / 2, 0.75, _GAME_SHORT.get(str(game).lower(), str(game)[:3].upper()),
                    color=_INK, fontsize=7.5, fontweight="bold", ha="center", va="center", transform=ax.transAxes)
        rows = int(startup.get("current_game_rows_fetched") or 0)
        rows_total = int(startup.get("current_game_rows_total") or 0)
        phase = startup.get("phase_label") or startup.get("phase") or startup.get("current_task") or ""
        lines = [
            (
                f"NOW: {_fmt_game(current)}" + (f" — {rows:,} / {rows_total:,} rows" if rows_total else "")
                if current else "NO GAME INGESTING",
                _CYAN if current else _MUTED, 9.5, True,
            ),
            (_clip(phase.replace("_", " ")) if phase else "", _INK, 8.5, False),
            (
                f"{done_count}/{total or '–'} games done"
                + (f" · {percent:.0f}%" if percent is not None else "")
                + (f" · elapsed {_elapsed(elapsed)}" if elapsed else ""),
                _MUTED, 8, False,
            ),
        ]
        _meta(ax, lines, y_top=0.5)
        _bar(ax, 0.01, 0.06, 0.98, (percent or 0) / 100.0 if percent is not None else None)
        return _finish(figure)


def _render_ingest(ingest: dict | None, game: str | None) -> bytes:
    row = ingest or {}
    status = str(row.get("status") or "not_started").lower()
    rows = int(row.get("rows_fetched") or 0)
    total = int(row.get("total_rows") or 0)
    percent = _as_float(row.get("percent"))
    if percent is None and total > 0:
        percent = min(100.0, rows / total * 100.0)
    with _RENDER_LOCK:
        figure, ax = _new_figure()
        state_color = _CYAN if _is_active(status) else (_GREEN if _is_done(status) else (_RED if _is_error(status) else _MUTED))
        _header(ax, f"INGESTION — {_fmt_game(game).upper()}" if game else "INGESTION", status, state_color)
        if not row or status in {"not_started", "idle"}:
            _empty(ax, f"No ingestion has run for {_fmt_game(game)} yet.")
            return _finish(figure)
        _step_box(ax, 0.01, 0.55, 0.42, 0.33, _fmt_game(game), status.upper(),
                  "active" if _is_active(status) else ("done" if _is_done(status) else ("error" if _is_error(status) else "idle")),
                  percent=percent if _is_active(status) else None)
        lines = [
            (f"{rows:,} / {total:,} rows" if total else f"{rows:,} rows fetched", _INK, 9.5, True),
            (_clip(row.get("error") or row.get("phase") or row.get("message") or "") if row.get("error") or row.get("phase") or row.get("message") else "", _RED if row.get("error") else _MUTED, 8.5, False),
            (f"{percent:.0f}% complete" if percent is not None else "", _MUTED, 8, False),
        ]
        # meta sits right of the step box
        y = 0.82
        for text, color, size, bold in lines:
            if not text:
                continue
            ax.text(0.47, y, _clip(text, 72), color=color, fontsize=size, fontweight="bold" if bold else "normal",
                    ha="left", va="center", transform=ax.transAxes)
            y -= 0.16
        _bar(ax, 0.01, 0.06, 0.98, (percent or 0) / 100.0 if percent is not None else None)
        return _finish(figure)


def _render_train(jobs: dict, game: str | None) -> bytes:
    rows = list((jobs or {}).values()) if isinstance(jobs, dict) else []
    rows.sort(key=lambda j: str(j.get("game") or ""))
    running = [j for j in rows if _is_active(j.get("status"))]
    with _RENDER_LOCK:
        figure, ax = _new_figure()
        state = "TRAINING" if running else ("ERROR" if any(_is_error(j.get("status")) for j in rows) else "IDLE")
        state_color = _CYAN if running else (_RED if state == "ERROR" else _MUTED)
        _header(ax, "MODEL TRAINING", state, state_color)
        if not rows:
            _empty(ax, "No training jobs yet — start training to see live steps.")
            return _finish(figure)
        shown = rows[:8]
        gap = 0.018
        width = (0.98 - gap * (len(shown) - 1)) / len(shown)
        for index, job in enumerate(shown):
            job_status = str(job.get("status") or "idle").lower()
            cell = "active" if _is_active(job_status) else ("done" if _is_done(job_status) else ("error" if _is_error(job_status) else "idle"))
            x = 0.01 + index * (width + gap)
            _step_box(ax, x, 0.55, width, 0.33, _fmt_game(job.get("game")), job_status.upper(), cell)
        focus = running[0] if running else None
        if focus is None and game:
            focus = next((j for j in rows if str(j.get("game") or "").lower() == str(game).lower()), None)
        if focus is not None:
            ago = _ago(focus.get("updated_at"))
            focus_running = _is_active(focus.get("status"))
            lines = [
                (
                    f"{'ACTIVE' if focus_running else 'LAST JOB'}: {_fmt_game(focus.get('game'))} — {str(focus.get('status') or '').upper()}",
                    _CYAN if focus_running else _MUTED, 9.5, True,
                ),
                (_clip(focus.get("message") or ""), _INK, 8.5, False),
                (
                    " · ".join(part for part in [
                        f"target {float(focus['target_accuracy']) * 100:.0f}%" if _as_float(focus.get("target_accuracy")) is not None else "",
                        f"max {focus.get('max_iterations')} iterations" if focus.get("max_iterations") is not None else "",
                        f"updated {ago}" if ago else "",
                    ] if part),
                    _MUTED, 8, False,
                ),
            ]
        else:
            done = [j for j in rows if _is_done(j.get("status"))]
            lines = [
                ("NO JOB RUNNING", _MUTED, 9.5, True),
                (f"{len(done)} of {len(rows)} job{'s' if len(rows) != 1 else ''} trained" if done else "Jobs are idle.", _INK, 8.5, False),
                ("", _MUTED, 8, False),
            ]
        _meta(ax, lines, y_top=0.44)
        if len(rows) > len(shown):
            figure.text(0.985, 0.02, f"+{len(rows) - len(shown)} more jobs", color=_MUTED, fontsize=7, ha="right", va="bottom")
        return _finish(figure)


def _render_predict(predict: dict) -> bytes:
    batch = predict or {}
    games = batch.get("games") or []
    total = int(batch.get("total") or len(games) or 0)
    done_count = int(batch.get("completed") or 0)
    errors = int(batch.get("errors") or 0)
    current = batch.get("current_game")
    status = str(batch.get("status") or "idle").lower()
    with _RENDER_LOCK:
        figure, ax = _new_figure()
        state_color = _CYAN if status == "running" else (_GREEN if status == "done" else (_RED if status == "error" else _MUTED))
        _header(ax, "SUGGESTIONS", status, state_color)
        if not games:
            _empty(ax, "No suggestion batch yet — run suggestions for all games to see live steps.")
            return _finish(figure)
        shown = games[:11]
        gap = 0.008
        width = (0.98 - gap * (len(shown) - 1)) / len(shown)
        for index, game in enumerate(shown):
            outcome = (batch.get("results") or {}).get(str(game).lower())
            if outcome == "error":
                cell = "error"
            elif outcome == "done":
                cell = "done"
            elif current and str(game).lower() == str(current).lower():
                cell = "active"
            else:
                cell = "idle"
            fill, edge, _ = _BOX[cell]
            x = 0.01 + index * (width + gap)
            ax.add_patch(FancyBboxPatch((x, 0.62), width, 0.26, boxstyle="round,pad=0.001,rounding_size=0.008",
                                        facecolor=fill, edgecolor=edge, linewidth=1.5 if cell == "active" else 0.8))
            ax.text(x + width / 2, 0.75, _GAME_SHORT.get(str(game).lower(), str(game)[:3].upper()),
                    color=_INK, fontsize=7.5, fontweight="bold", ha="center", va="center", transform=ax.transAxes)
        elapsed = _elapsed(batch.get("started_at"))
        ago = _ago(batch.get("finished_at"))
        lines = [
            (
                f"NOW: {_fmt_game(current)}" if current
                else (f"FINISHED {ago}" if status == "done" and ago else "BATCH NOT RUNNING"),
                _CYAN if current else _MUTED, 9.5, True,
            ),
            (
                f"{done_count}/{total or '–'} games done"
                + (f" · {errors} failed" if errors else "")
                + (f" · elapsed {elapsed}" if elapsed and status == "running" else ""),
                _INK, 8.5, False,
            ),
            ("Suggestions are built one game at a time, in order." if status == "running" else "", _MUTED, 8, False),
        ]
        _meta(ax, lines, y_top=0.5)
        _bar(ax, 0.01, 0.06, 0.98, (done_count / total) if total else None)
        return _finish(figure)


def render_workflow_chart(flow: str, *, startup=None, jobs=None, ingest=None, predict=None, game=None) -> bytes:
    """Return a PNG for one workflow; flow is pipeline/startup/ingest/train/predict."""
    key = str(flow or "pipeline").strip().lower()
    if key == "pipeline":
        return _render_pipeline(startup or {}, jobs or {}, predict or {})
    if key == "startup":
        return _render_startup(startup or {})
    if key == "ingest":
        return _render_ingest(ingest, game)
    if key == "train":
        return _render_train(jobs or {}, game)
    if key == "predict":
        return _render_predict(predict or {})
    raise ValueError(f"Unknown workflow: {flow}")
