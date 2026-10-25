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
        import backend.strategies.strategy_vwap.engine  # noqa: F401
        import backend.strategies.strategy_two.engine  # noqa: F401
        import backend.strategies.strategy_boom.engine  # noqa: F401
        import backend.strategies.strategy_orb.engine  # noqa: F401
        import backend.strategies.strategy_synth.engine  # noqa: F401
        import backend.strategies.strategy_trend.engine  # noqa: F401
        import backend.strategies.strategy_fib.engine  # noqa: F401  (fib pullback in trend, 2026-10-25)
        import backend.strategies.strategy_overnight.engine  # noqa: F401
        import backend.strategies.strategy_spike_resumption.engine  # noqa: F401
        import backend.strategies.strategy_kronos.engine  # noqa: F401  (reads forecasts; never imports torch)
        import backend.strategies.strategy_roboquant.engine  # noqa: F401  (five roboquant.dev templates)
        import backend.strategies.strategy_roboquant.engine_more  # noqa: F401  (nine more, 2026-10-20)
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
