"""The six classic families must trade exactly what the strategy search measured:
same bars, same direction, same stop — on the window size the app hands them —
and their exits must be the research exits."""

import asyncio

import numpy as np
import pandas as pd
import pytest

from backend.analytics import strategy_search as ss
from backend.core.config_schema import UserConfigV2
from backend.strategies.registry import get_strategy
from backend.strategies.strategy_classic import params as P
from backend.strategies.strategy_classic.engine import CLASSIC_STRATEGIES
from backend.strategies.windows import window_bars


def _bars(tf="H1", n=2600, seed=5):
    rng = np.random.default_rng(seed)
    freq = {"H1": "1h", "D1": "1D"}[tf]
    idx = pd.date_range("2023-01-02", periods=n, freq=freq, tz="UTC")
    regime = np.repeat(rng.normal(0, 0.04, n // 200 + 1), 200)[:n]
    close = 100 + np.cumsum(regime + rng.normal(0, 0.35, n))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + np.abs(rng.normal(0.15, 0.1, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0.15, 0.1, n))
    vol = rng.integers(100, 400, n).astype(float) * np.where(rng.random(n) < 0.08, 3.0, 1.0)
    t = (idx.asi8 // 10**9).astype(np.int64)
    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "tick_volume": vol},
                      index=pd.to_datetime(t, unit="s"))
    return df


CASES = [
    ("Donchian_v1", "donchian", P.DonchianParams(channel_bars=20, stop_atr=2.0, exit_mode="trail"),
     ss._donchian, {"n": 20, "k": 2.0, "exit": "trail", "side": "both"}),
    ("Donchian_v1", "donchian", P.DonchianParams(channel_bars=55, stop_atr=3.0, exit_mode="channel", side="long"),
     ss._donchian, {"n": 55, "k": 3.0, "exit": "channel", "side": "long"}),
    ("EMAPullback_v1", "ema_pullback", P.EMAPullbackParams(fast_ema=20, slow_ema=50),
     ss._ema_pullback, {"emas": (20, 50), "rr": 3.0, "side": "both"}),
    ("RSI2_v1", "rsi2", P.RSI2Params(threshold=10, max_hold_bars=24),
     ss._rsi2, {"th": 10, "hold": 24, "side": "both"}),
    ("BollingerFade_v1", "bollinger_fade", P.BollingerFadeParams(band_sigma=2.0),
     ss._bollinger, {"k": 2.0, "side": "both"}),
    ("VolBreakout_v1", "vol_breakout", P.VolBreakoutParams(channel_bars=20, volume_mult=1.5),
     ss._vol_breakout, {"n": 20, "m": 1.5, "side": "both"}),
]


def _engine(sid, section, params):
    cfg = UserConfigV2()
    setattr(cfg, section, params)
    return get_strategy(sid)(cfg)


@pytest.mark.parametrize("sid,section,params,builder,research", CASES,
                         ids=[f"{c[0]}-{i}" for i, c in enumerate(CASES)])
def test_live_engine_reproduces_the_measured_signals(sid, section, params, builder, research):
    df = _bars()
    eng = _engine(sid, section, params)
    w = window_bars("H1", eng)
    start = 1600                               # every window is full-length from here
    b = ss.Bars("X", "H1", (pd.DatetimeIndex(df.index).asi8 // 10**9).astype(np.int64),
                *(df[c].to_numpy() for c in ("open", "high", "low", "close")), np.zeros(len(df)),
                df["tick_volume"].to_numpy())
    got = []
    for i in range(start, len(df) - 1):
        sig = asyncio.run(eng.on_bar("X", "H1", df.iloc[max(0, i + 1 - w): i + 1]))
        if sig:
            got.append((i, 1 if sig.direction == "BUY" else -1, abs(sig.entry_price - sig.stop_loss)))
    if sid == "EMAPullback_v1":
        research = dict(research, rr=eng._slot_rr("X"))
    # the research ran on full history; long EMAs are compared on the same window the engine sees
    want = []
    for s in builder(b, research)[0]:
        if start <= s.i < len(df) - 1:
            want.append((s.i, s.direction, s.stop_dist))
    assert len(want) >= 5, "fixture too quiet to test anything"
    assert [(i, d) for i, d, _ in got] == [(i, d) for i, d, _ in want]
    for (_, _, a), (_, _, e) in zip(got, want):
        assert a == pytest.approx(e, rel=1e-6)


def test_tsmom_reproduces_the_measured_flips():
    df = _bars("D1", n=700, seed=2)
    eng = _engine("TSMOM_v1", "tsmom", P.TSMOMParams(lookback_days=60))
    w = window_bars("D1", eng)
    b = ss.Bars("X", "D1", (pd.DatetimeIndex(df.index).asi8 // 10**9).astype(np.int64),
                *(df[c].to_numpy() for c in ("open", "high", "low", "close")), np.zeros(len(df)))
    want = [(s.i, s.direction) for s in ss._tsmom(b, {"lookback": 60, "side": "both"})[0] if 250 <= s.i < len(df) - 1]
    got = []
    for i in range(250, len(df) - 1):
        sig = asyncio.run(eng.on_bar("X", "D1", df.iloc[max(0, i + 1 - w): i + 1]))
        if sig:
            got.append((i, 1 if sig.direction == "BUY" else -1))
    assert len(want) >= 5 and got == want


def test_exits_match_the_research_rules():
    df = _bars(n=300)
    last = df.iloc[-60:]
    c = float(last["close"].iloc[-1])

    don = _engine("Donchian_v1", "donchian", P.DonchianParams(exit_mode="trail"))
    act = don.on_position_bar("X", "H1", last, {"ticket": 7, "direction": "BUY"})
    b = ss.Bars("X", "H1", np.arange(60), *(last[k].to_numpy() for k in ("open", "high", "low", "close")), np.zeros(60))
    assert act.action == "MODIFY_SL" and act.new_sl == pytest.approx(c - 3.0 * ss.atr(b, 14)[-1])

    rsi = _engine("RSI2_v1", "rsi2", P.RSI2Params())
    sma5 = float(last["close"].iloc[-5:].mean())
    act = rsi.on_position_bar("X", "H1", last, {"ticket": 1, "direction": "BUY" if c >= sma5 else "SELL"})
    assert act is not None and act.action == "CLOSE" and act.close_reason == "SMA"

    boll = _engine("BollingerFade_v1", "bollinger_fade", P.BollingerFadeParams())
    sma20 = float(last["close"].iloc[-20:].mean())
    act = boll.on_position_bar("X", "H1", last, {"ticket": 1, "direction": "SELL" if c >= sma20 else "BUY"})
    assert act is None


def test_time_exit_counts_bars_not_hours():
    df = _bars(n=100)
    eng = _engine("RSI2_v1", "rsi2", P.RSI2Params(max_hold_bars=24))
    win = df.iloc[-40:]
    # entered 24 bars before the last bar -> held 24 bars -> out
    entry = win.index[-25]
    # keep the SMA exit from firing first: pick the side the mean exit would NOT close
    c, m = float(win["close"].iloc[-1]), float(win["close"].iloc[-5:].mean())
    side = "SELL" if c >= m else "BUY"
    act = eng.on_position_bar("X", "H1", win, {"ticket": 3, "direction": side, "entry_time": entry})
    assert act is not None and act.close_reason == "TIME"
    act = eng.on_position_bar("X", "H1", win, {"ticket": 3, "direction": side, "entry_time": win.index[-24]})
    assert act is None


def test_classic_strategies_are_wired_everywhere_a_strategy_must_be():
    from backend.api.routes.backtest import STRATEGY_PARAM_SECTION
    from backend.core.schema_introspection import build_full_schema
    from backend.strategies.strategy_defaults import STRATEGY_DEFAULTS

    groups = {row["group"] for row in build_full_schema()}
    for sid, cls in CLASSIC_STRATEGIES.items():
        assert STRATEGY_PARAM_SECTION[sid] == cls.PARAM_SECTION
        assert cls.PARAM_SECTION in groups
        assert STRATEGY_DEFAULTS[sid]["tp_count"] == 1 and STRATEGY_DEFAULTS[sid].get("evidence")
    cfg = UserConfigV2.from_dict({"donchian": {"channel_bars": 55}, "tsmom": {"lookback_days": 120}})
    assert cfg.donchian.channel_bars == 55 and cfg.tsmom.lookback_days == 120
