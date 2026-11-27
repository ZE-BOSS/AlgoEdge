"""
backend/marketdata/source.py

Candles for strategies, backtests and charts from CME futures (Databento),
translated into the broker's prices. DataFetcher calls `bars()` first for every
symbol mapped in instruments.py; None means "use MT5" (the fallback).

What a caller gets
    the DataFetcher frame (time epoch UTC, open, high, low, close, tick_volume,
    spread, real_volume) plus the order flow of each bar:
        of_vol     contracts traded
        delta      buyer- minus seller-initiated contracts (NaN: no flow data)
        delta_h2   the same over the bar's second half
        big_buy    contracts in trades >= the market's "big" size, buyer-initiated
        big_sell   ... seller-initiated
        vwap       the bar's volume-weighted price
    frame.attrs: source="databento", future, basis (last applied), basis_from

Prices
    broker price = futures price (1/price for 6J, 6C, 6S) + basis. The basis is
    measured from the broker's own bars at the same times: per UTC day the
    median difference of closes, and for the latest day the median of its last
    12 matched bars. Bars keep the futures' shape; only the level moves.
    Distances (stops, targets, ATR) are unchanged, so MT5 sizing is unchanged.

When it falls back to MT5 (returns None)
    * the symbol is not mapped, or MARKETDATA_SOURCE=mt5;
    * a live request (count=...) and the live file is older than LIVE_MAX_AGE_S
      or its newest minute older than LIVE_MAX_LAG_S, or the basis cannot be measured (orders would be placed at
      the wrong level);
    * fewer than 80 % of the requested bars exist.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from backend.marketdata import history, store
from backend.marketdata.instruments import Instrument, mapped
from backend.utils.logger import get_logger

logger = get_logger(__name__)

TF_MIN = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}
LIVE_MAX_AGE_S = 150          # the live file must have been rewritten this recently ...
LIVE_MAX_LAG_S = 600          # ... and hold a minute this recent (quiet FX nights have gaps)
_warned: dict[str, float] = {}


def _warn(key: str, msg: str) -> None:
    now = time.time()
    if now - _warned.get(key, 0) > 600:
        _warned[key] = now
        logger.warning(msg)


def invert(m: pd.DataFrame) -> pd.DataFrame:
    """6J-style quotes -> USDJPY-style: prices 1/x (high and low swap), buyers and sellers swap."""
    out = m.copy()
    with np.errstate(divide="ignore", invalid="ignore"):
        out["open"], out["close"] = 1.0 / m["open"], 1.0 / m["close"]
        out["high"], out["low"] = 1.0 / m["low"], 1.0 / m["high"]
        vw = m["pv"] / m["volume"].replace(0, np.nan)
        out["pv"] = (1.0 / vw) * m["volume"]
    out["buy"], out["sell"] = m["sell"], m["buy"]
    out["big_buy"], out["big_sell"] = m["big_sell"], m["big_buy"]
    return out


def resample(m: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Minutes -> timeframe bars (left-labelled, UTC), with the flow columns."""
    tf = TF_MIN[timeframe]
    if not len(m):
        return pd.DataFrame()
    m = m.copy()
    has_flow = m["buy"].notna()
    m["delta"] = (m["buy"] - m["sell"]).where(has_flow)
    rule = "1D" if tf == 1440 else f"{tf}min"
    start = m.index.floor(rule) if tf < 1440 else m.index.normalize()
    pos = (m.index - start).total_seconds() / 60.0
    # the second half as the research counts it: minutes n//2 .. n-1 of an n-minute bar
    m["delta_h2"] = m["delta"].where(pos >= tf // 2) if tf > 1 else m["delta"]
    m["vw_pv"] = m["pv"].where(has_flow, ((m["high"] + m["low"] + m["close"]) / 3.0) * m["volume"])
    g = m.groupby(start)
    out = pd.DataFrame({
        "open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(), "close": g["close"].last(),
        "of_vol": g["volume"].sum(),
        "delta": g["delta"].sum(min_count=1), "delta_h2": g["delta_h2"].sum(min_count=1),
        "big_buy": g["big_buy"].sum(min_count=1), "big_sell": g["big_sell"].sum(min_count=1),
        "vw_pv": g["vw_pv"].sum(min_count=1),
    })
    out = out.dropna(subset=["open", "close"])
    out["vwap"] = out["vw_pv"] / out["of_vol"].replace(0, np.nan)
    out["vwap"] = out["vwap"].fillna((out["high"] + out["low"] + out["close"]) / 3.0)
    out = out.drop(columns="vw_pv")
    out["tick_volume"] = out["of_vol"].round().astype("int64")
    out["real_volume"] = out["tick_volume"]
    out["time"] = (out.index.astype("int64") // 1_000_000_000).astype("int64")
    return out


HVN_SLOTS = 6
PRICE_COLS = ("open", "high", "low", "close", "vwap") + tuple(f"pd_hvn{i}" for i in range(HVN_SLOTS))


def prior_day_nodes(m: pd.DataFrame) -> dict[pd.Timestamp, list[float]]:
    """High-volume nodes of each UTC day's minute volume profile, keyed by the NEXT
    calendar day (the day they are traded on): minute VWAP x volume binned at
    0.04% of price, smoothed over 5 bins, local peaks at least half the top one,
    the first six by price. Days with under 60 traded minutes have none."""
    vol = m["volume"].astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        px = (m["pv"] / vol.replace(0, np.nan)).where(m["pv"].notna(),
                                                     (m["high"] + m["low"] + m["close"]) / 3.0)
    ok = (vol > 0) & px.notna()
    out: dict[pd.Timestamp, list[float]] = {}
    for day, idx in pd.Series(np.flatnonzero(ok.values)).groupby(m.index[ok.values].normalize()):
        rows = idx.values
        if len(rows) < 60:
            continue
        p, v = px.values[rows], vol.values[rows]
        step = max(float(np.median(p)) * 0.0004, 1e-9)
        prof = pd.Series(v).groupby(np.round(p / step) * step).sum().sort_index()
        sm = prof.rolling(5, center=True, min_periods=1).mean().to_numpy()
        peaks = [float(prof.index[i]) for i in range(1, len(sm) - 1)
                 if sm[i] >= sm[i - 1] and sm[i] >= sm[i + 1] and sm[i] >= 0.5 * sm.max()]
        out[pd.Timestamp(day) + pd.Timedelta(days=1)] = peaks[:HVN_SLOTS]
    return out


def attach_nodes(bars_df: pd.DataFrame, m: pd.DataFrame) -> pd.DataFrame:
    """Columns pd_hvn0..5 on timeframe bars: the nodes of the previous UTC day (NaN = none)."""
    nodes = prior_day_nodes(m)
    cols = [f"pd_hvn{i}" for i in range(HVN_SLOTS)]
    table = pd.DataFrame([(v + [np.nan] * HVN_SLOTS)[:HVN_SLOTS] for v in nodes.values()],
                         index=pd.DatetimeIndex(list(nodes.keys())), columns=cols, dtype=float)
    per_bar = table.reindex(bars_df.index.normalize())
    for k in cols:
        bars_df[k] = per_bar[k].to_numpy() if len(table) else np.nan
    return bars_df


def with_forming_bar(fut: pd.DataFrame, now: datetime, step: int) -> pd.DataFrame:
    """Live frames end with the bar still forming, as MT5's do - every caller drops
    the last row as 'forming'. Minute bars only exist once something trades, so in
    the first seconds of a bar the newest row is the bar that just CLOSED, and
    dropping it would hide that bar until the first trade of the next one. A flat,
    empty row for the current bar is added then (only right after the previous
    bar, never across a gap such as a session break)."""
    if not len(fut):
        return fut
    t = pd.Timestamp(now)
    t = (t.tz_convert("UTC") if t.tzinfo else t.tz_localize("UTC")).tz_localize(None)
    cur = t.normalize() if step >= 1440 else t.floor(f"{step}min")
    last = fut.index[-1]
    if last >= cur or last != cur - pd.Timedelta(minutes=step):
        return fut
    px = float(fut["close"].iloc[-1])
    row = {k: 0.0 for k in fut.columns}
    row.update(open=px, high=px, low=px, close=px, vwap=px)
    row["time"] = int(cur.timestamp())
    add = pd.DataFrame([row], index=pd.DatetimeIndex([cur]))
    for k in ("tick_volume", "real_volume", "time"):
        if k in add.columns:
            add[k] = add[k].astype("int64")
    return pd.concat([fut, add[fut.columns]])


def basis_series(fut: pd.DataFrame, mt5: pd.DataFrame | None) -> tuple[pd.Series | None, str]:
    """Per bar: broker price - futures price, from matched closes."""
    if mt5 is None or not len(mt5) or not len(fut):
        return None, "none"
    a = fut.set_index("time")["close"]
    b = mt5.set_index("time")["close"]
    both = pd.concat([a.rename("f"), b.rename("m")], axis=1, join="inner").dropna()
    if len(both) < 3:
        return None, "none"
    diff = both["m"] - both["f"]
    day = pd.to_datetime(diff.index, unit="s").normalize()
    per_day = diff.groupby(day).median()
    last_day = day.max()
    recent = diff[day == last_day].iloc[-12:]
    per_day.loc[last_day] = float(recent.median())
    bar_day = pd.to_datetime(fut["time"], unit="s").dt.normalize()
    s = bar_day.map(per_day)
    s = s.ffill().bfill()
    s.index = fut.index
    return s, "matched broker bars"


async def _mt5_bars(symbol: str, timeframe: str, count=None, start=None, end=None):
    from backend.mt5.data_fetcher import DataFetcher
    try:
        if count is not None:
            return await DataFetcher.get_historical_data(symbol, timeframe, count, _mt5_only=True)
        return await DataFetcher.get_data_range(symbol, timeframe, start, end, _mt5_only=True)
    except Exception:  # noqa: BLE001
        return None


def _apply_basis(df: pd.DataFrame, b: pd.Series) -> pd.DataFrame:
    out = df.copy()
    for k in PRICE_COLS:
        if k in out.columns:
            out[k] = out[k] + b.values
    return out


async def bars(symbol: str, timeframe: str, count: int | None = None,
               start: datetime | None = None, end: datetime | None = None) -> pd.DataFrame | None:
    inst = mapped(symbol)
    tf = str(timeframe).upper()
    if inst is None or tf not in TF_MIN:
        return None
    try:
        return await _bars(inst, symbol, tf, count, start, end)
    except Exception as e:  # noqa: BLE001
        _warn(f"err:{symbol}:{tf}", f"[MARKETDATA] {symbol} {tf}: Databento bars failed, using MT5 "
                                    f"({type(e).__name__}: {e})")
        return None


async def _bars(inst: Instrument, symbol: str, tf: str, count, start, end) -> pd.DataFrame | None:
    live = count is not None
    step = TF_MIN[tf]
    now = datetime.now(timezone.utc)
    if live:
        age, lag = store.live_age(inst.future), store.live_lag(inst.future)
        if age is None or age > LIVE_MAX_AGE_S or lag is None or lag > LIVE_MAX_LAG_S:
            _warn(f"stale:{inst.future}", f"[MARKETDATA] {inst.future}: no fresh streamed minutes "
                                          f"(file {'none' if age is None else f'{age:.0f}s old'}, newest minute "
                                          f"{'none' if lag is None else f'{lag:.0f}s ago'}); {symbol} uses MT5 bars")
            return None
        e = now + timedelta(minutes=1)
        span = timedelta(minutes=int(count) * step * 1.6) + timedelta(days=4)
        s = e - span
    else:
        s, e = pd.Timestamp(start).to_pydatetime(), pd.Timestamp(end).to_pydatetime()
        if s.tzinfo is None:
            s = s.replace(tzinfo=timezone.utc)
        if e.tzinfo is None:
            e = e.replace(tzinfo=timezone.utc)
        e = e + timedelta(minutes=step)               # the last requested bar's minutes
    s = s - timedelta(days=1)                         # the prior day's minutes: volume nodes
    loop = asyncio.get_running_loop()
    if history.key():
        await loop.run_in_executor(None, history.ensure_ohlcv, inst, s, e)
    m = await loop.run_in_executor(None, store.read_range, inst.future, s, e)
    if live and len(m) and count:
        # not enough history yet (a new market): reach further back once
        need = int(count) * step
        if len(m) < need * 0.8 and history.key():
            s2 = s - timedelta(minutes=need * 2)
            await loop.run_in_executor(None, history.ensure_ohlcv, inst, s2, e)
            m = await loop.run_in_executor(None, store.read_range, inst.future, s2, e)
    if not len(m):
        return None
    if inst.inverse:
        m = invert(m)
    fut = resample(m, tf)
    if live:
        fut = with_forming_bar(fut, now, step)
    fut = attach_nodes(fut, m)
    if live:
        # the bar still forming is not a closed bar - MT5's last bar is the forming
        # one too, so keep it (callers drop it as they do for MT5)
        fut = fut.iloc[-int(count):]
        if len(fut) < int(count) * 0.8:
            return None
    else:
        lo, hi = int(pd.Timestamp(start).timestamp()), int(pd.Timestamp(end).timestamp())
        fut = fut[(fut["time"] >= lo) & (fut["time"] <= hi)]
        if not len(fut):
            return None
    fut = fut.reset_index(drop=True)
    if live:
        mt = await _mt5_bars(symbol, tf, count=min(int(count), 500))
    else:
        mt = await _mt5_bars(symbol, tf, start=start, end=end)
    b, how = basis_series(fut, mt)
    if b is None:
        if live:
            _warn(f"basis:{symbol}", f"[MARKETDATA] {symbol}: cannot measure the futures-broker basis; using MT5 bars")
            return None
        b = pd.Series(0.0, index=fut.index)
        how = "none (futures prices)"
    out = _apply_basis(fut, b)
    if mt is not None and len(mt) and "spread" in mt.columns:
        sp = mt.set_index("time")["spread"]
        out["spread"] = out["time"].map(sp).ffill().bfill().fillna(float(sp.median()) if len(sp) else 0).astype("int64")
    else:
        out["spread"] = 0
    cols = ["time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume",
            "of_vol", "delta", "delta_h2", "big_buy", "big_sell", "vwap"] + [f"pd_hvn{i}" for i in range(HVN_SLOTS)]
    out = out[cols]
    out.attrs.update(source="databento", future=inst.future, basis=float(b.iloc[-1]), basis_from=how)
    return out
