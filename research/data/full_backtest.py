"""Own-engine backtest: every strategy x every asset x RR 2/3/4/5, both brokers.

Independent of the app's database and UI. Drives the real strategy classes with
correctly-sliced multi-timeframe data (the way backtest.py does), then simulates
execution on the real bar path and reports the full metric set:

  P&L in dollars, return %, Sharpe, Sortino, max drawdown, max DAILY drawdown,
  profit factor, win rate, expectancy in R, and signal accounting
  (generated / accepted / rejected, with reasons).

Capital $10,000, risk 1% per trade, costs charged from the live spread.
"""
from __future__ import annotations
import asyncio, os, sys, json, math, warnings
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from backend.strategies.registry import get_strategy            # noqa: E402
from backend.core.config_schema import UserConfigV2, InstrumentSettings  # noqa: E402

TF_MIN = {"M5": 5, "M15": 15, "H1": 60, "H4": 240, "D1": 1440}
BALANCE = 10_000.0
RISK_PCT = 1.0
RRS = (2.0, 3.0, 4.0, 5.0)
MAX_DAILY_TRADES = 7
MIN_BARS_BETWEEN = 7
HORIZON = 2000         # primary-TF bars a trade may stay open (~7d of M5)


# ───────────────────────── data ─────────────────────────
def load_tf(cache: Path, sym: str, tf: str):
    f = cache / f"{sym.replace(' ', '_')}__{tf}.npz"
    if not f.exists():
        return None, {}
    z = np.load(f)
    df = pd.DataFrame({k: z[k].astype(float)
                       for k in ("open", "high", "low", "close", "volume")})
    df.index = pd.to_datetime(z["time"].astype(np.int64), unit="s", utc=True)
    meta = {"point": float(z["point"]),
            "spread_px": float(z["spread_px"]) if "spread_px" in z
            else float(z["spread"]) * float(z["point"])}
    return df, meta


# ───────────────────────── metrics ─────────────────────────
def metrics(trades, balance0=BALANCE):
    """trades: list of dicts with r, pnl, entry_time."""
    if not trades:
        return None
    r = np.array([t["r"] for t in trades], float)
    pnl = np.array([t["pnl"] for t in trades], float)
    eq = balance0 + np.cumsum(pnl)
    peak = np.maximum.accumulate(np.concatenate(([balance0], eq)))[1:]
    dd_pct = float(((peak - eq) / peak).max() * 100)
    dd_abs = float((peak - eq).max())

    # daily aggregation for daily drawdown + risk ratios
    days = pd.Series(pnl, index=pd.to_datetime([t["entry_time"] for t in trades]))
    daily = days.resample("1D").sum()
    daily = daily[daily != 0]
    max_daily_loss = float(daily.min()) if len(daily) else 0.0
    max_daily_dd_pct = abs(max_daily_loss) / balance0 * 100

    if len(daily) > 1 and daily.std(ddof=1) > 0:
        sharpe = float(daily.mean() / daily.std(ddof=1) * math.sqrt(252))
    else:
        sharpe = 0.0
    # Sortino needs a meaningful number of losing days. With one or two, the
    # downside deviation collapses and the ratio explodes (a 129 was observed on
    # 10 trades). Require >=5 down days, and report NaN rather than a fantasy.
    downside = daily[daily < 0]
    if len(downside) >= 5 and downside.std(ddof=1) > 0:
        sortino = float(daily.mean() / downside.std(ddof=1) * math.sqrt(252))
    else:
        sortino = float("nan")
    if len(daily) < 5:
        sharpe = float("nan")

    wins = r[r > 0]
    gl = -r[r <= 0].sum()
    return {
        "trades": len(r),
        "pnl": float(pnl.sum()),
        "ret_pct": float(pnl.sum() / balance0 * 100),
        "final": float(balance0 + pnl.sum()),
        "wr": float((r > 0).mean()),
        "pf": float(wins.sum() / gl) if gl > 0 else float("inf"),
        "exp_r": float(r.mean()),
        "maxdd_pct": dd_pct,
        "maxdd_abs": dd_abs,
        "max_daily_dd_pct": max_daily_dd_pct,
        "max_daily_loss": max_daily_loss,
        "sharpe": sharpe,
        "sortino": sortino,
        "trading_days": int(len(daily)),
    }


# ───────────────────────── signal generation ─────────────────────────
# WINDOWED BY DATE, NOT BAR COUNT.
#
# The first run capped every asset at the last 8,000 M5 bars. Because
# instruments trade different hours, that spanned 28 days on a 24/7 synthetic
# and 71 days on a short-session index — so cross-asset P&L was never
# like-for-like. Plan rule 0.1 warns about exactly this. A fixed calendar window
# gives every asset the same exposure.
WINDOW_FROM = pd.Timestamp("2026-01-01", tz="UTC")
WINDOW_TO = pd.Timestamp("2026-09-01", tz="UTC")


def _tf_end_indices(primary_index, tf_index, tf_minutes):
    """For each primary bar, the count of TF bars fully closed by then.

    Precomputed once with a single vectorised searchsorted. The previous version
    called searchsorted per bar per timeframe inside the hot loop, which is what
    made the run take an hour.
    """
    cutoffs = (primary_index - pd.Timedelta(minutes=tf_minutes)).values
    return np.searchsorted(tf_index.values, cutoffs, side="right")


async def gen_signals(sid, sym, cache: Path, window=(WINDOW_FROM, WINDOW_TO)):
    cfg = UserConfigV2()
    cfg.instrument_settings = [InstrumentSettings(symbol=sym, strategy_id=sid)]
    eng = get_strategy(sid)(cfg)
    eng.is_backtesting = True
    eng.gates.enabled = True

    want = [t for t in eng.get_required_timeframes() if t in TF_MIN]
    if not want:
        return None, None, None, "no usable timeframe"
    want = sorted(set(want), key=lambda t: TF_MIN[t])
    data, meta = {}, {}
    for t in want:
        d, m = load_tf(cache, sym, t)
        if d is None or len(d) < 300:
            return None, None, None, f"missing {t} data"
        data[t], meta = d, m

    primary = want[0]
    full = data[primary]
    lo, hi = window
    pdf = full[(full.index >= lo) & (full.index < hi)]
    if len(pdf) < 500:
        return None, None, None, "window too short"

    # precompute, per timeframe, the closed-bar cursor for every primary bar
    ends = {t: _tf_end_indices(pdf.index, data[t].index, TF_MIN[t]) for t in want}

    seen = {t: -1 for t in want}
    sigs = []
    for i in range(300, len(pdf)):
        for t in want:
            end = int(ends[t][i])
            if end < 60 or end == seen[t]:
                continue          # this timeframe has not closed a new bar
            seen[t] = end
            sl = data[t].iloc[max(0, end - 400):end]
            try:
                s = await eng.on_bar(sym, t, sl)
            except Exception:
                s = None
            if s:
                sigs.append({"i": i, "time": pdf.index[i],
                             "direction": s.direction,
                             "entry": float(s.entry_price),
                             "stop": float(s.stop_loss)})
    eng.gates.finish()
    return pdf, sigs, eng.gates, meta


# ───────────────────────── execution ─────────────────────────
def simulate(pdf, sigs, rr, spread_px, balance0=BALANCE):
    h = pdf["high"].values
    l = pdf["low"].values
    c = pdf["close"].values
    idx = pdf.index
    n = len(c)
    bal = balance0
    trades, rejected = [], {"too_soon": 0, "daily_cap": 0, "bad_stop": 0}
    last_i, day_count, cur_day = -10**9, 0, None

    for s in sigs:
        i = s["i"]
        if i >= n - 2:
            continue
        day = idx[i].date()
        if day != cur_day:
            cur_day, day_count = day, 0
        if i - last_i < MIN_BARS_BETWEEN:
            rejected["too_soon"] += 1
            continue
        if day_count >= MAX_DAILY_TRADES:
            rejected["daily_cap"] += 1
            continue
        entry, stop = s["entry"], s["stop"]
        risk_px = abs(entry - stop)
        if risk_px <= 0:
            rejected["bad_stop"] += 1
            continue
        buy = str(s["direction"]).upper() in ("BUY", "LONG")
        tp = entry + rr * risk_px if buy else entry - rr * risk_px
        last_i, day_count = i, day_count + 1

        r = None
        end = min(i + HORIZON, n - 1)
        for j in range(i + 1, end + 1):
            if buy:
                if l[j] <= stop:
                    r = -1.0; break
                if h[j] >= tp:
                    r = rr; break
            else:
                if h[j] >= stop:
                    r = -1.0; break
                if l[j] <= tp:
                    r = rr; break
        if r is None:
            r = ((c[end] - entry) if buy else (entry - c[end])) / risk_px
            r = float(np.clip(r, -1.0, rr))
        r -= spread_px / risk_px                     # round-trip cost in R
        risk_dollars = bal * RISK_PCT / 100.0
        pnl = risk_dollars * r
        bal += pnl
        trades.append({"r": r, "pnl": pnl, "entry_time": idx[i]})
    return trades, rejected


# ───────────────────────── walk-forward ─────────────────────────
# [18.1] Rule 0.5-6 requires any rule chosen by looking at results to be
# re-tested on a period it was NOT chosen on. Report 16's conclusions are all
# in-sample, and VWAP already collapsed once when the window widened — so the
# split is the check that stops report 16 becoming the next report 15.
SPLIT = pd.Timestamp("2026-05-01", tz="UTC")


def split_metrics(trades, balance0=BALANCE):
    """Metrics for the full window, the in-sample half, and the out-of-sample half.

    Each half is measured on its OWN starting balance so the OOS figure is not
    inflated by in-sample compounding.
    """
    if not trades:
        return None
    is_t = [t for t in trades if t["entry_time"] < SPLIT]
    oos_t = [t for t in trades if t["entry_time"] >= SPLIT]
    return {
        "full": metrics(trades, balance0),
        "is": metrics(is_t, balance0) if is_t else None,
        "oos": metrics(oos_t, balance0) if oos_t else None,
    }
