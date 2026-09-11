"""
Chat tool utilities for AI assistant functionality.
"""
from typing import Dict, Any, Optional
from datetime import datetime
import json
import os

from utils.file_tools import _tool_list_files, _tool_read_file, _tool_write_file


def _parse_datetime(value: Any) -> Optional[datetime]:
    """
    Parse various datetime formats into datetime objects.
    """
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value)
        except (ValueError, OSError):
            pass
    return None


def _extract_metadata_value(metadata: dict, preferred_tokens: list[str]) -> Optional[str]:
    """
    Extract a value from metadata preferring specific tokens.
    """
    for token in preferred_tokens:
        if token in metadata:
            return metadata[token]
    return None


def _normalize_tool_params(tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Map frontend param names to backend tool handlers."""
    normalized = dict(params or {})

    if tool_name == "list_files":
        if "path" in normalized and "directory" not in normalized:
            normalized["directory"] = normalized.pop("path")
    elif tool_name in {"read_file", "write_file"}:
        if "path" in normalized and "file_path" not in normalized:
            normalized["file_path"] = normalized.pop("path")

    return normalized


def _apply_line_range(content: str, start_line: Optional[int], end_line: Optional[int]) -> str:
    lines = content.splitlines()
    start_idx = max(1, int(start_line or 1)) - 1
    end_idx = int(end_line) if end_line is not None else len(lines)
    return "\n".join(lines[start_idx:end_idx])


async def _tool_self_diagnostics(_params: Dict[str, Any]) -> Dict[str, Any]:
    from utils.diagnostics import collect_runtime_diagnostics

    return await collect_runtime_diagnostics()


def _tool_optimize_suggestions(params: Dict[str, Any]) -> Dict[str, Any]:
    """Tune one game or all games against their chronological draw history."""
    from services.game_tuner import game_tuner

    game = params.get("game")
    return game_tuner.start(game=game)


def _tool_internet_search(params: Dict[str, Any]) -> Dict[str, Any]:
    query = (params.get("query") or "").strip()
    if not query:
        return {"status": "error", "message": "query is required"}

    return {
        "status": "success",
        "query": query,
        "results": [],
        "message": (
            "Live internet search is not configured on this server. "
            "Enable RAG in chat to ground answers from ChromaDB, or ask directly in chat."
        ),
    }


async def execute_chat_tool(tool_name: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run a concierge tool without invoking the language model."""
    normalized = _normalize_tool_params(tool_name, params or {})

    if tool_name == "list_files":
        return _tool_list_files(normalized)

    if tool_name == "read_file":
        result = _tool_read_file(normalized)
        if result.get("status") == "success" and (
            normalized.get("start_line") is not None or normalized.get("end_line") is not None
        ):
            result["content"] = _apply_line_range(
                result.get("content", ""),
                normalized.get("start_line"),
                normalized.get("end_line"),
            )
            result["size"] = len(result["content"])
        return result

    if tool_name == "write_file":
        return _tool_write_file(normalized)

    if tool_name == "self_diagnostics":
        return await _tool_self_diagnostics(normalized)

    if tool_name == "optimize_suggestions":
        return _tool_optimize_suggestions(normalized)

    if tool_name == "internet_search":
        return _tool_internet_search(normalized)

    return {
        "status": "error",
        "message": f"Unknown tool: {tool_name}",
    }


def _render_tool_response(tool_name: str, tool_result: Dict[str, Any]) -> str:
    """
    Format a tool result into a human-readable response.
    """
    if tool_result.get("status") == "error":
        return f"Tool '{tool_name}' failed: {tool_result.get('message', 'Unknown error')}"
    
    # Format successful responses based on tool type
    if tool_name == "list_files":
        files = tool_result.get("files", [])
        return f"Found {len(files)} files: {', '.join(files[:10])}" + ("..." if len(files) > 10 else "")
    elif tool_name == "read_file":
        content = tool_result.get("content", "")
        preview = content[:200] + "..." if len(content) > 200 else content
        return f"File content preview: {preview}"
    elif tool_name == "internet_search":
        message = tool_result.get("message")
        if message:
            return message
        results = tool_result.get("results", [])
        return f"Found {len(results)} search results"
    elif tool_name == "self_diagnostics":
        from utils.diagnostics import format_diagnostics_summary

        return format_diagnostics_summary(tool_result)
    elif tool_name == "optimize_suggestions":
        games = tool_result.get("games") or []
        if tool_result.get("status") in {"started", "running"}:
            scope = tool_result.get("game") or "all games"
            return f"Suggestion tuning started for {scope}. Poll /api/tuning_status for live progress."
        if tool_result.get("status") == "already_running":
            return "Suggestion tuning is already running. Poll /api/tuning_status for live progress."
        successful = sum(1 for game in games if game.get("status") == "ok")
        return f"Suggestion tuning completed for {successful}/{len(games)} game(s); report saved to the prediction state directory."
    else:
        return f"Tool '{tool_name}' completed successfully"