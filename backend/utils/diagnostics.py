"""
Shared runtime diagnostics for /api/diag and concierge self_diagnostics.
"""
from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import Any, Dict, Optional
from urllib import error as urlerror
from urllib import request as urlrequest


def _probe_http(url: str, timeout: float = 3.0) -> Dict[str, Any]:
    try:
        with urlrequest.urlopen(url, timeout=timeout) as resp:
            body = resp.read(512).decode("utf-8", errors="replace")
            return {
                "ok": True,
                "status": getattr(resp, "status", 200),
                "body_preview": body[:200],
            }
    except urlerror.HTTPError as exc:
        return {
            "ok": False,
            "status": exc.code,
            "error": str(exc),
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": None,
            "error": str(exc),
        }


def _probe_tcp(host: str, port: int, timeout: float = 2.0) -> Dict[str, Any]:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _count_model_artifacts(models_dir: Path) -> Dict[str, Any]:
    if not models_dir.is_dir():
        return {"ok": False, "count": 0, "path": str(models_dir), "error": "models directory missing"}

    artifacts = sorted(
        p.name for p in models_dir.iterdir()
        if p.is_file() and p.suffix in {".joblib", ".pkl", ".json"}
    )
    return {
        "ok": True,
        "count": len(artifacts),
        "path": str(models_dir),
        "sample": artifacts[:8],
    }


async def collect_runtime_diagnostics() -> Dict[str, Any]:
    """Gather backend-local health signals (no LLM)."""
    from services.lm_router import lm_router

    data_dir = os.environ.get("DATA_DIR", "/data")
    chroma_host = os.environ.get("CHROMA_HOST", "pocketpro_nyl_chroma")
    chroma_port = int(os.environ.get("CHROMA_PORT", "8000"))
    experiments_path = Path(data_dir) / "experiments" / "experiments.json"
    models_dir = Path(data_dir) / "models"

    chroma_url = f"http://{chroma_host}:{chroma_port}/api/v1/heartbeat"
    chroma_probe = _probe_http(chroma_url, timeout=4.0)
    local_health = _probe_http("http://127.0.0.1:5000/api/health", timeout=3.0)
    lm_snapshot = await lm_router.audit_connections(force=False)

    experiments_info: Dict[str, Any] = {
        "path": str(experiments_path),
        "exists": experiments_path.is_file(),
        "size_bytes": experiments_path.stat().st_size if experiments_path.is_file() else 0,
    }

    gateway_hints = []
    if not chroma_probe.get("ok"):
        gateway_hints.append("Chroma heartbeat failed — check pocketpro_nyl_chroma container.")
    if not local_health.get("ok"):
        gateway_hints.append("Backend /api/health not responding on port 5000.")
    if not experiments_path.is_file():
        gateway_hints.append("Experiments store missing — training history may be empty until first train.")

    return {
        "status": "success",
        "data_dir": data_dir,
        "chroma": {
            "host": chroma_host,
            "port": chroma_port,
            "heartbeat": chroma_probe,
        },
        "models": _count_model_artifacts(models_dir),
        "experiments": experiments_info,
        "local_health": local_health,
        "lm_providers": lm_snapshot,
        "gateway_hints": gateway_hints,
    }


def format_diagnostics_summary(payload: Dict[str, Any]) -> str:
    """Human-readable summary for chat tool responses."""
    if payload.get("status") == "error":
        return f"Diagnostics failed: {payload.get('message', 'unknown error')}"

    chroma = payload.get("chroma", {})
    chroma_ok = (chroma.get("heartbeat") or {}).get("ok", False)
    models = payload.get("models", {})
    experiments = payload.get("experiments", {})
    local_ok = (payload.get("local_health") or {}).get("ok", False)
    providers = (payload.get("lm_providers") or {}).get("ordered_available", [])
    provider_text = ", ".join(providers) if providers else "none"

    lines = [
        "**PocketPro:NYL runtime diagnostics**",
        f"- Backend health (local): {'OK' if local_ok else 'FAIL'}",
        f"- Chroma ({chroma.get('host')}:{chroma.get('port')}): {'OK' if chroma_ok else 'FAIL'}",
        f"- Models on disk: {models.get('count', 0)} under `{models.get('path', 'n/a')}`",
        f"- Experiments store: {'present' if experiments.get('exists') else 'missing'}",
        f"- LM providers: {provider_text}",
    ]

    hints = payload.get("gateway_hints") or []
    if hints:
        lines.append("")
        lines.append("**Gateway hints**")
        for hint in hints:
            lines.append(f"- {hint}")

    return "\n".join(lines)