"""
backend/data/orderflow.py

[10.1-10.4] Order Flow Tier 1 — MT5-feasible, no external vendor (Master Plan
§9.2 / Part 9). Honest feasibility note from the plan (§9.1) that scopes this
module: MT5 CFD data has no aggressor flag on ticks and no true exchange
volume — CVD here is INFERRED from tick price relative to bid/ask, not read
directly from an order-flow feed. This is the single most useful primitive
that IS computable from `copy_ticks_range`, per the plan; it is a proxy, not
a ground-truth tape read. A Bookmap-style DOM heatmap is explicitly NOT
attempted (needs full order-book depth history MT5 CFDs don't provide).

Wiring: [10.5] these are confluence CONTRIBUTORS ONLY (never a veto), meant
to feed backend/services/market_context.py's MarketContext the way the
Part 7 §7.3 framework describes — not currently wired into MarketContext
directly (see that module's docstring); the pure computation functions here
are ready to be called from there once a symbol's tick history is fetched.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from backend.utils.logger import get_logger

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

logger = get_logger(__name__)

_executor = ThreadPoolExecutor(max_workers=1)


# ─────────────────────────────────────────────────────────────────────────
# [10.1] Tick classification and CVD
# ─────────────────────────────────────────────────────────────────────────

def tick_price(ticks: pd.DataFrame) -> pd.Series:
    """
    The price series to reason about, whatever shape the feed is.

    Prefers a real traded price (`last`), falls back to the bid/ask mid. This
    matters more than it looks: on the live Deriv feed `last` is 0.0 on EVERY
    tick (verified 2026-08-23 — a quote feed, not a trade feed), so anything
    reading `last` directly gets a column of zeros and silently produces
    garbage rather than failing.
    """
    if "last" in ticks.columns and (ticks["last"] > 0).any():
        last = ticks["last"].replace(0, np.nan)
        if "bid" in ticks.columns and "ask" in ticks.columns:
            return last.fillna((ticks["bid"] + ticks["ask"]) / 2.0)
        return last.ffill().bfill()
    if "bid" in ticks.columns and "ask" in ticks.columns:
        return (ticks["bid"] + ticks["ask"]) / 2.0
    if "close" in ticks.columns:
        return ticks["close"]
    raise ValueError("ticks carry no usable price column")


def has_traded_prices(ticks: pd.DataFrame) -> bool:
    """Whether this feed publishes traded prices at all (vs. quotes only)."""
    return "last" in ticks.columns and bool((ticks["last"] > 0).any())


def tick_volume(ticks: pd.DataFrame) -> pd.Series:
    """
    Per-tick size, or 1.0 per tick when the feed publishes no size.

    On a quote-only feed every tick is worth 1, which makes "volume" a TICK
    COUNT. That is a real measure of activity but not of size, and the snapshot
    labels it as such rather than implying traded size.
    """
    for col in ("volume_real", "volume"):
        if col in ticks.columns and (ticks[col] > 0).any():
            return ticks[col].replace(0, np.nan).fillna(1.0)
    return pd.Series(1.0, index=ticks.index)


def classification_method(ticks: pd.DataFrame) -> str:
    """Which rule `classify_ticks` will use — surfaced so a caller can caveat it."""
    if ticks.empty:
        return "none"
    return "quote_rule" if not has_traded_prices(ticks) else "lee_ready"


def classify_ticks(ticks: pd.DataFrame) -> pd.Series:
    """
    Classify each tick as BUY (+volume), SELL (-volume) or 0 (neutral).

    Two rules, chosen by what the feed actually publishes:

    **Lee-Ready** (feeds that publish a traded price):
        last >= ask  -> BUY  (traded at/through the offer — buyer-initiated)
        last <= bid  -> SELL (traded at/through the bid — seller-initiated)
        else         -> midpoint rule; exactly at mid -> 0

    **Quote rule** (quote-only feeds — bid/ask move, no `last`):
        mid rises  -> BUY-side pressure
        mid falls  -> SELL-side pressure
        unchanged  -> carry the previous sign forward (standard tick-rule
                      convention: an unchanged quote is not new information,
                      so it inherits the prevailing direction)

    **Why the second rule exists — a defect this fixes.** The original
    implementation only had the first, and read `last` unconditionally. On the
    live Deriv feed `last` is 0.0 on every tick, which made `last <= bid`
    trivially true, so *every* tick classified as SELL: CVD came out as exactly
    −(tick count) and imbalance as exactly −1.0 on every symbol. That is not a
    weak signal, it is a constant, and it would have looked like relentless
    selling pressure forever. Verified against BTCUSD and Volatility 75 Index
    on 2026-08-23; both returned imbalance = −1.0 before this change.

    The quote rule is a WEAKER proxy than Lee-Ready — it infers pressure from
    quote movement rather than from where trades printed. `classification_method`
    reports which was used so callers can say so.
    """
    if ticks.empty:
        return pd.Series(dtype=float)

    vol = tick_volume(ticks)

    # ── Quote-only feed: tick rule on the mid ──
    if not has_traded_prices(ticks):
        mid = (ticks["bid"] + ticks["ask"]) / 2.0
        direction = np.sign(mid.diff().fillna(0.0))
        # Carry the last non-zero direction through unchanged quotes; leading
        # ticks before any move stay 0 rather than being guessed.
        direction = direction.replace(0.0, np.nan).ffill().fillna(0.0)
        return direction * vol

    # ── Feed with traded prices: Lee-Ready ──
    price = ticks["last"].replace(0, np.nan).fillna((ticks["bid"] + ticks["ask"]) / 2.0)
    bid = ticks["bid"]
    ask = ticks["ask"]
    mid = (bid + ask) / 2.0
    signed = pd.Series(0.0, index=ticks.index)

    at_or_above_ask = price >= ask
    at_or_below_bid = price <= bid
    remainder = ~(at_or_above_ask | at_or_below_bid)

    signed.loc[at_or_above_ask] = vol.loc[at_or_above_ask]
    signed.loc[at_or_below_bid] = -vol.loc[at_or_below_bid]

    # Midpoint rule for whatever's left (price strictly between bid and ask).
    closer_to_ask = remainder & (price > mid)
    closer_to_bid = remainder & (price < mid)
    signed.loc[closer_to_ask] = vol.loc[closer_to_ask]
    signed.loc[closer_to_bid] = -vol.loc[closer_to_bid]
    # Exactly at mid: stays 0 (genuinely unclassifiable, not guessed).

    return signed


def compute_cvd(ticks: pd.DataFrame) -> pd.Series:
    """Cumulative Volume Delta — running sum of classify_ticks()'s signed volume, indexed like `ticks`."""
    return classify_ticks(ticks).cumsum()


def aggregate_cvd_per_bar(ticks: pd.DataFrame, bar_index: pd.DatetimeIndex) -> pd.Series:
    """
    [10.1] Bucket per-tick signed volume into the OHLC bar timeframe given by
    `bar_index` (e.g. the M5 candle index a strategy is already working
    with), returning per-bar delta (not cumulative — see `.cumsum()` on the
    result for a per-bar CVD series) aligned to `bar_index`.
    """
    if ticks.empty or len(bar_index) == 0:
        return pd.Series(0.0, index=bar_index)

    signed = classify_ticks(ticks)
    tick_times = ticks["time"] if "time" in ticks.columns else ticks.index
    tick_times = pd.to_datetime(tick_times, unit="s", utc=True) if pd.api.types.is_numeric_dtype(tick_times) else pd.DatetimeIndex(tick_times)

    bar_index_sorted = pd.DatetimeIndex(bar_index).sort_values()
    # Assign each tick to the bar it falls within: bar[i] covers
    # [bar_index[i], bar_index[i+1]) — the standard OHLC-bar convention.
    bar_positions = np.searchsorted(bar_index_sorted.values, tick_times.values, side="right") - 1
    bar_positions = np.clip(bar_positions, 0, len(bar_index_sorted) - 1)

    per_bar = pd.Series(0.0, index=bar_index_sorted)
    grouped = pd.Series(signed.values, index=bar_index_sorted[bar_positions]).groupby(level=0).sum()
    per_bar.loc[grouped.index] = grouped.values

    # Return aligned to the ORIGINAL (possibly unsorted) bar_index.
    return per_bar.reindex(bar_index).fillna(0.0)


# ─────────────────────────────────────────────────────────────────────────
# [10.2] Delta divergence
# ─────────────────────────────────────────────────────────────────────────

def detect_delta_divergence(
    candles: pd.DataFrame,
    cvd_per_bar: pd.Series,
    lookback: int = 10,
) -> dict | None:
    """
    [10.2] Price makes a new high (low) over `lookback` bars while cumulative
    delta does NOT confirm it — a classic order-flow divergence warning
    (buying pressure not actually behind the new high, or selling pressure
    not behind the new low).

    `cvd_per_bar` must be a CUMULATIVE series (use `.cumsum()` on
    `aggregate_cvd_per_bar()`'s output), aligned to `candles`' index.

    Returns None (no divergence) or a dict describing the divergence.
    Never a veto — a confluence contributor only, per the module docstring.
    """
    if len(candles) < lookback + 1 or len(cvd_per_bar) < lookback + 1:
        return None

    recent_highs = candles["high"].iloc[-lookback:]
    recent_lows = candles["low"].iloc[-lookback:]
    recent_cvd = cvd_per_bar.iloc[-lookback:]

    price_new_high = candles["high"].iloc[-1] >= recent_highs.max()
    price_new_low = candles["low"].iloc[-1] <= recent_lows.min()
    cvd_new_high = cvd_per_bar.iloc[-1] >= recent_cvd.max()
    cvd_new_low = cvd_per_bar.iloc[-1] <= recent_cvd.min()

    if price_new_high and not cvd_new_high:
        return {"type": "BEARISH_DIVERGENCE", "reason": "price made a new high without CVD confirmation"}
    if price_new_low and not cvd_new_low:
        return {"type": "BULLISH_DIVERGENCE", "reason": "price made a new low without CVD confirmation"}
    return None


# ─────────────────────────────────────────────────────────────────────────
# [10.3] Absorption
# ─────────────────────────────────────────────────────────────────────────

def detect_absorption(
    candles: pd.DataFrame,
    delta_per_bar: pd.Series,
    price_move_atr_mult: float = 0.3,
    delta_std_mult: float = 2.0,
    atr: float | None = None,
) -> dict | None:
    """
    [10.3] High delta, minimal price movement — large one-sided volume was
    absorbed at a level rather than moving price, a classic "someone big is
    defending this level" signal.

    On the LATEST bar only: |delta| >= `delta_std_mult` standard deviations
    above its own recent (20-bar) mean, while the bar's own range is <
    `price_move_atr_mult` × ATR.
    """
    if len(candles) < 20 or len(delta_per_bar) < 20:
        return None

    from backend.strategies.core.swing_structure import calculate_atr
    atr_val = atr if atr is not None else calculate_atr(candles, 14)
    if atr_val <= 0:
        return None

    latest_range = candles["high"].iloc[-1] - candles["low"].iloc[-1]
    latest_delta = delta_per_bar.iloc[-1]
    recent_delta = delta_per_bar.iloc[-21:-1]
    delta_std = recent_delta.abs().std()
    delta_mean = recent_delta.abs().mean()

    if delta_std <= 0 or pd.isna(delta_std):
        return None

    delta_z = (abs(latest_delta) - delta_mean) / delta_std
    range_ok = latest_range < price_move_atr_mult * atr_val

    if delta_z >= delta_std_mult and range_ok:
        return {
            "type": "ABSORPTION",
            "direction": "BUY_ABSORBED" if latest_delta > 0 else "SELL_ABSORBED",
            "delta_z": round(float(delta_z), 2),
            "range_atr_pct": round(float(latest_range / atr_val), 3),
        }
    return None


# ─────────────────────────────────────────────────────────────────────────
# [10.4] Volume profile / VPOC / value area
# ─────────────────────────────────────────────────────────────────────────

def compute_volume_profile(
    ticks: pd.DataFrame,
    num_bins: int = 50,
    value_area_pct: float = 0.70,
) -> dict:
    """
    [10.4] Volume-at-price histogram from tick data, plus VPOC (the price bin
    with the most volume) and the value area (the tightest contiguous price
    range containing `value_area_pct` of total volume — the standard
    "expand outward from VPOC" value-area algorithm).

    Returns {"profile": {price_bin: volume}, "vpoc": float, "value_area_low": float, "value_area_high": float}.
    Empty/degenerate input returns all-None values rather than raising.
    """
    empty = {"profile": {}, "vpoc": None, "value_area_low": None, "value_area_high": None}
    if ticks.empty:
        return empty

    # Was `ticks["last"]` unconditionally, which is 0.0 on every tick of a
    # quote-only feed — every price landed in one bin and VPOC came back None.
    price = tick_price(ticks)
    if "volume_real" in ticks.columns and (ticks["volume_real"] > 0).any():
        vol = ticks["volume_real"].replace(0, np.nan).fillna(1.0)
    elif "volume" in ticks.columns and (ticks["volume"] > 0).any():
        vol = ticks["volume"].replace(0, np.nan).fillna(1.0)
    else:
        vol = pd.Series(1.0, index=ticks.index)

    lo, hi = price.min(), price.max()
    if pd.isna(lo) or pd.isna(hi) or hi <= lo:
        return empty

    bins = np.linspace(lo, hi, num_bins + 1)
    bin_idx = np.clip(np.digitize(price.values, bins) - 1, 0, num_bins - 1)
    bin_centers = (bins[:-1] + bins[1:]) / 2.0

    profile = pd.Series(0.0, index=range(num_bins))
    grouped = pd.Series(vol.values, index=bin_idx).groupby(level=0).sum()
    profile.loc[grouped.index] = grouped.values

    total_vol = profile.sum()
    if total_vol <= 0:
        return empty

    vpoc_bin = int(profile.idxmax())
    vpoc_price = float(bin_centers[vpoc_bin])

    # Value area: expand outward from VPOC, adding whichever adjacent bin
    # (above or below the current range) holds more volume, until
    # value_area_pct of total volume is enclosed.
    lo_i, hi_i = vpoc_bin, vpoc_bin
    enclosed = profile.iloc[vpoc_bin]
    target = total_vol * value_area_pct
    while enclosed < target and (lo_i > 0 or hi_i < num_bins - 1):
        vol_below = profile.iloc[lo_i - 1] if lo_i > 0 else -1
        vol_above = profile.iloc[hi_i + 1] if hi_i < num_bins - 1 else -1
        if vol_above >= vol_below:
            hi_i += 1
            enclosed += profile.iloc[hi_i]
        else:
            lo_i -= 1
            enclosed += profile.iloc[lo_i]

    return {
        "profile": {round(float(bin_centers[i]), 5): float(profile.iloc[i]) for i in range(num_bins) if profile.iloc[i] > 0},
        "vpoc": vpoc_price,
        "value_area_low": float(bin_centers[lo_i]),
        "value_area_high": float(bin_centers[hi_i]),
    }


# ─────────────────────────────────────────────────────────────────────────
# MT5 tick fetching (live/backtest data source)
# ─────────────────────────────────────────────────────────────────────────

def _fetch_ticks_sync(symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
    if not mt5:
        return pd.DataFrame()
    try:
        terminal = mt5.terminal_info()
        if not (terminal and terminal.connected):
            logger.warning("[ORDERFLOW] MT5 not connected, cannot fetch ticks.")
            return pd.DataFrame()
        ticks = mt5.copy_ticks_range(symbol, start, end, mt5.COPY_TICKS_ALL)
        if ticks is None or len(ticks) == 0:
            return pd.DataFrame()
        df = pd.DataFrame(ticks)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        return df
    except Exception as e:
        logger.error(f"[ORDERFLOW] {symbol}: tick fetch failed: {e}")
        return pd.DataFrame()


async def fetch_ticks(symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
    """Async wrapper around mt5.copy_ticks_range — same executor pattern as mt5/data_fetcher.py."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _fetch_ticks_sync, symbol, start, end)


# ─────────────────────────────────────────────────────────────────────────
# [10.5] Snapshot composer — the consumer the primitives above were waiting for
# ─────────────────────────────────────────────────────────────────────────

async def compute_orderflow_snapshot(
    symbol: str,
    minutes: int = 60,
    timeframe: str = "M5",
    bubbles: bool = True,
    max_bubbles: int = 400,
) -> dict:
    """
    One call that turns raw MT5 ticks into everything the Fundamentals order-flow
    panel and the replay chart's bubble overlay need.

    Task 10.5 was left open with the note that these functions were "ready to be
    called once that wiring is undertaken" — this is that wiring. It is a
    read-only composition: no primitive's behaviour changes, they just finally
    have a caller.

    `bubbles` returns per-price-level aggregated signed volume, which is what
    the chart draws as circles. Aggregated by price bucket rather than emitted
    per tick on purpose: a busy hour is hundreds of thousands of ticks and no
    renderer survives that, nor would you learn anything from it.

    Every number here inherits the module's proxy caveat — MT5 CFD ticks have no
    aggressor flag, so tick signs are inferred from price relative to bid/ask.
    """
    from datetime import timedelta

    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=minutes)

    ticks = await fetch_ticks(symbol, start, end)
    if ticks is None or ticks.empty:
        return {
            "symbol": symbol, "minutes": minutes, "ticks": 0,
            "error": "No ticks returned — the symbol may be closed, or not "
                     "subscribed in the MT5 terminal.",
        }

    method = classification_method(ticks)
    _has_size = any(
        c in ticks.columns and (ticks[c] > 0).any() for c in ("volume_real", "volume")
    )
    signed = classify_ticks(ticks)
    cvd = signed.cumsum()
    total_signed = float(signed.sum())
    buy_vol = float(signed[signed > 0].sum())
    sell_vol = float(-signed[signed < 0].sum())
    both = buy_vol + sell_vol

    snapshot: dict = {
        "symbol": symbol,
        "minutes": minutes,
        "ticks": int(len(ticks)),
        "cvd": round(float(cvd.iloc[-1]), 4) if len(cvd) else 0.0,
        "delta": round(total_signed, 4),
        "buy_volume": round(buy_vol, 4),
        "sell_volume": round(sell_vol, 4),
        # Positive = buyer-initiated flow dominated the window.
        "imbalance": round((buy_vol - sell_vol) / both, 4) if both else 0.0,
        "source": "mt5_ticks_inferred",
        # Which rule classified these ticks, and whether "volume" is real size
        # or a tick count. Both change how much weight the numbers deserve, so
        # they travel with the data rather than living in a docstring.
        "classification": method,
        "volume_is_tick_count": not _has_size,
        "caveat": (
            "Quote-only feed: no traded prices and no traded size. Direction is "
            "inferred from mid-price movement and each tick counts as 1. A "
            "weaker proxy than trade-based classification."
            if method == "quote_rule" else
            "Traded prices classified against bid/ask (Lee-Ready). No aggressor "
            "flag is published, so direction is inferred, not read."
        ),
    }

    # CVD series for the panel's line chart, downsampled to something a chart
    # can actually draw.
    try:
        step = max(1, len(cvd) // 500)
        series = cvd.iloc[::step]
        times = ticks["time"].iloc[::step] if "time" in ticks.columns else series.index
        snapshot["cvd_series"] = [
            {"time": int(t) if isinstance(t, (int, float)) else int(pd.Timestamp(t).timestamp()),
             "value": round(float(v), 4)}
            for t, v in zip(times, series)
        ]
    except Exception as e:
        logger.debug(f"[orderflow] cvd_series build failed for {symbol}: {e}")

    # Volume profile / VPOC / value area.
    try:
        snapshot["volume_profile"] = compute_volume_profile(ticks)
    except Exception as e:
        logger.debug(f"[orderflow] volume profile failed for {symbol}: {e}")

    # Bubbles: signed volume aggregated per price bucket. Radius is scaled at
    # render time from `abs_volume`; sign drives colour.
    if bubbles:
        try:
            price = tick_price(ticks)
            lo, hi = float(price.min()), float(price.max())
            if hi > lo:
                bins = np.linspace(lo, hi, min(max_bubbles, 200) + 1)
                idx = np.clip(np.digitize(price.values, bins) - 1, 0, len(bins) - 2)
                agg: dict[int, float] = {}
                for b, v in zip(idx, signed.values):
                    agg[int(b)] = agg.get(int(b), 0.0) + float(v)
                rows = [
                    {
                        "price": round((bins[b] + bins[b + 1]) / 2, 6),
                        "signed_volume": round(v, 4),
                        "abs_volume": round(abs(v), 4),
                        "side": "buy" if v > 0 else "sell",
                    }
                    for b, v in agg.items() if v != 0
                ]
                # Keep the largest — the small ones are noise at any zoom level.
                rows.sort(key=lambda r: -r["abs_volume"])
                snapshot["bubbles"] = rows[:max_bubbles]
        except Exception as e:
            logger.debug(f"[orderflow] bubble aggregation failed for {symbol}: {e}")

    # Divergence and absorption need OHLC bars, so they are best-effort: a
    # missing candle set costs those two fields, not the whole snapshot.
    try:
        from backend.mt5.data_fetcher import DataFetcher
        candles = await DataFetcher.get_historical_data(symbol, timeframe, count=120)
        if candles is not None and not candles.empty:
            bar_index = (
                pd.to_datetime(candles["time"], unit="s", utc=True)
                if "time" in candles.columns else pd.DatetimeIndex(candles.index)
            )
            per_bar = aggregate_cvd_per_bar(ticks, bar_index)
            snapshot["divergence"] = detect_delta_divergence(candles, per_bar.cumsum())
            snapshot["absorption"] = detect_absorption(candles, per_bar)
            snapshot["delta_per_bar"] = [
                {"time": int(t.timestamp()), "delta": round(float(d), 4)}
                for t, d in zip(bar_index, per_bar)
            ][-120:]
    except Exception as e:
        logger.debug(f"[orderflow] bar-aligned metrics failed for {symbol}: {e}")

    return snapshot
