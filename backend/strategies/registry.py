"""
backend/strategies/registry.py

Strategy plugin registry.
Allows dynamic loading of different strategies (e.g. SMC, RL, Momentum).
"""


from backend.strategies.base_strategy import BaseStrategy
from backend.utils.logger import get_logger

logger = get_logger(__name__)

# Registry of available strategies
_STRATEGIES: dict[str, type[BaseStrategy]] = {}


_LOADED = False

def _load_strategies():
    global _LOADED
    if not _LOADED:
        import backend.strategies.strategy_apa.engine  # noqa: F401
        import backend.strategies.strategy_vwap.engine  # noqa: F401
        import backend.strategies.strategy_two.engine  # noqa: F401
        import backend.strategies.strategy_three_crt.engine  # noqa: F401
        import backend.strategies.strategy_four_htf_fvg_flip.engine  # noqa: F401
        import backend.strategies.strategy_five_bias_ifvg.engine  # noqa: F401
        import backend.strategies.strategy_six_ny_open_retest.engine  # noqa: F401
        import backend.strategies.strategy_boom.engine  # noqa: F401
        import backend.strategies.strategy_synth.engine  # noqa: F401
        _LOADED = True

def register_strategy(name: str):
    """Decorator to register a strategy class."""
    def decorator(cls: type[BaseStrategy]):
        _STRATEGIES[name] = cls
        return cls
    return decorator


def get_strategy(name: str) -> type[BaseStrategy]:
    """Get a strategy class by name."""
    _load_strategies()
    if name not in _STRATEGIES:
        logger.error(f"Registry dump: {list(_STRATEGIES.keys())}")
        raise ValueError(f"Strategy {name} not found in registry.")
    return _STRATEGIES[name]


def list_strategies() -> list[str]:
    """Get all registered strategy names."""
    _load_strategies()
    return list(_STRATEGIES.keys())


def get_all_strategies() -> dict[str, type[BaseStrategy]]:
    """Return the full {strategy_id: class} registry dict."""
    _load_strategies()
    return dict(_STRATEGIES)
