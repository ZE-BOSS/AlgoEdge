"""fvg_research must reproduce HTFFVGFlip_v1 and BiasIFVG_v1 signal for signal —
same bar, same direction, same entry, same stop distance — when fed the way the
backtest route feeds the engines. Its confluence numbers are only about these
strategies if this holds."""

import asyncio

import numpy as np
import pandas as pd
import pytest

from backend.analytics import fvg_research as fr
from backend.core.config_schema import UserConfigV2
from backend.risk.position_sizer import get_pip_size
from backend.strategies.registry import get_strategy
from backend.strategies.windows import window_bars

SYMBOL = "EURUSD"
NP_TD = {"M5": (5, "m"), "M15": (15, "m"), "H1": (1, "h"), "H4": (4, "h")}
TF_MIN = {"M5": 5, "M15": 15, "H1": 60, "H4": 240}


def _m5(n=9000, seed=3):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-06 00:00", periods=n, freq="5min")
    vol = 0.00025 * np.exp(np.cumsum(rng.normal(0, 0.03, n)).clip(-1, 1))
    drift = np.repeat(rng.normal(0, 0.00004, n // 120 + 1), 120)[:n]
    ret = drift + vol * rng.standard_t(3, n) * 0.6
    close = 1.10 + np.cumsum(ret)
    open_ = np.r_[close[0], close[:-1]] + rng.normal(0, 0.00003, n)
    wick = np.abs(rng.normal(0.00012, 0.0001, (2, n)))
    high = np.maximum(open_, close) + wick[0]
    low = np.minimum(open_, close) - wick[1]
    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                       "tick_volume": np.ones(n)}, index=idx)
    df["time"] = idx.as_unit("s").asi8
    return df


def _resample(m5: pd.DataFrame, rule: str) -> pd.DataFrame:
    g = m5.resample(rule, label="left", closed="left")
    out = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                        "close": g["close"].last(), "tick_volume": g["tick_volume"].sum()}).dropna()
    out["time"] = out.index.as_unit("s").asi8
    return out


def _arrays(df):
    return {k: df[k].to_numpy() for k in ("open", "high", "low", "close")} | {"time": df["time"].to_numpy()}


def _run_engine(sid, frames, cfg=None):
    eng = get_strategy(sid)(cfg or UserConfigV2())
    eng.is_backtesting = True
    req = eng.get_required_timeframes()
    primary = sorted(req, key=lambda t: TF_MIN[t])[0]
    times = frames[primary].index.values
    tf_times = {tf: frames[tf].index.values for tf in req}
    prev = {tf: None for tf in req}
    sigs = []

    async def go():
        for i in range(20, len(times)):
            cur = times[i]
            got = None
            for tf in req:
                if tf == primary:
                    end, last = i, times[i]
                else:
                    cut = cur - np.timedelta64(*NP_TD[tf])
                    end = int(np.searchsorted(tf_times[tf], cut, side="right"))
                    last = tf_times[tf][end - 1] if end > 0 else None
                if last is None or last == prev[tf]:
                    continue
                sl = frames[tf].iloc[max(0, end - window_bars(tf, eng)):end]
                if len(sl) < 20:
                    continue
                s = await eng.on_bar(SYMBOL, tf, sl)
                if s:
                    got = s
                prev[tf] = last
            if got is not None:
                d = 1 if got.direction == "BUY" else -1
                sigs.append((int(frames[primary]["time"].iloc[i - 1]), d, round(got.entry_price, 8),
                             round(abs(got.entry_price - got.stop_loss), 8)))

    asyncio.run(go())
    return sigs


@pytest.fixture(scope="module")
def frames():
    m5 = _m5()
    return {"M5": m5, "M15": _resample(m5, "15min"), "H1": _resample(m5, "1h"), "H4": _resample(m5, "4h")}


def _research(cands):
    return [(c.t_signal, c.direction, round(c.detail["entry_signal"], 8), round(c.stop_dist, 8)) for c in cands]


def test_htf_fvg_flip_default_variant_matches_engine(frames):
    engine = _run_engine("HTFFVGFlip_v1", frames)
    pip = get_pip_size(SYMBOL)
    out = fr.htf_candidates(fr.from_arrays(_arrays(frames["M5"])), fr.from_arrays(_arrays(frames["H1"])),
                            pip, [fr.HTFVariant()])
    research = _research(out[fr.HTFVariant().name])
    assert engine, "engine produced no HTFFVGFlip signals on the fixture — enlarge or reseed it"
    assert research == engine


def test_bias_ifvg_default_variant_matches_engine(frames, monkeypatch):
    # The research candidates are permissive about the day-stop rule and the daily
    # cap (both are applied at selection); open them on the engine too.
    from backend.strategies.strategy_five_bias_ifvg.params import BiasIFVGParams
    monkeypatch.setattr(BiasIFVGParams, "max_trades_per_day", 10_000)
    from backend.strategies.strategy_five_bias_ifvg import engine as be
    monkeypatch.setattr(be.BiasIFVGEngine, "notify_outcome", lambda *a, **k: None)
    engine = _run_engine("BiasIFVG_v1", frames)
    pip = get_pip_size(SYMBOL)
    out = fr.bias_candidates(fr.from_arrays(_arrays(frames["M5"])), fr.from_arrays(_arrays(frames["M15"])),
                             fr.from_arrays(_arrays(frames["H4"])), pip, [fr.BiasVariant()])
    research = _research(out[fr.BiasVariant().name])
    assert engine, "engine produced no BiasIFVG signals on the fixture — enlarge or reseed it"
    assert research == engine


def test_structure_trend_matches_detector(frames):
    from backend.strategies.core.market_structure import MarketStructureDetector
    h1 = frames["H1"]
    det = MarketStructureDetector(swing_length=5, min_bos_count=1)
    st = fr.Structure(fr.from_arrays(_arrays(h1)))
    mism = 0
    for k in range(20, len(h1)):
        det.update(h1.iloc[max(0, k - 199):k + 1])
        want = {"BULLISH": 1, "BEARISH": -1}.get(det.get_bias(), 0)
        mism += int(st.update(k) != want)
    assert mism == 0


HTF_CASES = [
    (fr.HTFVariant(trend="WITH", first_tap_only=True, require_retest=False, displacement=False, session_rth=False), {}, ()),
    (fr.HTFVariant(trend="OFF", first_tap_only=False, require_retest=True, displacement=False, session_rth=False), {}, ()),
    (fr.HTFVariant(trend="OFF", first_tap_only=True, require_retest=False, displacement=False, session_rth=False),
     {"min_inversion_disp_atr": 0.25, "require_stop_ok": True}, ("inv_disp_025", "stop_ok")),
]


@pytest.mark.parametrize("variant,gates,flags", HTF_CASES)
def test_htf_variants_and_gates_match_engine(frames, variant, gates, flags):
    cfg = UserConfigV2()
    for k, v in {**variant.params(), **gates}.items():
        setattr(cfg.htf_fvg_flip, k, v)
    engine = _run_engine("HTFFVGFlip_v1", frames, cfg)
    pip = get_pip_size(SYMBOL)
    out = fr.htf_candidates(fr.from_arrays(_arrays(frames["M5"])), fr.from_arrays(_arrays(frames["H1"])),
                            pip, [variant])
    cands = [c for c in out[variant.name] if all(c.feats[f] for f in flags)]
    assert out[variant.name], "research produced no candidates for this variant on the fixture"
    assert _research(cands) == engine


BIAS_CASES = [
    (fr.BiasVariant(bias_mode="OFF", key_levels="FVG", leg_mode="REACTION", session="OFF"), {}, ()),
    (fr.BiasVariant(bias_mode="H4", key_levels="CISD_REJ", leg_mode="BOTH", session="0800-1600"), {}, ()),
    (fr.BiasVariant(bias_mode="OFF", key_levels="ALL", leg_mode="BOTH", session="OFF"),
     {"min_inversion_disp_atr": 0.25}, ("inv_disp_025",)),
]


@pytest.mark.parametrize("variant,gates,flags", BIAS_CASES)
def test_bias_variants_and_gates_match_engine(frames, variant, gates, flags, monkeypatch):
    from backend.strategies.strategy_five_bias_ifvg import engine as be
    monkeypatch.setattr(be.BiasIFVGEngine, "notify_outcome", lambda *a, **k: None)
    cfg = UserConfigV2()
    for k, v in {**variant.params(), **gates, "max_trades_per_day": 10_000, "day_stop_enabled": False}.items():
        setattr(cfg.bias_ifvg, k, v)
    engine = _run_engine("BiasIFVG_v1", frames, cfg)
    pip = get_pip_size(SYMBOL)
    out = fr.bias_candidates(fr.from_arrays(_arrays(frames["M5"])), fr.from_arrays(_arrays(frames["M15"])),
                             fr.from_arrays(_arrays(frames["H4"])), pip, [variant])
    cands = [c for c in out[variant.name] if all(c.feats[f] for f in flags)]
    assert out[variant.name], "research produced no candidates for this variant on the fixture"
    assert _research(cands) == engine
