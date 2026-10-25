"""
Kronos feature service: feature math, gate staleness/fail-mode, no lookahead,
and (when torch and the weights are present) reproducible sampling.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from backend.kronos_service import features as kf
from backend.kronos_service import gate as kg
from backend.kronos_service import precompute as kp
from backend.kronos_service.config import KronosConfig


def _paths(ends, last=100.0, H=4):
    """S paths that walk linearly from `last` to each end, +-0.5 high/low."""
    out = np.zeros((len(ends), H, 6))
    for s, e in enumerate(ends):
        c = np.linspace(last, e, H + 1)[1:]
        out[s, :, 3] = c
        out[s, :, 0] = c
        out[s, :, 1] = c + 0.5
        out[s, :, 2] = c - 0.5
    return out


# -- feature math --------------------------------------------------------------


def test_feature_math():
    f = kf.compute(_paths([102, 104, 98, 106]), last_close=100.0, atr=2.0)
    assert f["p_up"] == 0.75
    assert f["exp_return_pct"] == pytest.approx(2.5)
    assert f["exp_return_atr"] == pytest.approx(1.25)
    assert f["dispersion_atr"] == pytest.approx(np.std([102, 104, 98, 106]) / 2.0)
    assert f["forecast_range_atr"] > 0


def test_a_missing_atr_gives_nan_not_a_fake_number():
    f = kf.compute(_paths([101]), last_close=100.0, atr=0.0)
    assert np.isnan(f["exp_return_atr"]) and f["p_up"] == 1.0


def test_the_rl_vector_masks_missing_values_with_a_flag():
    v, ok = kf.vector(None)
    assert not ok and not v.any()
    v, ok = kf.vector({"exp_return_pct": 1, "exp_return_atr": float("nan"), "p_up": 0.6,
                       "forecast_range_atr": 1, "dispersion_atr": 1})
    assert not ok and not v.any(), "a NaN must not leak into an observation"
    v, ok = kf.vector({k: 0.5 for k in kf.FEATURES})
    assert ok and v.tolist() == [0.5] * len(kf.FEATURES)


def test_atr_uses_only_the_bars_it_is_given():
    h = np.array([10, 11, 12, 11, 13] * 4, float)
    lo, c = h - 1, h - 0.5
    a1 = kf.atr(h, lo, c, n=3)
    assert a1 == kf.atr(h, lo, c, n=3) and a1 > 0
    assert np.isnan(kf.atr(h[:3], lo[:3], c[:3], n=3))


# -- the gate --------------------------------------------------------------------


ROW = {"asof": 1000, "p_up": 0.8, "dispersion_atr": 0.5, "exp_return_atr": 0.4}


def test_the_gate_lets_agreeing_setups_through_and_drops_the_rest():
    cfg = kg.GateConfig(enabled=True, min_agreement=0.6)
    assert kg.decide(cfg, "BUY", ROW, 1000).allow
    assert not kg.decide(cfg, "SELL", ROW, 1000).allow     # only 20% of paths agree


def test_disabled_gate_never_blocks():
    assert kg.decide(kg.GateConfig(enabled=False), "SELL", None, 1).allow


@pytest.mark.parametrize("mode,expected", [("open", True), ("closed", False)])
def test_missing_and_stale_features_follow_fail_mode(mode, expected):
    cfg = kg.GateConfig(enabled=True, fail_mode=mode)
    assert kg.decide(cfg, "BUY", None, 1000).allow is expected
    stale = dict(ROW, asof=700)               # a forecast from an older bar
    d = kg.decide(cfg, "BUY", stale, 1000)
    assert d.allow is expected and "stale" in d.reason


def test_dispersion_and_expected_move_limits():
    cfg = kg.GateConfig(enabled=True, max_dispersion_atr=0.3)
    assert not kg.decide(cfg, "BUY", ROW, 1000).allow
    cfg = kg.GateConfig(enabled=True, min_exp_return_atr=0.5)
    assert not kg.decide(cfg, "BUY", ROW, 1000).allow
    log = kg.decide(cfg, "BUY", ROW, 1000).to_log("XAUUSD", "ORB_v1", "BUY")
    assert {"symbol", "strategy", "direction", "allow", "reason", "features"} <= set(log)


# -- no lookahead ----------------------------------------------------------------


class FakeEngine:
    """Paths that are a deterministic function of the window: if any bar after
    the decision bar leaked in, the features would change."""

    def __init__(self, cfg):
        self.cfg = cfg

    def paths(self, windows):
        out = []
        for w in windows:
            c = w.frame["close"].to_numpy()
            drift = c[-1] - c[0]
            ends = c[-1] + drift * np.linspace(-0.5, 1.0, self.cfg.sample_count)
            out.append(_paths(ends, last=c[-1], H=self.cfg.pred_len))
        return np.stack(out), 0.01


def _frame(n=200, seed=1):
    rng = np.random.default_rng(seed)
    c = 100 + np.cumsum(rng.normal(0, 0.3, n))
    t = 1_735_689_600 + 300 * np.arange(n)
    return pd.DataFrame({"time": t, "open": c, "high": c + 0.2, "low": c - 0.2, "close": c,
                         "volume": rng.integers(10, 99, n).astype(float)})


def test_features_at_a_bar_do_not_see_any_later_bar():
    cfg = KronosConfig(lookback=64, pred_len=4, sample_count=5)
    eng = FakeEngine(cfg)
    f = _frame()
    i = 120
    base = kp.features_at(eng, f, [i], "EURUSD", "M5").iloc[0].to_dict()
    g = f.copy()
    g.loc[i + 1:, ["open", "high", "low", "close"]] *= 3.0     # rewrite the future
    after = kp.features_at(eng, g, [i], "EURUSD", "M5").iloc[0].to_dict()
    for k in kf.FEATURES:
        assert base[k] == after[k], k
    assert base["asof"] == int(f["time"].iloc[i])


def test_the_window_ends_at_the_decision_bar_and_future_times_are_calendar():
    cfg = KronosConfig(lookback=64, pred_len=3)
    f = _frame()
    w = kp.window_at(f, 100, cfg, "EURUSD", "M5")
    assert len(w.frame) == 64 and int(w.frame["time"].iloc[-1]) == int(f["time"].iloc[100])
    assert (w.future_times[0] - pd.Timestamp(w.asof, unit="s")) == pd.Timedelta(minutes=5)
    assert kp.window_at(f, 10, cfg, "EURUSD", "M5") is None


def test_the_store_round_trips(tmp_path):
    cfg = KronosConfig(lookback=64, pred_len=4, sample_count=3)
    f = _frame()
    eng = FakeEngine(cfg)
    got = kp.extend(eng, f, [100, 150], "EURUSD", "M5", root=str(tmp_path))
    again = kp.extend(eng, f, [100, 150, 160], "EURUSD", "M5", root=str(tmp_path))
    assert len(got) == 2 and len(again) == 3
    assert int(f["time"].iloc[160]) in kp.load("EURUSD", "M5", cfg, str(tmp_path))


def test_model_id_changes_with_anything_that_changes_the_output():
    a = KronosConfig()
    assert a.model_id == KronosConfig().model_id
    assert a.model_id != KronosConfig(sample_count=10).model_id
    assert a.model_id == KronosConfig(threads=4).model_id   # speed is not output
    with pytest.raises(ValueError):
        KronosConfig(model="Kronos-base")


# -- the real model (skipped without torch / weights) -------------------------


def _real_engine(**kw):
    pytest.importorskip("torch")
    cfg = KronosConfig(model="Kronos-mini", lookback=128, pred_len=3, sample_count=3, **kw)
    if not (cfg.model_dir() / "model.safetensors").exists():
        pytest.skip("Kronos weights not downloaded")
    from backend.kronos_service.predictor import KronosEngine
    return KronosEngine(cfg)


def test_real_paths_are_reproducible_and_batch_independent():
    eng = _real_engine()
    f = _frame(400)
    w1 = kp.window_at(f, 300, eng.cfg, "EURUSD", "M5")
    w2 = kp.window_at(f, 350, eng.cfg, "EURUSD", "M5")
    alone, _ = eng.paths([w1])
    batched, _ = eng.paths([w2, w1])
    again, _ = eng.paths([w1])
    assert alone.shape == (1, 3, 3, 6)
    assert np.allclose(alone[0], again[0])
    assert np.allclose(alone[0], batched[1], atol=1e-4), "a path must not depend on its batch"


def test_the_kv_cache_gives_the_same_paths_as_upstreams_loop():
    """The cached generator must be the same computation, not an approximation:
    with the same seeds it has to sample the same tokens."""
    eng = _real_engine()
    f = _frame(400)
    w = kp.window_at(f, 300, eng.cfg, "EURUSD", "M5")
    eng.cached = True
    fast, t_fast = eng.paths([w])
    eng.cached = False
    slow, t_slow = eng.paths([w])
    eng.cached = True
    assert np.allclose(fast, slow, atol=1e-3), np.abs(fast - slow).max()
