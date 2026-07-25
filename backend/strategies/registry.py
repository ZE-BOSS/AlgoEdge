"""
backend/strategies/registry.py

Strategy plugin registry.
Allows dynamic loading of different strategies (e.g. SMC, RL, Momentum).
"""

from typing import Dict, Type
from backend.strategies.base_strategy import BaseStrategy
from backend.utils.logger import get_logger

logger = get_logger(__name__)

# Registry of available strategies
_STRATEGIES: Dict[str, Type[BaseStrategy]] = {}


_LOADED = False

def _load_strategies():
    global _LOADED
    if not _LOADED:
        import backend.strategies.strategy_one.engine
        import backend.strategies.strategy_two.engine
        import backend.strategies.strategy_three_crt.engine
        import backend.strategies.strategy_four_htf_fvg_flip.engine
        import backend.strategies.strategy_five_bias_ifvg.engine
        import backend.strategies.strategy_six_ny_open_retest.engine
        _LOADED = True

def register_strategy(name: str):
    """Decorator to register a strategy class."""
    def decorator(cls: Type[BaseStrategy]):
        _STRATEGIES[name] = cls
        return cls
    return decorator


def get_strategy(name: str) -> Type[BaseStrategy]:
    """Get a strategy class by name."""
    _load_strategies()
    if name not in _STRATEGIES:
        raise ValueError(f"Strategy {name} not found in registry.")
    return _STRATEGIES[name]


def list_strategies() -> list[str]:
    """Get all registered strategy names."""
    _load_strategies()
    return list(_STRATEGIES.keys())
