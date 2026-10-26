"""Auto-promotion loop for pattern signals (one game at a time, strictly isolated).

* A signal that passes validation BH-FDR becomes a feature in champion/challenger
  variants (the PR #13 machinery, ``evaluate_chain``). It is promoted only when a
  patterned variant beats the incumbent on validation (Bonferroni) AND on the
  untouched holdout. Never on backtest fit alone.
* A promoted signal is re-judged once ``demote_min_new_draws`` draws have arrived
  since its promotion: it is demoted when it shows no lift over chance on those
  new draws, or no longer passes validation FDR on the refreshed scan. If the
  current chained champion uses it, the champion falls back to the incumbent.
* Every promote / demote / rejected decision is appended to
  ``$PREDICTION_STATE_DIR/patterns/<game>.events.jsonl`` (append-only).
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import replace
from pathlib import Path

from prediction.chain import champion_store
from utils.paths import prediction_state_dir

_LOCK = threading.Lock()


def _dir() -> Path:
    return Path(prediction_state_dir()) / "patterns"


def _key(game: str) -> str:
    champion_store._path(game)  # validates the game key (no path tricks)
    return str(game).strip().lower()


def promoted_path(game: str) -> Path:
    return _dir() / f"{_key(game)}.promoted.json"


def events_path(game: str) -> Path:
    return _dir() / f"{_key(game)}.events.jsonl"


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load_promoted(game: str) -> dict:
    path = promoted_path(game)
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict) or data.get("game") not in (None, _key(game)):
        data = {}
    data.setdefault("signals", {})
    return data


def save_promoted(game: str, data: dict) -> None:
    path = promoted_path(game)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({**data, "game": _key(game)}, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def append_event(game: str, event: dict) -> dict:
    record = {"at": now_iso(), "game": _key(game), **event}
    path = events_path(game)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK, path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, default=str) + "\n")
    return record


def read_events(game: str, limit: int = 50) -> list[dict]:
    path = events_path(game)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get("game") == _key(game):
            out.append(item)
    return out[-limit:]


def draw_marker(draw) -> dict:
    return {"draw_id": str(getattr(draw, "draw_id", "") or ""), "draw_date": str(getattr(draw, "draw_date", "") or "")}


def new_draw_indices(history: list, marker: dict | None) -> list[int]:
    """Indices of draws that arrived after ``marker`` (the last draw seen at promotion)."""
    if not marker or not history:
        return []
    want = marker.get("draw_id") or ""
    if want:
        for idx in range(len(history) - 1, -1, -1):
            if str(getattr(history[idx], "draw_id", "") or "") == want:
                return list(range(idx + 1, len(history)))
    when = marker.get("draw_date") or ""
    if when:
        return [i for i, d in enumerate(history) if str(getattr(d, "draw_date", "") or "") > when]
    return []


def default_challenger(game: str, history: list, signals: tuple[str, ...], settings) -> dict:
    """Champion/challenger with the signals as meta-learner features (no commit here)."""
    from prediction.chain.chained import DEFAULT_VARIANTS
    from prediction.chain.evaluate import evaluate_chain
    from services.prediction_adapter import prediction_adapter

    window = history[-int(settings.chain_history_limit):]
    variants = [replace(v, patterns=tuple(signals)) for v in DEFAULT_VARIANTS]
    return evaluate_chain(prediction_adapter.engine, game, window, variants=variants,
                          pattern_search=False, commit=False)


def _commit_champion(game: str, result: dict) -> None:
    stored = champion_store.load(game)
    champion_store.save(game, {
        **{k: v for k, v in stored.items() if k != "game"},
        "champion": champion_store.CHAINED,
        "champion_variant": result.get("selected_variant"),
        "last_evaluated_at": result.get("evaluated_at") or now_iso(),
        "last_evaluation": {k: result.get(k) for k in
                            ("promoted", "decision_reason", "holdout", "split", "selected_variant")},
        "promoted_by": "pattern_auto_promotion",
    })


def _demote_champion_if_uses(game: str, signal: str) -> bool:
    stored = champion_store.load(game)
    variant = stored.get("champion_variant") or {}
    if stored.get("champion") != champion_store.CHAINED or signal not in (variant.get("patterns") or []):
        return False
    champion_store.save(game, {
        **{k: v for k, v in stored.items() if k != "game"},
        "champion": champion_store.INCUMBENT,
        "champion_variant": None,
        "demoted_at": now_iso(),
        "demoted_reason": f"pattern signal {signal} failed on new draws",
    })
    return True


def apply_auto_promotion(game: str, history: list, report: dict, settings, *, challenger=None,
                         rules=None) -> dict:
    """Demote failing promoted signals, then challenge with fresh validation survivors."""
    from prediction.patterns.search import PatternMatrix, parse_signal, score_signal_on

    summary = {"promoted": [], "demoted": [], "rejected": [], "challenged": [], "events": []}
    if report.get("status") != "ok" or not history:
        return summary
    state = load_promoted(game)
    rows = {r["signal"]: r for r in report.get("signals") or []}
    pm = None

    # 1) Demotion: re-judge promoted signals on draws that arrived after promotion.
    for name, info in list(state["signals"].items()):
        new_idx = new_draw_indices(history, info.get("marker"))
        if len(new_idx) < settings.demote_min_new_draws:
            info["pending_new_draws"] = len(new_idx)
            continue
        try:
            parse_signal(name)
        except KeyError:
            continue
        if pm is None:
            if rules is None:
                from prediction.config.loader import game_rules_from_config

                rules = game_rules_from_config(game)
            pm = PatternMatrix(history, rules)
        check = score_signal_on(pm, name, new_idx)
        row = rows.get(name) or {}
        failed_new = (check.get("lift") or 0.0) <= 1.0
        failed_fdr = bool(row) and row.get("status") != "not_applicable" and not row.get("passed_validation")
        info["last_check"] = {"at": now_iso(), "new_draws": len(new_idx), **check,
                              "passed_validation": row.get("passed_validation")}
        if failed_new or failed_fdr:
            reason = (f"no lift on {len(new_idx)} new draws (lift {check.get('lift')})" if failed_new
                      else "no longer passes validation BH-FDR on the refreshed scan")
            champion_reset = _demote_champion_if_uses(game, name)
            state["signals"].pop(name, None)
            summary["demoted"].append(name)
            summary["events"].append(append_event(game, {
                "type": "demote", "signal": name, "reason": reason, "new_draws": len(new_idx),
                "new_draw_lift": check.get("lift"), "new_draw_p": check.get("p_value"),
                "champion_reset": champion_reset}))
        else:
            # Survived: the next re-judgement counts from here.
            info["marker"] = draw_marker(history[-1])

    # 2) Promotion: validation-FDR survivors that are not promoted yet face the holdout test.
    candidates = [s for s in report.get("feature_signals") or [] if s not in state["signals"]]
    if candidates and settings.auto_promote:
        features = tuple(list(state["signals"].keys()) + candidates)
        summary["challenged"] = candidates
        try:
            result = (challenger or default_challenger)(game, history, features, settings)
        except Exception as exc:  # keep the scan result even if the challenger fails
            result = {"status": "error", "decision_reason": str(exc)}
        patterns = list((result.get("selected_variant") or {}).get("patterns") or [])
        won = bool(result.get("status") == "ok" and result.get("promoted") and patterns)
        hold = result.get("holdout") or {}
        if won:
            _commit_champion(game, result)
            for name in candidates:
                state["signals"][name] = {"promoted_at": now_iso(), "marker": draw_marker(history[-1]),
                                          "variant": result.get("selected_variant"),
                                          "holdout_diff": hold.get("mean_hit_diff_chained_minus_incumbent"),
                                          "holdout_p": hold.get("paired_permutation_p_value")}
            summary["promoted"] = candidates
            summary["events"].append(append_event(game, {
                "type": "promote", "signals": candidates, "features": list(features),
                "variant": (result.get("selected_variant") or {}).get("name"),
                "holdout_diff": hold.get("mean_hit_diff_chained_minus_incumbent"),
                "holdout_p": hold.get("paired_permutation_p_value"),
                "reason": result.get("decision_reason")}))
        else:
            summary["rejected"] = candidates
            summary["events"].append(append_event(game, {
                "type": "rejected", "signals": candidates,
                "holdout_diff": hold.get("mean_hit_diff_chained_minus_incumbent"),
                "holdout_p": hold.get("paired_permutation_p_value"),
                "reason": result.get("decision_reason") or "Patterned challenger did not win the holdout."}))
    state["updated_at"] = now_iso()
    save_promoted(game, state)
    summary["active"] = sorted(state["signals"].keys())
    return summary
