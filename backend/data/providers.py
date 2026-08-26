"""
backend/data/providers.py

[Phase 13 Part E] Market-data provider registry — free and paid, selectable
per capability.

The design goal from your brief: run on free sources now, flip to paid later
with no code change. That means the *capability* is the stable thing and the
provider behind it is swappable, so nothing upstream ever imports a vendor.

Honest positioning, because free tiers are not equivalent to paid ones:

  * `mt5_orderflow` and `mt5_book` read your own terminal. Genuinely live, and
    the only true real-time sources here — but MT5 CFD ticks carry no aggressor
    flag, so CVD is INFERRED from price vs. bid/ask. A proxy, not a tape read.
  * `own_bars` computes correlation from your own history. No vendor is better
    at this, because it is your instruments and your timeframe.
  * `cboe` serves an exchange-published delayed file. Real data, ~15 min behind,
    no key, no SLA.
  * `yahoo` has no SLA and rate-limits aggressively. Fine as a fallback, not as
    a foundation.
  * `polygon` / `databento` are the paid slots. They are declared and wired but
    inert without a key — so the picker can show them, and the day you add a key
    nothing else has to change.

On latency, answering the question directly: polling is the wrong shape for
anything that moves in seconds. Order book and order flow are subscriptions
(MT5 `market_book_add`, or a venue WebSocket) pushed over the socket you already
have. Options chains only update on exchange dissemination, so polling them
faster than the feed buys nothing.
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from backend.utils.logger import get_logger

logger = get_logger(__name__)

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False


# ── Capabilities ─────────────────────────────────────────────────────────
CAP_OPTIONS_CHAIN = "options_chain"
CAP_GEX = "gex"
CAP_ORDER_BOOK = "order_book"
CAP_ORDER_FLOW = "order_flow"
CAP_CORRELATION = "correlation"
CAP_CALENDAR = "calendar"

ALL_CAPABILITIES = [
    CAP_ORDER_FLOW, CAP_ORDER_BOOK, CAP_CORRELATION,
    CAP_OPTIONS_CHAIN, CAP_GEX, CAP_CALENDAR,
]


@dataclass
class ProviderResult:
    ok: bool
    capability: str
    provider: str
    data: Any = None
    error: str | None = None
    latency_ms: float = 0.0
    fetched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    # Set when the data is delayed or derived rather than live and direct. The
    # UI shows this next to the number; a proxy presented as ground truth is
    # how people end up trusting a figure more than it deserves.
    caveat: str | None = None

    def to_dict(self) -> dict:
        return {
            "ok": self.ok, "capability": self.capability, "provider": self.provider,
            "data": self.data, "error": self.error, "latency_ms": round(self.latency_ms, 1),
            "fetched_at": self.fetched_at, "caveat": self.caveat,
        }


@dataclass
class ProviderHealth:
    name: str
    tier: str
    available: bool
    configured: bool
    capabilities: list[str]
    last_latency_ms: float | None = None
    last_error: str | None = None
    calls: int = 0
    errors: int = 0
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name, "tier": self.tier, "available": self.available,
            "configured": self.configured, "capabilities": self.capabilities,
            "last_latency_ms": self.last_latency_ms, "last_error": self.last_error,
            "calls": self.calls, "errors": self.errors,
            "error_rate": round(self.errors / self.calls, 3) if self.calls else 0.0,
            "note": self.note,
        }


class MarketDataProvider(Protocol):
    name: str
    tier: str
    capabilities: set[str]
    async def fetch(self, capability: str, **kw) -> ProviderResult: ...
    def health(self) -> ProviderHealth: ...


class BaseProvider:
    name = "base"
    tier = "free"
    capabilities: set[str] = set()
    note = ""

    def __init__(self):
        self._calls = 0
        self._errors = 0
        self._last_latency: float | None = None
        self._last_error: str | None = None

    def configured(self) -> bool:
        return True

    def available(self) -> bool:
        return True

    async def fetch(self, capability: str, **kw) -> ProviderResult:
        started = time.perf_counter()
        self._calls += 1
        try:
            if capability not in self.capabilities:
                raise ValueError(f"{self.name} does not serve '{capability}'")
            data, caveat = await self._fetch(capability, **kw)
            latency = (time.perf_counter() - started) * 1000
            self._last_latency = latency
            self._last_error = None
            return ProviderResult(True, capability, self.name, data,
                                  latency_ms=latency, caveat=caveat)
        except Exception as e:
            latency = (time.perf_counter() - started) * 1000
            self._errors += 1
            self._last_latency = latency
            self._last_error = str(e)
            logger.warning(f"[providers] {self.name}.{capability} failed: {e}")
            return ProviderResult(False, capability, self.name, None, str(e), latency)

    async def _fetch(self, capability: str, **kw) -> tuple[Any, str | None]:
        raise NotImplementedError

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            name=self.name, tier=self.tier, available=self.available(),
            configured=self.configured(), capabilities=sorted(self.capabilities),
            last_latency_ms=self._last_latency, last_error=self._last_error,
            calls=self._calls, errors=self._errors, note=self.note,
        )


# ─────────────────────────────────────────────────────────────────────────
# Free — your own terminal and your own history
# ─────────────────────────────────────────────────────────────────────────

class MT5OrderFlowProvider(BaseProvider):
    """
    CVD / order-flow imbalance from MT5 ticks, via `data/orderflow.py`.

    This is Phase 10's Tier 1 work finally getting a consumer. The caveat
    travels with the data rather than living only in a docstring: MT5 CFD ticks
    have no aggressor flag, so the sign of each tick is inferred from its price
    relative to bid/ask.
    """
    name = "mt5_orderflow"
    tier = "free"
    capabilities = {CAP_ORDER_FLOW}
    note = "Your MT5 terminal. Live, but CVD is inferred from bid/ask, not a true tape read."

    def available(self) -> bool:
        return mt5 is not None

    async def _fetch(self, capability: str, **kw):
        from backend.data.orderflow import compute_orderflow_snapshot
        symbol = kw.get("symbol")
        minutes = int(kw.get("minutes", 60))
        if not symbol:
            raise ValueError("symbol is required")
        snap = await compute_orderflow_snapshot(symbol, minutes=minutes)
        # Prefer the snapshot's own caveat: it knows which classification rule
        # actually ran (quote_rule vs. lee_ready) and whether "volume" is real
        # size or a tick count. A fixed string here would claim bid/ask
        # classification even on a quote-only feed, where the weaker mid-price
        # rule was used instead.
        return snap, snap.get("caveat") or (
            "MT5 CFD ticks carry no aggressor flag — direction is inferred, not read."
        )


class MT5BookProvider(BaseProvider):
    """
    Level-2 depth via `market_book_get`.

    CFD depth is thin and broker-synthesised, which is worth saying plainly —
    but it is real, it is yours, and it costs nothing.
    """
    name = "mt5_book"
    tier = "free"
    capabilities = {CAP_ORDER_BOOK}
    note = "Your MT5 terminal. CFD depth is thin and broker-synthesised."

    def available(self) -> bool:
        return mt5 is not None

    async def _fetch(self, capability: str, **kw):
        symbol = kw.get("symbol")
        if not symbol:
            raise ValueError("symbol is required")

        def _read():
            if not mt5.market_book_add(symbol):
                raise RuntimeError(f"market_book_add failed for {symbol}")
            try:
                book = mt5.market_book_get(symbol)
                if not book:
                    raise RuntimeError("empty book")
                bids, asks = [], []
                for e in book:
                    row = {"price": float(e.price), "volume": float(e.volume)}
                    # MT5 BookType: 0/1 = sell side (ask), 2/3 = buy side (bid).
                    (asks if e.type in (0, 1) else bids).append(row)
                bids.sort(key=lambda r: -r["price"])
                asks.sort(key=lambda r: r["price"])
                bid_vol = sum(r["volume"] for r in bids)
                ask_vol = sum(r["volume"] for r in asks)
                total = bid_vol + ask_vol
                return {
                    "symbol": symbol,
                    "bids": bids[:20], "asks": asks[:20],
                    "bid_volume": bid_vol, "ask_volume": ask_vol,
                    # Positive = more resting bid size than ask size.
                    "imbalance": round((bid_vol - ask_vol) / total, 4) if total else 0.0,
                    "spread": round(asks[0]["price"] - bids[0]["price"], 6) if bids and asks else None,
                }
            finally:
                mt5.market_book_release(symbol)

        data = await asyncio.to_thread(_read)
        return data, "Broker-synthesised CFD depth, not exchange order book."


class OwnBarsCorrelationProvider(BaseProvider):
    """
    Correlation matrix from your own MT5 history.

    Deliberately has no vendor alternative in the table: this is your
    instruments on your timeframe from your broker's prices. A third-party
    correlation on a different feed would be less accurate here, not more.
    """
    name = "own_bars"
    tier = "free"
    capabilities = {CAP_CORRELATION}
    note = "Computed from your own MT5 bars. No vendor does this better."

    def available(self) -> bool:
        return mt5 is not None

    async def _fetch(self, capability: str, **kw):
        import numpy as np
        import pandas as pd
        from backend.mt5.data_fetcher import DataFetcher

        symbols = kw.get("symbols") or []
        timeframe = kw.get("timeframe", "H1")
        count = int(kw.get("count", 500))
        if len(symbols) < 2:
            raise ValueError("at least two symbols are required")

        series: dict[str, pd.Series] = {}
        for sym in symbols:
            df = await DataFetcher.get_historical_data(sym, timeframe, count=count)
            if df is None or df.empty:
                continue
            s = df.set_index("time")["close"] if "time" in df.columns else df["close"]
            series[sym] = s.astype(float)

        if len(series) < 2:
            raise RuntimeError("not enough symbols returned data")

        # Correlate RETURNS, not prices: two upward-drifting price series
        # correlate near 1.0 regardless of whether they actually co-move.
        frame = pd.DataFrame(series).dropna()
        returns = frame.pct_change().dropna()
        corr = returns.corr()

        pairs = []
        cols = list(corr.columns)
        for i, a in enumerate(cols):
            for b in cols[i + 1:]:
                v = corr.loc[a, b]
                if not (isinstance(v, float) and math.isnan(v)):
                    pairs.append({"a": a, "b": b, "corr": round(float(v), 4)})
        pairs.sort(key=lambda p: -abs(p["corr"]))

        return {
            "symbols": cols,
            "timeframe": timeframe,
            "bars": int(len(returns)),
            "matrix": {a: {b: round(float(corr.loc[a, b]), 4) for b in cols} for a in cols},
            "pairs": pairs,
        }, None


class ForexFactoryCalendarProvider(BaseProvider):
    """Economic calendar — the feed `news_filter.py` already uses."""
    name = "forexfactory"
    tier = "free"
    capabilities = {CAP_CALENDAR}
    note = "Free weekly JSON. Already the live bot's calendar source."
    URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

    def available(self) -> bool:
        return HAS_HTTPX

    async def _fetch(self, capability: str, **kw):
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(self.URL)
            r.raise_for_status()
            events = r.json()
        impact = kw.get("impact")
        if impact:
            events = [e for e in events if str(e.get("impact", "")).lower() == impact.lower()]
        return {"events": events[:400], "count": len(events)}, None


# ─────────────────────────────────────────────────────────────────────────
# Free — options chains
# ─────────────────────────────────────────────────────────────────────────

def _compute_gex(chain: list[dict], spot: float, contract_size: int = 100) -> dict:
    """
    Dealer gamma exposure by strike.

    Convention used here, stated because there is no universal one: dealers are
    assumed long calls and short puts against retail, so call gamma is positive
    and put gamma negative. The flip point is where cumulative GEX crosses zero
    — above it dealers hedge against trend (suppressing volatility), below it
    with trend (amplifying it).

    This is a MODEL over an options chain, not a data feed. Every "GEX vendor"
    is doing the same arithmetic over the same public chain, which is why the
    recommendation is to compute it yourself and only pay for the chain.
    """
    by_strike: dict[float, float] = {}
    total = 0.0
    for row in chain:
        strike = row.get("strike")
        gamma = row.get("gamma")
        oi = row.get("open_interest")
        if strike is None or gamma is None or oi is None:
            continue
        sign = 1.0 if str(row.get("type", "")).lower().startswith("c") else -1.0
        # Standard formulation: gamma x OI x contract size x spot^2 x 1%
        gex = sign * float(gamma) * float(oi) * contract_size * (spot ** 2) * 0.01
        by_strike[float(strike)] = by_strike.get(float(strike), 0.0) + gex
        total += gex

    strikes = sorted(by_strike)
    cumulative, running, flip = [], 0.0, None
    for k in strikes:
        prev = running
        running += by_strike[k]
        cumulative.append({"strike": k, "gex": round(by_strike[k], 2), "cumulative": round(running, 2)})
        if flip is None and prev < 0 <= running:
            flip = k

    return {
        "spot": spot,
        "total_gex": round(total, 2),
        "regime": "positive" if total > 0 else "negative",
        "flip_strike": flip,
        "by_strike": cumulative,
        "interpretation": (
            "Positive total GEX: dealers hedge against the move, which tends to "
            "suppress realised volatility."
            if total > 0 else
            "Negative total GEX: dealers hedge with the move, which tends to "
            "amplify realised volatility."
        ),
    }


class YahooOptionsProvider(BaseProvider):
    """
    Options chains via `yfinance`.

    No key, wide coverage, and no SLA whatsoever — Yahoo rate-limits hard and
    changes shape without notice. Fine as a fallback; not something to build a
    strategy's dependency on.
    """
    name = "yahoo"
    tier = "free"
    capabilities = {CAP_OPTIONS_CHAIN, CAP_GEX}
    note = "No key needed. No SLA — rate-limits aggressively and can break without notice."

    def available(self) -> bool:
        try:
            import yfinance  # noqa: F401
            return True
        except ImportError:
            return False

    async def _fetch(self, capability: str, **kw):
        import yfinance as yf

        ticker = kw.get("ticker") or kw.get("symbol")
        if not ticker:
            raise ValueError("ticker is required")
        expiry = kw.get("expiry")

        def _pull():
            t = yf.Ticker(ticker)
            expiries = list(t.options or [])
            if not expiries:
                raise RuntimeError(f"no option expiries for {ticker}")
            exp = expiry if expiry in expiries else expiries[0]
            oc = t.option_chain(exp)
            spot = float(t.fast_info.get("last_price") or t.fast_info.get("previous_close") or 0)

            rows = []
            for frame, kind in ((oc.calls, "call"), (oc.puts, "put")):
                for _, r in frame.iterrows():
                    rows.append({
                        "type": kind,
                        "strike": float(r.get("strike", 0)),
                        "open_interest": float(r.get("openInterest") or 0),
                        "volume": float(r.get("volume") or 0),
                        "implied_volatility": float(r.get("impliedVolatility") or 0),
                        "last_price": float(r.get("lastPrice") or 0),
                        # yfinance does not return greeks; gamma is approximated
                        # below so GEX is computable at all. Flagged as derived.
                        "gamma": None,
                    })
            return {"ticker": ticker, "expiry": exp, "expiries": expiries,
                    "spot": spot, "chain": rows}

        data = await asyncio.to_thread(_pull)

        if capability == CAP_GEX:
            _approximate_gamma(data["chain"], data["spot"], data["expiry"])
            gex = _compute_gex(data["chain"], data["spot"])
            return gex, ("Gamma is APPROXIMATED with Black-Scholes from Yahoo's implied "
                         "volatility — Yahoo does not publish greeks. Directionally useful, "
                         "not a dealer-desk figure.")
        return data, "Delayed quotes, no SLA. Greeks are not provided by this source."


def _approximate_gamma(chain: list[dict], spot: float, expiry: str) -> None:
    """
    Fill in Black-Scholes gamma in place when the source does not publish greeks.

    Marked as an approximation everywhere it surfaces. Assumes zero rate and
    zero dividend, which is a real simplification — it matters more for LEAPS
    than for the near-dated strikes that dominate GEX.
    """
    try:
        exp_dt = datetime.strptime(expiry, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        tau = max((exp_dt - datetime.now(timezone.utc)).days, 1) / 365.0
    except Exception:
        tau = 30 / 365.0

    for row in chain:
        iv = row.get("implied_volatility") or 0.0
        k = row.get("strike") or 0.0
        if iv <= 0 or k <= 0 or spot <= 0:
            row["gamma"] = 0.0
            continue
        try:
            d1 = (math.log(spot / k) + 0.5 * iv * iv * tau) / (iv * math.sqrt(tau))
            pdf = math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi)
            row["gamma"] = pdf / (spot * iv * math.sqrt(tau))
            row["gamma_source"] = "approximated"
        except (ValueError, ZeroDivisionError):
            row["gamma"] = 0.0


class CBOEOptionsProvider(BaseProvider):
    """
    CBOE's public delayed quote endpoint.

    Recommended over Yahoo as the free default for one reason: it comes from the
    exchange and publishes greeks, so GEX is computed from real gamma rather
    than a Black-Scholes approximation. Delayed and unsupported, but not
    scraped and not reverse-engineered.
    """
    name = "cboe"
    tier = "free"
    capabilities = {CAP_OPTIONS_CHAIN, CAP_GEX}
    note = "Exchange-published delayed data, greeks included. No key. Delayed ~15 min."
    URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{sym}.json"

    def available(self) -> bool:
        return HAS_HTTPX

    async def _fetch(self, capability: str, **kw):
        ticker = (kw.get("ticker") or kw.get("symbol") or "").upper()
        if not ticker:
            raise ValueError("ticker is required")
        # Index options live under an underscore prefix (_SPX, _NDX).
        sym = f"_{ticker}" if ticker in {"SPX", "NDX", "RUT", "VIX", "DJX"} else ticker

        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(self.URL.format(sym=sym))
            r.raise_for_status()
            payload = r.json()

        d = payload.get("data") or {}
        spot = float(d.get("current_price") or d.get("close") or 0)
        rows = []
        for o in d.get("options") or []:
            # CBOE option ids: SPX240119C04800000 — the C/P and the strike are
            # positionally encoded in the last 9 characters.
            oid = o.get("option", "")
            if len(oid) < 9:
                continue
            kind = "call" if oid[-9] == "C" else "put"
            try:
                strike = int(oid[-8:]) / 1000.0
            except ValueError:
                continue
            rows.append({
                "type": kind,
                "strike": strike,
                "open_interest": float(o.get("open_interest") or 0),
                "volume": float(o.get("volume") or 0),
                "implied_volatility": float(o.get("iv") or 0),
                "gamma": float(o.get("gamma") or 0),
                "delta": float(o.get("delta") or 0),
                "last_price": float(o.get("last_trade_price") or 0),
            })

        if not rows:
            raise RuntimeError(f"no option rows returned for {ticker}")

        if capability == CAP_GEX:
            return _compute_gex(rows, spot), "Delayed ~15 minutes. Greeks are exchange-published."
        return {"ticker": ticker, "spot": spot, "chain": rows, "count": len(rows)}, \
               "Delayed ~15 minutes."


# ─────────────────────────────────────────────────────────────────────────
# Paid — declared, wired, inert without a key
# ─────────────────────────────────────────────────────────────────────────

class PolygonProvider(BaseProvider):
    """Polygon.io. Real-time chains with greeks; needs POLYGON_API_KEY."""
    name = "polygon"
    tier = "paid"
    capabilities = {CAP_OPTIONS_CHAIN, CAP_GEX}
    note = "Real-time chains with greeks. Requires POLYGON_API_KEY."

    def _key(self) -> str:
        import os
        return os.environ.get("POLYGON_API_KEY", "")

    def configured(self) -> bool:
        return bool(self._key())

    def available(self) -> bool:
        return HAS_HTTPX

    async def _fetch(self, capability: str, **kw):
        key = self._key()
        if not key:
            raise RuntimeError("POLYGON_API_KEY is not set")
        ticker = (kw.get("ticker") or kw.get("symbol") or "").upper()
        if not ticker:
            raise ValueError("ticker is required")

        url = f"https://api.polygon.io/v3/snapshot/options/{ticker}"
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(url, params={"limit": 250, "apiKey": key})
            r.raise_for_status()
            payload = r.json()

        rows, spot = [], 0.0
        for res in payload.get("results") or []:
            det = res.get("details") or {}
            greeks = res.get("greeks") or {}
            ua = res.get("underlying_asset") or {}
            spot = float(ua.get("price") or spot)
            rows.append({
                "type": det.get("contract_type"),
                "strike": float(det.get("strike_price") or 0),
                "open_interest": float(res.get("open_interest") or 0),
                "volume": float((res.get("day") or {}).get("volume") or 0),
                "implied_volatility": float(res.get("implied_volatility") or 0),
                "gamma": float(greeks.get("gamma") or 0),
                "delta": float(greeks.get("delta") or 0),
            })

        if capability == CAP_GEX:
            return _compute_gex(rows, spot), None
        return {"ticker": ticker, "spot": spot, "chain": rows, "count": len(rows)}, None


class DatabentoProvider(BaseProvider):
    """Databento MBO — true tape with aggressor flags. Needs DATABENTO_API_KEY."""
    name = "databento"
    tier = "paid"
    capabilities = {CAP_ORDER_FLOW, CAP_ORDER_BOOK}
    note = "True MBO tape with aggressor flags — no inference. Requires DATABENTO_API_KEY."

    def _key(self) -> str:
        import os
        return os.environ.get("DATABENTO_API_KEY", "")

    def configured(self) -> bool:
        return bool(self._key())

    def available(self) -> bool:
        try:
            import databento  # noqa: F401
            return True
        except ImportError:
            return False

    async def _fetch(self, capability: str, **kw):
        if not self._key():
            raise RuntimeError("DATABENTO_API_KEY is not set")
        # Left unimplemented on purpose rather than faked: Databento's schema
        # and dataset selection depend on which venues the subscription covers,
        # and guessing them would produce confidently wrong data. The slot is
        # here so the registry and the UI already know about it.
        raise NotImplementedError(
            "Databento is registered but not implemented — its dataset/schema "
            "selection depends on your subscription. Wire it when you subscribe."
        )


# ─────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────

class ProviderRegistry:
    """
    Capability -> provider selection, with a per-capability TTL cache.

    Selection is persisted by the caller (config), not here, so switching a
    provider is a settings change rather than a deploy.
    """

    # Defaults are the free tier throughout, and each is the best free option
    # for its capability rather than merely the first one that works.
    DEFAULT_SELECTION = {
        CAP_ORDER_FLOW: "mt5_orderflow",
        CAP_ORDER_BOOK: "mt5_book",
        CAP_CORRELATION: "own_bars",
        CAP_OPTIONS_CHAIN: "cboe",
        CAP_GEX: "cboe",
        CAP_CALENDAR: "forexfactory",
    }

    # Cache TTLs matched to how fast each source actually changes AND to how
    # long it takes to produce. Polling an options chain faster than the
    # exchange disseminates it buys nothing.
    #
    # Order flow was 2s, which was wrong in a way only measuring caught: a
    # 60-minute BTCUSD window is ~24k ticks and takes ~6s to fetch and classify,
    # so a 2s TTL meant every single request missed the cache and the panel
    # spent all its time refetching data that had barely changed. 15s is still
    # well inside "live" for a window measured in hours.
    #
    # The cache key includes the request kwargs, so a short window (minutes=5)
    # and a long one (minutes=240) are cached separately — asking for a 5-minute
    # window stays genuinely fast.
    TTL = {
        CAP_ORDER_FLOW: 15,
        CAP_ORDER_BOOK: 1,      # depth genuinely changes tick-to-tick
        CAP_CORRELATION: 300,
        CAP_OPTIONS_CHAIN: 60,
        CAP_GEX: 60,
        CAP_CALENDAR: 900,
    }

    def __init__(self):
        self._providers: dict[str, BaseProvider] = {}
        for cls in (
            MT5OrderFlowProvider, MT5BookProvider, OwnBarsCorrelationProvider,
            ForexFactoryCalendarProvider, CBOEOptionsProvider, YahooOptionsProvider,
            PolygonProvider, DatabentoProvider,
        ):
            p = cls()
            self._providers[p.name] = p
        self._selection = dict(self.DEFAULT_SELECTION)
        self._cache: dict[str, tuple[float, ProviderResult]] = {}

    # ── selection ──
    def select(self, capability: str, provider_name: str) -> None:
        if provider_name not in self._providers:
            raise ValueError(f"unknown provider '{provider_name}'")
        if capability not in self._providers[provider_name].capabilities:
            raise ValueError(f"{provider_name} does not serve '{capability}'")
        self._selection[capability] = provider_name

    def selection(self) -> dict[str, str]:
        return dict(self._selection)

    def providers_for(self, capability: str) -> list[BaseProvider]:
        return [p for p in self._providers.values() if capability in p.capabilities]

    def catalogue(self) -> dict:
        return {
            "capabilities": {
                cap: {
                    "selected": self._selection.get(cap),
                    "options": [p.health().to_dict() for p in self.providers_for(cap)],
                    "ttl_seconds": self.TTL.get(cap),
                }
                for cap in ALL_CAPABILITIES
            },
            "providers": [p.health().to_dict() for p in self._providers.values()],
        }

    # ── fetch ──
    async def fetch(self, capability: str, use_cache: bool = True, **kw) -> ProviderResult:
        name = self._selection.get(capability)
        provider = self._providers.get(name) if name else None
        if provider is None:
            return ProviderResult(False, capability, name or "none", None,
                                  f"No provider selected for '{capability}'")

        key = f"{capability}:{name}:{sorted(kw.items())}"
        ttl = self.TTL.get(capability, 30)
        if use_cache:
            hit = self._cache.get(key)
            if hit and (time.time() - hit[0]) < ttl:
                return hit[1]

        result = await provider.fetch(capability, **kw)
        if result.ok:
            self._cache[key] = (time.time(), result)
        elif use_cache:
            # Serve stale rather than nothing when a provider blips — a chart
            # that goes blank on one failed poll is worse than a chart that says
            # "as of 40 seconds ago".
            hit = self._cache.get(key)
            if hit and (time.time() - hit[0]) < ttl * 10:
                stale = hit[1]
                return ProviderResult(
                    True, capability, stale.provider, stale.data,
                    error=f"stale: {result.error}", latency_ms=0.0,
                    fetched_at=stale.fetched_at,
                    caveat=(stale.caveat or "") + " (served from cache after a fetch failure)",
                )
        return result

    def health(self) -> list[dict]:
        return [p.health().to_dict() for p in self._providers.values()]


registry = ProviderRegistry()
