"""
backend/risk/portfolio_governor.py

[9.5/9.6] Cluster exposure caps and directional netting — Part 7 §7.4 of the
master plan: "Independent engines need a shared exposure manager, or three
strategies fire the same directional bet on correlated symbols and you take
3% risk thinking you took 1%."

Two guards, both applied in CircuitBreaker.check_symbol (see circuit_breaker.py):
  - Cluster exposure cap: aggregate open risk across all symbols in the same
    correlation cluster (e.g. XAUUSD+XAGUSD) must not exceed max_cluster_risk_pct.
  - Directional netting cap: aggregate open risk across all symbols in the
    same cluster AND the same direction must not exceed max_net_direction_risk_pct
    — three simultaneous USD-short bets (XAUUSD long, XAGUSD long, EURUSD
    long) are one bet, not three independent ones.

Both are 0 (disabled) by default — this module changes nothing until a user
explicitly sets a cap, per the same "no silent tightening" policy applied
elsewhere in this codebase.

CLUSTERING: true rolling-correlation-based dynamic clustering needs
synchronized multi-symbol price history that no current call site has ready
access to (CircuitBreaker only ever sees one symbol/signal at a time). This
ships a static, editable clustering table instead — grouping by known
correlated instrument families — which is immediately usable and correct for
the common cases (metals, majors sharing a base/quote currency, indices).
Swap SYMBOL_CLUSTERS for a live correlation-derived mapping later without
changing any call site here.
"""

from __future__ import annotations

# Default correlation clusters. A symbol not listed here resolves to its own
# singleton cluster (itself) — i.e. no netting effect, matching today's
# behaviour for anything not explicitly grouped.
SYMBOL_CLUSTERS: dict[str, str] = {
    # Precious metals — move together on real-rate/USD-strength shocks.
    "XAUUSD": "METALS", "GOLD": "METALS",
    "XAGUSD": "METALS", "SILVER": "METALS",
    "XPTUSD": "METALS", "XPDUSD": "METALS",

    # USD majors — each is fundamentally a USD bet, same direction convention
    # (long EURUSD/GBPUSD/AUDUSD/NZDUSD = short USD; long USDJPY/USDCHF/USDCAD = long USD).
    "EURUSD": "USD_MAJORS", "GBPUSD": "USD_MAJORS", "AUDUSD": "USD_MAJORS",
    "NZDUSD": "USD_MAJORS",
    "USDJPY": "USD_MAJORS_INV", "USDCHF": "USD_MAJORS_INV", "USDCAD": "USD_MAJORS_INV",

    # JPY crosses — dominated by carry-trade/risk-sentiment flow together.
    "EURJPY": "JPY_CROSSES", "GBPJPY": "JPY_CROSSES", "AUDJPY": "JPY_CROSSES",
    "CADJPY": "JPY_CROSSES", "CHFJPY": "JPY_CROSSES", "NZDJPY": "JPY_CROSSES",

    # US equity indices.
    "US30": "US_INDICES", "US500": "US_INDICES", "SPX": "US_INDICES",
    "NAS100": "US_INDICES", "USTEC": "US_INDICES", "NDX": "US_INDICES",
    "US2000": "US_INDICES",

    # European equity indices.
    "GER40": "EU_INDICES", "DAX": "EU_INDICES", "UK100": "EU_INDICES",
    "FRA40": "EU_INDICES", "EU50": "EU_INDICES",

    # Majors crypto — BTC dominance drags most alts with it intraday.
    "BTCUSD": "CRYPTO_MAJORS", "ETHUSD": "CRYPTO_MAJORS",
}

# Direction convention per cluster: for USD_MAJORS_INV (USDJPY/USDCHF/USDCAD),
# a BUY is a LONG-USD bet — the same directional bet as a SELL on USD_MAJORS
# (EURUSD/GBPUSD/etc, quote currency USD). Inverted clusters flip the
# effective direction before netting so these correctly aggregate as one bet.
_INVERTED_CLUSTERS = {"USD_MAJORS_INV"}


def resolve_cluster(symbol: str, overrides: dict[str, str] | None = None) -> str:
    """Cluster name for a symbol. Falls back to the symbol itself (singleton cluster, no netting effect)."""
    table = overrides if overrides else SYMBOL_CLUSTERS
    return table.get(symbol.upper(), symbol.upper())


def resolve_net_direction_key(symbol: str, direction: str, overrides: dict[str, str] | None = None) -> tuple[str, str]:
    """
    (cluster, effective_direction) — effective_direction already accounts for
    inverted clusters (USDJPY BUY nets against EURUSD SELL, both "long USD").
    """
    cluster = resolve_cluster(symbol, overrides)
    is_buy = direction.upper() in ("BUY", "BULLISH")
    base_cluster = cluster
    if cluster in _INVERTED_CLUSTERS:
        is_buy = not is_buy
        # Normalise the cluster name so USD_MAJORS and USD_MAJORS_INV net
        # against each other as the same underlying USD bet.
        base_cluster = cluster.removesuffix("_INV")
    return base_cluster, ("BUY" if is_buy else "SELL")
