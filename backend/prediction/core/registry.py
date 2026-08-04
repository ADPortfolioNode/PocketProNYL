"""Plugin registry for strategies and pattern agents."""

from __future__ import annotations

from typing import Callable, TypeVar

T = TypeVar("T")

_STRATEGIES: dict[str, type] = {}
_AGENTS: dict[str, type] = {}


def ensure_plugins_loaded() -> None:
    """Import built-in strategy and agent modules so decorators register them."""
    import importlib

    for module_name in ("prediction.strategies", "prediction.agents"):
        importlib.import_module(module_name)


def register_strategy(name: str) -> Callable[[type], type]:
    """Decorator to register a strategy class by name."""

    def decorator(cls: type) -> type:
        _STRATEGIES[name] = cls
        cls.strategy_name = name  # type: ignore[attr-defined]
        return cls

    return decorator


def register_agent(name: str) -> Callable[[type], type]:
    """Decorator to register a pattern agent class by name."""

    def decorator(cls: type) -> type:
        _AGENTS[name] = cls
        cls.agent_name = name  # type: ignore[attr-defined]
        return cls

    return decorator


def get_strategy_class(name: str) -> type | None:
    """Get a strategy class by name, loading its module on first request."""
    if name not in _STRATEGIES:
        try:
            # Dynamically import the strategy module to trigger registration
            import importlib
            importlib.import_module(f"prediction.strategies.{name}")
        except (ImportError, ModuleNotFoundError):
            # The module doesn't exist, so we can't load the class.
            return None
    return _STRATEGIES.get(name)


def get_agent_class(name: str) -> type | None:
    """Get a pattern agent class by name, loading its module on first request."""
    if name not in _AGENTS:
        try:
            # Dynamically import the agent module to trigger registration
            import importlib
            importlib.import_module(f"prediction.agents.{name}")
        except (ImportError, ModuleNotFoundError):
            # The module doesn't exist, so we can't load the class.
            return None
    return _AGENTS.get(name)


def list_strategies() -> list[str]:
    return sorted(_STRATEGIES.keys())


def list_agents() -> list[str]:
    return sorted(_AGENTS.keys())

