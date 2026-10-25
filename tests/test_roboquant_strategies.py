"""
The five roboquant.dev templates (backend/strategies/strategy_roboquant) on
hand-built bars where the right answer is known - each rule from the template
source pinned, so a later edit cannot quietly change what was replicated.
"""

from __future__ import annotations

import asyncio
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from backend.core.config_schema import UserConfigV2
from backend.strategies.registry import get_strategy

ET = ZoneInfo("America/New_York")


def _frame(et_times, opens, highs, lows, closes, vols=None):
    idx = pd.DatetimeIndex([pd.Timestamp(t, tz=ET).tz_convert("UTC").tz_localize(None) for t in et_times])
    n = len(idx)
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes,
                         "tick_volume": vols if vols is not None else np.full(n, 100.0)}, index=idx)


def _flat_session(day, start, n, minutes, price=100.0, rng_pts=0.5):
    """n flat bars from `start` (ET) every `minutes`, each with a small range."""
    t0 = pd.Timestamp(f"{day} {start}")
    times = [t0 + pd.Timedelta(minutes=minutes * k) for k in range(n)]
    p = np.full(n, price)
    return times, p.copy(), p + rng_pts, p - rng_pts, p.copy()


def _on_bar(sid, frame, tf):
    eng = get_strategy(sid)(UserConfigV2())
    return asyncio.run(eng.on_bar("NDX100", tf, frame)), eng


# -- NY ORB ------------------------------------------------------------------


def _ny_orb_frame(break_at=None, vol_spike=3.0):
    # 60 quiet 15m bars before the open, then the 09:30 range bar, then the day.
    # ATR must sit in the template's 0.10-0.60%-of-price band: 0.15-pt bars on 100.
    t, o, h, l, c = _flat_session("2026-03-03", "21:00", 50 + 30, 15, rng_pts=0.15)
    vols = np.full(len(t), 100.0)
    k930 = [i for i, x in enumerate(t) if x.hour == 9 and x.minute == 30][0]
    h[k930], l[k930] = 101.0, 99.0
    if break_at is not None:
        k = k930 + break_at
        c[k], h[k] = 101.6, 101.7
        vols[k] = 100.0 * vol_spike
    return _frame(t, o, h, l, c, vols), k930


def test_ny_orb_breaks_out_with_its_filters_and_targets():
    f, k930 = _ny_orb_frame(break_at=1)
    sig, _ = _on_bar("RQ_NYORB_v1", f.iloc[:k930 + 2], "M15")
    assert sig is not None and sig.direction == "BUY"
    risk = sig.entry_price - sig.stop_loss
    assert abs((sig.take_profit - sig.entry_price) / risk - 0.4) < 1e-9


def test_ny_orb_blocks_weak_volume_and_trades_once_a_day():
    f, k930 = _ny_orb_frame(break_at=1, vol_spike=0.5)
    sig, _ = _on_bar("RQ_NYORB_v1", f.iloc[:k930 + 2], "M15")
    assert sig is None, "RVOL below 1.0x must block"
    f, k930 = _ny_orb_frame(break_at=1)
    f.iloc[k930 + 2, f.columns.get_loc("close")] = 101.8
    sig, _ = _on_bar("RQ_NYORB_v1", f.iloc[:k930 + 3], "M15")
    assert sig is None, "the day's trade already fired on the previous bar"


# -- Crazy ORB ---------------------------------------------------------------


def _crazy_frame():
    t, o, h, l, c = _flat_session("2026-03-03", "21:00", 49 + 30, 15)
    k915 = [i for i, x in enumerate(t) if x.hour == 9 and x.minute == 15][0]
    h[k915], l[k915] = 101.0, 99.0
    return t, o, h, l, c, k915


def test_crazy_orb_takes_each_side_once_and_not_while_a_trade_is_open():
    t, o, h, l, c, k = _crazy_frame()
    c[k + 1], h[k + 1] = 101.5, 101.6                 # 09:30 close above the range
    f = _frame(t, o, h, l, c)
    sig, _ = _on_bar("RQ_CrazyORB_v1", f.iloc[:k + 2], "M15")
    assert sig is not None and sig.direction == "BUY"
    rr = (sig.take_profit - sig.entry_price) / (sig.entry_price - sig.stop_loss)
    assert abs(rr - 0.35) < 1e-9
    # A second close above: long side already used -> nothing.
    c[k + 2], h[k + 2] = 101.6, 101.65
    sig, _ = _on_bar("RQ_CrazyORB_v1", _frame(t, o, h, l, c).iloc[:k + 3], "M15")
    assert sig is None


def test_crazy_orb_short_after_the_long_has_closed():
    t, o, h, l, c, k = _crazy_frame()
    c[k + 1], h[k + 1] = 101.5, 101.6                 # long signal
    h[k + 2] = 140.0                                  # its target is hit on the next bar
    c[k + 3], l[k + 3] = 98.5, 98.4                   # then a close below the range
    sig, _ = _on_bar("RQ_CrazyORB_v1", _frame(t, o, h, l, c).iloc[:k + 4], "M15")
    assert sig is not None and sig.direction == "SELL"


# -- NQ Precision ------------------------------------------------------------


def test_nq_precision_decides_only_at_0959_and_only_outside_the_range():
    t, o, h, l, c = _flat_session("2026-03-04", "09:00", 61, 1)
    k959 = [i for i, x in enumerate(t) if x.hour == 9 and x.minute == 59][0]
    # Volume flat -> rvol 1.0, inside its 0.5-1.5 band; ATR regime ~1.0 needs a
    # slightly livelier last stretch.
    for i in range(k959 - 14, k959):
        h[i], l[i] = 100.6, 99.4
    c[k959] = 100.0
    f = _frame(t, o, h, l, c)
    sig, _ = _on_bar("RQ_NQPrecision_v1", f.iloc[:k959 + 1], "M1")
    assert sig is None, "inside the 09:30-09:45 range is no trade"
    c[k959] = 101.0
    sig, _ = _on_bar("RQ_NQPrecision_v1", _frame(t, o, h, l, c).iloc[:k959 + 1], "M1")
    assert sig is not None and sig.direction == "BUY"
    assert abs((sig.take_profit - sig.entry_price) / (sig.entry_price - sig.stop_loss) - 0.3 / 2.2) < 1e-9
    sig, _ = _on_bar("RQ_NQPrecision_v1", _frame(t, o, h, l, c).iloc[:k959], "M1")
    assert sig is None, "only the 09:59 bar decides"


def test_nq_precision_time_close_after_eight_minutes():
    eng = get_strategy("RQ_NQPrecision_v1")(UserConfigV2())
    t, o, h, l, c = _flat_session("2026-03-04", "10:00", 12, 1)
    f = _frame(t, o, h, l, c)
    pos = {"ticket": 1, "entry_time": int(f.index[0].timestamp()), "direction": "BUY"}
    assert eng.on_position_bar("NDX100", "M1", f.iloc[:8], pos) is None
    assert eng.on_position_bar("NDX100", "M1", f.iloc[:9], pos).action == "CLOSE"


# -- T-Mean Reversion ---------------------------------------------------------


def _week_utc(friday_red: bool):
    idx = pd.date_range("2026-03-02 00:00", "2026-03-13 23:00", freq="1h")   # Mon .. next Fri (UTC)
    p = np.full(len(idx), 100.0)
    fri = (idx.normalize() == pd.Timestamp("2026-03-06"))
    p[fri] = np.linspace(100, 99 if friday_red else 101, fri.sum())
    f = pd.DataFrame({"open": p, "high": p + 0.2, "low": p - 0.2, "close": p,
                      "tick_volume": 100.0}, index=idx)
    f.loc[fri, "open"] = np.r_[100.0, p[fri][:-1]]
    return f


def test_t_mean_reversion_buys_friday_1800_utc_only_after_a_red_friday():
    for red, expect in ((True, "BUY"), (False, None)):
        f = _week_utc(red)
        k = f.index.get_loc(pd.Timestamp("2026-03-13 18:00"))
        sig, _ = _on_bar("RQ_TMeanReversion_v1", f.iloc[:k + 1], "H1")
        assert (sig.direction if sig else None) == expect
    eng = get_strategy("RQ_TMeanReversion_v1")(UserConfigV2())
    idx = pd.date_range("2026-03-16 00:00", "2026-03-18 12:00", freq="1h")
    g = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0}, index=idx)
    pos = {"ticket": 1, "direction": "BUY"}
    k10 = idx.get_loc(pd.Timestamp("2026-03-18 10:00"))     # a Wednesday
    assert eng.on_position_bar("NDX100", "H1", g.iloc[:k10], pos) is None
    assert eng.on_position_bar("NDX100", "H1", g.iloc[:k10 + 1], pos).action == "CLOSE"


# -- Charles Monger -----------------------------------------------------------


def test_charles_monger_enters_on_the_green_flip_and_exits_on_red():
    n = 700
    idx = pd.date_range("2026-01-01", periods=n, freq="1h")
    close = np.r_[np.full(500, 100.0), np.linspace(100, 90, 100), np.linspace(90, 120, 100)]
    f = pd.DataFrame({"open": close, "high": close + 0.1, "low": close - 0.1, "close": close,
                      "tick_volume": 100.0}, index=idx)
    eng = get_strategy("RQ_CharlesMonger_v1")(UserConfigV2())
    r = eng._regimes(f)
    first_green = int(np.where(r[600:] == 1)[0][0]) + 600
    sig = asyncio.run(eng.on_bar("SPX500", "H1", f.iloc[:first_green + 1]))
    assert sig is not None and sig.direction == "BUY"
    assert abs(sig.stop_loss - sig.entry_price * 0.99) < 1e-9
    assert asyncio.run(eng.on_bar("SPX500", "H1", f.iloc[:first_green + 2])) is None, "no flip, no entry"
    red = int(np.where(r[500:600] == -1)[0][0]) + 500
    pos = {"ticket": 1, "direction": "BUY"}
    assert eng.on_position_bar("SPX500", "H1", f.iloc[:red + 1], pos).action == "CLOSE"


def test_t_mean_reversion_exit_survives_a_missed_bar():
    """Live, the Wed 10:00 bar can be missed (bot offline): the exit must still
    happen on the next bar, not a week later."""
    eng = get_strategy("RQ_TMeanReversion_v1")(UserConfigV2())
    idx = pd.date_range("2026-03-13 19:00", "2026-03-18 14:00", freq="1h")
    idx = idx[idx != pd.Timestamp("2026-03-18 10:00")]
    g = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0}, index=idx)
    pos = {"ticket": 1, "direction": "BUY", "entry_time": int(pd.Timestamp("2026-03-13 19:00").timestamp())}
    k9 = idx.get_loc(pd.Timestamp("2026-03-18 09:00"))
    assert eng.on_position_bar("NDX100", "H1", g.iloc[:k9 + 1], pos) is None
    assert eng.on_position_bar("NDX100", "H1", g.iloc[:k9 + 2], pos).action == "CLOSE"   # 11:00
