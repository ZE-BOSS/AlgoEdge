"""QL_DipBuy_v1: IBS / cumulative RSI(2) / 3-down union with an SMA5 exit (quant lab round 3)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from backend.core.config_schema import UserConfigV2
from backend.strategies.registry import get_strategy
from backend.strategies.strategy_roboquant.engine_quant import dipbuy_signals, session_ohlc, wilder_rsi

ET = ZoneInfo("America/New_York")


def _sessions(days_ohlc, start="2026-03-02"):
    """M30 bars 09:30..15:30 ET per (open, high, low, close) day, plus a 16:00 bar."""
    rows = []
    for d, (o, h, lo, c) in zip(pd.bdate_range(start, periods=len(days_ohlc)), days_ohlc):
        t0 = pd.Timestamp(d).replace(hour=9, minute=30, tzinfo=ET)
        path = np.linspace(o, c, 13)
        for k in range(14):
            t = int((t0 + pd.Timedelta(minutes=int(30 * k))).timestamp())
            if k < 13:
                p0 = path[k - 1] if k else o
                p1 = path[k]
                hi = max(p0, p1) + (h - max(o, c)) * (k == 3)
                lw = min(p0, p1) - (min(o, c) - lo) * (k == 6)
                rows.append((t, p0, hi, lw, p1))
            else:
                rows.append((t, c, c, c, c))
    return pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"])


def _upto(df, day_index):
    return df.iloc[: day_index * 14 + 13].reset_index(drop=True)


def _lab_reference(H, L, C, sma=200, ibs_below=0.2, cum_below=35.0, ndown=3):
    """Independent pandas copy of scripts/quant_lab3.py features()."""
    c = pd.Series(C)
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=0.5, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=0.5, adjust=False).mean()
    r2 = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    trend = c > c.rolling(sma).mean()
    ibs = ((c - pd.Series(L)) / (pd.Series(H) - pd.Series(L))).fillna(0.5)
    sig_a = (ibs < ibs_below) & trend
    sig_c = (r2 + r2.shift(1) < cum_below) & trend
    sig_d = ((c.diff() < 0).astype(int).rolling(ndown).sum() == ndown) & trend
    return (sig_a | sig_c | sig_d).to_numpy(), r2.to_numpy()


def test_signals_match_the_lab_on_a_random_walk():
    rng = np.random.default_rng(7)
    C = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.012, 600)))
    H = C * (1 + rng.uniform(0, 0.01, 600))
    L = C * (1 - rng.uniform(0, 0.01, 600))
    p = SimpleNamespace(sma_days=50, ibs_below=0.2, use_cum_rsi=True, cum_rsi_below=35.0,
                        use_down_days=True, down_days=3)
    ref, r2_ref = _lab_reference(H, L, C, sma=50)
    r2 = wilder_rsi(C, 2)
    ok = ~np.isnan(r2_ref)
    assert np.allclose(r2[ok], r2_ref[ok])
    got = dipbuy_signals(H, L, C, p)
    assert got.sum() > 10
    assert (got == ref).all()


def test_signals_can_be_switched_off():
    rng = np.random.default_rng(3)
    C = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.012, 400)))
    H, L = C * 1.004, C * 0.996
    on = SimpleNamespace(sma_days=50, ibs_below=0.2, use_cum_rsi=True, cum_rsi_below=35.0, use_down_days=True, down_days=3)
    off = SimpleNamespace(sma_days=50, ibs_below=0.0, use_cum_rsi=False, cum_rsi_below=35.0, use_down_days=False, down_days=3)
    assert dipbuy_signals(H, L, C, on).sum() > 0
    assert dipbuy_signals(H, L, C, off).sum() == 0


# rising, then three lower closes (strong IBS so only the 3-down rule fires), then a bounce
DAYS = [(100, 101, 99.5, 100.8), (100.8, 102, 100.5, 101.9), (101.9, 103.2, 101.7, 103.0),
        (103.0, 104.4, 102.8, 104.2), (104.2, 105.5, 104.0, 105.3), (105.3, 106.6, 105.1, 106.4),
        (106.4, 106.5, 105.9, 106.3),      # lower close 1
        (106.3, 106.35, 105.8, 106.2),     # lower close 2
        (106.2, 106.25, 105.7, 106.1),     # lower close 3 -> 3-down signal (close still > SMA5? no matter)
        (106.1, 106.3, 106.0, 106.25),     # held: close 106.25 < SMA5 (106.25)? equal -> stay
        (106.25, 108, 106.2, 107.8)]       # close 107.8 > SMA5 -> exit


def _engine(**kw):
    cfg = UserConfigV2()
    cfg.ql_dipbuy.sma_days = 5
    cfg.ql_dipbuy.use_cum_rsi = False
    cfg.ql_dipbuy.ibs_below = 0.0
    for k, v in kw.items():
        setattr(cfg.ql_dipbuy, k, v)
    return get_strategy("QL_DipBuy_v1")(cfg)


def test_entry_on_three_lower_closes_and_sma_exit():
    df = _sessions(DAYS)
    days, H, L, C = session_ohlc(df)
    assert len(days) == len(DAYS) and C[-1] == 107.8
    eng = _engine(sma_days=8)
    sig = asyncio.run(eng.on_bar("US_Tech_100", "M30", _upto(df, 8)))
    assert sig is not None and sig.direction == "BUY"
    assert asyncio.run(eng.on_bar("US_Tech_100", "M30", _upto(df, 7))) is None    # only 2 lower closes
    entry_ts = int(_upto(df, 8).time.iloc[-1]) + 1800
    pos = {"ticket": 5, "entry_time": entry_ts}
    assert eng.on_position_bar("US_Tech_100", "M30", _upto(df, 8), pos) is None   # entry day itself
    assert eng.on_position_bar("US_Tech_100", "M30", _upto(df, 9), pos) is None   # not above SMA5
    act = eng.on_position_bar("US_Tech_100", "M30", _upto(df, 10), pos)
    assert act is not None and act.action == "CLOSE" and act.close_reason == "QL_DIP_SMA_EXIT"


def test_time_exit_after_max_hold_days():
    falling = [(100 - k * 0.5, 100.2 - k * 0.5, 99.3 - k * 0.5, 99.5 - k * 0.5) for k in range(14)]
    df = _sessions(falling)
    eng = _engine(sma_days=1, max_hold_days=3)
    pos = {"ticket": 9, "entry_time": int(_upto(df, 4).time.iloc[-1]) + 1800}
    assert eng.on_position_bar("US_Tech_100", "M30", _upto(df, 6), pos) is None   # held 2
    act = eng.on_position_bar("US_Tech_100", "M30", _upto(df, 7), pos)            # held 3
    assert act is not None and act.close_reason == "QL_DIP_TIME_EXIT"
