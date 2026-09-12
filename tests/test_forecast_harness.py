"""
tests/test_forecast_harness.py — [P3.10 / P3.11 / P3.13]

The post-cutoff forecasting test and the provider seam in front of it.

The properties that matter most, in order:
  1. a provider never sees a bar after its decision bar;
  2. no call is made once the plan exceeds the cost ceiling, and a run that
     cannot reach its provider stops instead of scoring the outage;
  3. the surrogate control is priced for what it actually does — every decision,
     on every surrogate series.
"""

import asyncio
import json
from datetime import datetime, timezone

import numpy as np
import pytest

from backend.analytics.forecast_context import ContextBuilder
from backend.analytics.forecast_harness import (
    CostCeilingExceeded,
    ForecastJournal,
    ProviderUnavailable,
    decision_indices,
    plan_run,
    resolve_outcome,
    run_test,
    verdict,
    walk_forward,
)
from backend.services.forecast_providers import (
    AnthropicProvider,
    BaselineProvider,
    Bars,
    CachedProvider,
    build_provider,
    is_operational_failure,
)
from backend.strategies.baseline_forecaster import Forecast

CUT = datetime(2026, 6, 1, tzinfo=timezone.utc)
CUT_E = int(CUT.timestamp())


def _data(n=1400, pre=500, seed=1, slope=0.0, noise=0.004, start=100.0):
    rng = np.random.default_rng(seed)
    c = start * np.exp(np.cumsum(rng.normal(slope, noise, n)))
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, noise / 2, n)))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, noise / 2, n)))
    t = CUT_E - pre * 3600 + np.arange(n) * 3600
    v = rng.integers(500, 5000, n).astype(float)
    return {"time": t, "open": o, "high": h, "low": lo, "close": c, "volume": v}


class Recording:
    """A paid-looking provider that records exactly what it was shown."""

    def __init__(self, direction="LONG", op_fail=False, cost=0.001):
        self.calls = 0
        self.seen = []
        self.direction = direction
        self.op_fail = op_fail
        self._cost = cost

    @property
    def name(self):
        return "recording"

    @property
    def costs_money(self):
        return True

    def estimate_call_cost_usd(self):
        return self._cost

    async def forecast(self, instrument, ctx, bars):
        self.calls += 1
        self.seen.append((bars.decision_time, len(bars.close), float(bars.close[-1])))
        if self.op_fail:
            return Forecast(instrument, abstain_reason="Analysis failed: Your credit balance is too low",
                            source="llm")
        if self.direction == "FLAT":
            return Forecast(instrument, abstain_reason="nothing here", source="llm")
        last, atr = ctx._private["last_close"], ctx._private["atr"]
        sgn = 1 if self.direction == "LONG" else -1
        return Forecast(instrument, direction=self.direction, conviction=0.6,
                        entry_zone=(last, last), invalidation=last - sgn * 2 * atr,
                        targets=[{"level": last + sgn * 4 * atr, "probability": 0.33}], source="llm")


def _run(coro):
    return asyncio.run(coro)


# ── providers ────────────────────────────────────────────────────────────────

def test_build_provider_specs(tmp_path):
    assert isinstance(build_provider("baseline"), BaselineProvider)
    p = build_provider("anthropic:claude-haiku-4-5")
    assert isinstance(p, AnthropicProvider)
    assert p.model == "claude-haiku-4-5" and p.rendering == "blinded"
    assert build_provider("anthropic:claude-haiku-4-5:plain").rendering == "plain"
    cached = build_provider("anthropic:claude-haiku-4-5", cache_dir=tmp_path)
    assert isinstance(cached, CachedProvider)
    assert cached.name == "anthropic:claude-haiku-4-5"
    assert isinstance(build_provider("baseline", cache_dir=tmp_path), BaselineProvider), \
        "a free provider gains nothing from a cache"
    with pytest.raises(ValueError):
        build_provider("openai:gpt-4o")
    with pytest.raises(ValueError):
        build_provider("anthropic:claude-haiku-4-5:sideways")


def test_the_default_model_is_opus_and_a_cheaper_one_is_an_explicit_choice():
    assert AnthropicProvider().model == "claude-opus-5"


def test_cost_estimates_rank_sensibly():
    assert BaselineProvider().estimate_call_cost_usd() == 0.0
    haiku = AnthropicProvider(model="claude-haiku-4-5").estimate_call_cost_usd()
    opus = AnthropicProvider(model="claude-opus-5").estimate_call_cost_usd()
    assert 0 < haiku < opus
    # measured prompt (869 in) + ~400 out, no thinking, at $1/$5 per Mtok
    assert haiku == pytest.approx(0.0029, abs=0.0005)


def test_thinking_models_get_output_headroom():
    """Thinking counts against max_tokens; a tight ceiling ends the turn before
    the JSON and the validator would score a config problem as a bad answer."""
    assert AnthropicProvider(model="claude-opus-5").resolved_max_tokens() >= 4000
    assert AnthropicProvider(model="claude-haiku-4-5").resolved_max_tokens() < 4000


@pytest.mark.parametrize("reason,operational", [
    ("Analysis failed: credit balance is too low", True),
    ("LLM call refused — daily cost cap reached", True),
    ("provider call failed: connection reset", True),
    ("No Anthropic API key configured", True),
    ("The model declined this request (cyber)", False),
    ("momentum inside the dead band", False),
])
def test_operational_failures_are_told_apart_from_model_abstentions(reason, operational):
    assert is_operational_failure(Forecast("X", abstain_reason=reason)) is operational
    assert is_operational_failure(
        Forecast("X", direction="LONG", conviction=0.5, abstain_reason=reason)) is False


# ── resolving an outcome ─────────────────────────────────────────────────────

def _flat_bars(n=10, px=100.0):
    return ([px] * n, [px + 0.5] * n, [px - 0.5] * n, [px] * n)


def _long(ref=100.0, stop=98.0, tgt=104.0):
    return Forecast("X", direction="LONG", conviction=0.5, entry_zone=(ref, ref),
                    invalidation=stop, targets=[{"level": tgt, "probability": 0.33}])


def test_target_hit():
    o, h, lo, c = _flat_bars()
    h[2] = 104.5
    out = resolve_outcome(_long(), open_=o, high=h, low=lo, close=c, decision_index=0, horizon=5)
    assert out.exit_reason == "TARGET" and out.r == pytest.approx(2.0) and out.exit_index == 2


def test_stop_touched_exactly_costs_exactly_one_r():
    o, h, lo, c = _flat_bars()
    lo[3] = 98.0
    out = resolve_outcome(_long(), open_=o, high=h, low=lo, close=c, decision_index=0, horizon=5)
    assert out.exit_reason == "STOP" and out.r == pytest.approx(-1.0)


def test_a_bar_that_travels_through_the_stop_pays_overshoot():
    o, h, lo, c = _flat_bars()
    lo[3] = 90.0
    out = resolve_outcome(_long(), open_=o, high=h, low=lo, close=c, decision_index=0, horizon=5)
    assert out.exit_reason == "STOP" and out.r < -1.0


def test_a_bar_touching_both_resolves_to_the_stop():
    o, h, lo, c = _flat_bars()
    h[2], lo[2] = 105.0, 97.0
    out = resolve_outcome(_long(), open_=o, high=h, low=lo, close=c, decision_index=0, horizon=5)
    assert out.exit_reason == "STOP"


def test_timeout_marks_to_the_horizon_close():
    o, h, lo, c = _flat_bars()
    c[5] = 101.0
    out = resolve_outcome(_long(), open_=o, high=h, low=lo, close=c, decision_index=0, horizon=5)
    assert out.exit_reason == "TIMEOUT" and out.exit_index == 5 and out.r == pytest.approx(0.5)


def test_entry_is_the_next_open_with_levels_re_anchored():
    """The convention the backtester and live path share: the fill moves, the
    geometry moves with it, and R is unchanged by the shift."""
    o, h, lo, c = _flat_bars()
    o[1], h[1], lo[1] = 101.0, 101.5, 100.5
    h[2] = 105.2
    out = resolve_outcome(_long(), open_=o, high=h, low=lo, close=c, decision_index=0, horizon=5)
    assert out.entry_price == pytest.approx(101.0)
    assert out.stop == pytest.approx(99.0) and out.target == pytest.approx(105.0)
    assert out.exit_reason == "TARGET" and out.r == pytest.approx(2.0)


def test_a_target_gapped_through_fills_at_the_better_open():
    o, h, lo, c = _flat_bars()
    o[2], h[2], lo[2] = 106.0, 106.5, 105.5
    out = resolve_outcome(_long(), open_=o, high=h, low=lo, close=c, decision_index=0, horizon=5)
    assert out.exit_reason == "TARGET" and out.r == pytest.approx(3.0)


def test_a_stop_gapped_through_fills_at_the_worse_open():
    o, h, lo, c = _flat_bars()
    o[3], h[3], lo[3] = 95.0, 95.5, 94.5
    out = resolve_outcome(_long(), open_=o, high=h, low=lo, close=c, decision_index=0, horizon=5)
    assert out.exit_reason == "STOP" and out.r == pytest.approx(-2.5)


def test_short_mirrors():
    o, h, lo, c = _flat_bars()
    lo[2] = 95.5
    f = Forecast("X", direction="SHORT", conviction=0.5, entry_zone=(100.0, 100.0),
                 invalidation=102.0, targets=[{"level": 96.0, "probability": 0.33}])
    out = resolve_outcome(f, open_=o, high=h, low=lo, close=c, decision_index=0, horizon=5)
    assert out.exit_reason == "TARGET" and out.r == pytest.approx(2.0)


def test_flat_and_incomplete_horizons_are_unresolvable():
    o, h, lo, c = _flat_bars()
    assert resolve_outcome(Forecast("X"), open_=o, high=h, low=lo, close=c,
                           decision_index=0, horizon=5) is None
    assert resolve_outcome(_long(), open_=o, high=h, low=lo, close=c,
                           decision_index=6, horizon=5) is None


# ── decisions ────────────────────────────────────────────────────────────────

def test_decisions_start_at_the_cutoff_and_never_overlap():
    t = CUT_E - 300 * 3600 + np.arange(1000) * 3600
    idx = decision_indices(t, cutoff_epoch=CUT_E, horizon=24, min_history=150)
    assert idx and all(int(t[i]) >= CUT_E for i in idx)
    assert int(t[idx[0]]) == CUT_E
    assert all(b - a >= 24 for a, b in zip(idx, idx[1:]))
    assert idx[-1] + 24 < len(t)


def test_short_history_delays_the_first_decision():
    t = CUT_E - 50 * 3600 + np.arange(1000) * 3600
    idx = decision_indices(t, cutoff_epoch=CUT_E, horizon=24, min_history=150)
    assert idx[0] == 149


def test_no_post_cutoff_data_means_no_decisions():
    t = CUT_E - 1000 * 3600 + np.arange(500) * 3600
    assert decision_indices(t, cutoff_epoch=CUT_E, horizon=24, min_history=150) == []


# ── the walk-forward ─────────────────────────────────────────────────────────

def test_provider_never_sees_a_bar_after_the_decision():
    d = _data()
    p = Recording()
    recs = _run(walk_forward(p, {"XAUUSD": d}, cutoff=CUT, horizon=24, lookback=400,
                             max_forecasts_per_instrument=10))
    assert p.calls == 10
    for (dt, n, last), rec in zip(p.seen, recs):
        i = rec.decision_index
        assert dt == int(d["time"][i]) and dt >= CUT_E
        assert last == pytest.approx(float(d["close"][i])), "the newest bar shown must BE the decision bar"
        assert n <= 400


def test_flat_forecasts_are_recorded_but_carry_no_r():
    recs = _run(walk_forward(Recording("FLAT"), {"X": _data()}, cutoff=CUT,
                             max_forecasts_per_instrument=5))
    assert len(recs) == 5
    assert all(r.r is None and r.exit_reason == "ABSTAIN" for r in recs)


def test_a_provider_that_cannot_be_reached_fails_fast_instead_of_looking_cautious():
    p = Recording(op_fail=True)
    with pytest.raises(ProviderUnavailable, match="credit balance"):
        _run(walk_forward(p, {"X": _data()}, cutoff=CUT, max_forecasts_per_instrument=50))
    assert p.calls == 3


def test_intermittent_outages_are_excluded_from_the_abstention_rate():
    class Flaky(Recording):
        async def forecast(self, instrument, ctx, bars):
            if (self.calls + 1) % 2 == 0:
                self.calls += 1
                return Forecast(instrument, abstain_reason="Analysis failed: 529 overloaded")
            return await super().forecast(instrument, ctx, bars)

    res = _run(run_test(Flaky(cost=0.0), {"A": _data()}, cutoff=CUT,
                        max_forecasts_per_instrument=10, n_surrogates=0, max_cost_usd=1.0))
    pi = res.per_instrument["A"]
    assert pi["operational_failures"] == 5
    assert pi["forecasts"] == 5
    assert pi["abstain_rate"] == 0.0, "an outage is not the model being cautious"


# ── money ────────────────────────────────────────────────────────────────────

def test_cost_ceiling_refuses_before_a_single_call():
    p = Recording(cost=1.0)
    with pytest.raises(CostCeilingExceeded, match="max-cost"):
        _run(run_test(p, {"X": _data()}, cutoff=CUT, max_forecasts_per_instrument=10,
                      n_surrogates=0, max_cost_usd=0.5))
    assert p.calls == 0


def test_plan_prices_the_surrogate_control_for_every_decision():
    """A surrogate run re-forecasts every decision point. Pricing it as one call
    per surrogate — the planning mistake this harness corrects — under-counts it
    by the number of decisions."""
    data = {"A": _data(seed=1), "B": _data(seed=2)}
    plan = plan_run(Recording(cost=0.01), data, cutoff=CUT, horizon=24, min_history=150,
                    max_forecasts_per_instrument=10, n_surrogates=3, surrogate_instruments=["A"])
    assert plan["real_calls"] == 20
    assert plan["surrogate_calls"] == 30
    assert plan["est_cost_usd"] == pytest.approx(0.50)


def test_cache_means_a_rerun_never_pays_twice(tmp_path):
    d = {"X": _data()}
    first = Recording()
    r1 = _run(walk_forward(CachedProvider(inner=first, path=tmp_path / "c.jsonl"), d,
                           cutoff=CUT, max_forecasts_per_instrument=6))
    assert first.calls == 6

    second = Recording()
    cached = CachedProvider(inner=second, path=tmp_path / "c.jsonl")   # a fresh process
    r2 = _run(walk_forward(cached, d, cutoff=CUT, max_forecasts_per_instrument=6))
    assert second.calls == 0 and cached.hits == 6
    assert [x.r for x in r1] == [x.r for x in r2]


def test_cache_never_stores_an_operational_failure(tmp_path):
    d = _data()
    ctx = ContextBuilder().build(instrument_class="x", timeframe="H1", high=d["high"][:400],
                                 low=d["low"][:400], close=d["close"][:400])
    bars = Bars(d["open"][:400], d["high"][:400], d["low"][:400], d["close"][:400], decision_time=1)
    inner = Recording(op_fail=True)
    cached = CachedProvider(inner=inner, path=tmp_path / "c.jsonl")
    _run(cached.forecast("X", ctx, bars))
    _run(cached.forecast("X", ctx, bars))
    assert inner.calls == 2, "a cached outage would replay forever as the model's answer"
    assert not (tmp_path / "c.jsonl").exists()


# ── the journal ──────────────────────────────────────────────────────────────

def test_journal_detects_an_altered_row(tmp_path):
    path = tmp_path / "j.jsonl"
    j = ForecastJournal(path)
    for k in range(4):
        j.append({"k": k, "r": 0.5 * k})
    assert j.verify() == {"ok": True, "rows": 4, "note": "chain intact"}

    lines = path.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[2])
    rec["row"]["r"] = 99.0                       # quietly improve a losing forecast
    lines[2] = json.dumps(rec)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    v = ForecastJournal(path).verify()
    assert v["ok"] is False and "altered" in v["note"]


def test_journal_detects_a_deleted_row(tmp_path):
    path = tmp_path / "j.jsonl"
    j = ForecastJournal(path)
    for k in range(4):
        j.append({"k": k})
    lines = path.read_text(encoding="utf-8").splitlines()
    del lines[1]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    v = ForecastJournal(path).verify()
    assert v["ok"] is False and "chain broken" in v["note"]


def test_journal_chain_continues_across_processes(tmp_path):
    path = tmp_path / "j.jsonl"
    a = ForecastJournal(path)
    a.append({"k": 0})
    a.append({"k": 1})
    ForecastJournal(path).append({"k": 2})
    assert ForecastJournal(path).verify() == {"ok": True, "rows": 3, "note": "chain intact"}


# ── the whole test ───────────────────────────────────────────────────────────

def test_end_to_end_with_the_free_baseline(tmp_path):
    data = {"A": _data(seed=3), "B": _data(seed=4)}
    res = _run(run_test(BaselineProvider(), data, cutoff=CUT, n_surrogates=10,
                        journal_path=tmp_path / "run.jsonl"))
    d = res.to_dict(include_records=False)
    for k in ("plan", "per_instrument", "score", "significance", "significance_independent",
              "surrogate", "verdict", "cost", "journal"):
        assert k in d, k
    assert res.surrogate["mode"] == "full" and res.surrogate["n_surrogate_series"] == 10
    assert res.plan["surrogate_calls"] == 10 * res.plan["real_calls"]
    assert res.verdict["verdict"] in ("PASS", "FAIL", "INCONCLUSIVE", "INSUFFICIENT")
    assert res.journal["ok"] is True
    assert res.journal["rows"] == res.plan["total_calls"]


def test_paid_providers_default_to_the_bootstrap_control():
    p = Recording(cost=0.0)
    res = _run(run_test(p, {"A": _data(seed=5)}, cutoff=CUT, max_forecasts_per_instrument=8,
                        n_surrogates=3, max_cost_usd=1.0))
    assert res.surrogate["mode"] == "bootstrap"
    assert res.surrogate["ran"] is True
    assert p.calls == 8 * (1 + 3), "the control must run the identical pipeline on every surrogate"


# ── the verdict ──────────────────────────────────────────────────────────────

def _sur(p, beats, real, mean):
    return {"ran": True, "p_value": p, "beats_control": beats,
            "real_expectancy": real, "surrogate_mean": mean}


def test_verdict_fails_when_no_better_than_structureless_data():
    v = verdict(n_actionable=100, significance_conservative={"verdict": "NOT SIGNIFICANT"},
                significance_independent={"n_nonoverlap": 100, "mean_r": 0.02},
                surrogate=_sur(0.6, False, 0.02, 0.05))
    assert v["verdict"] == "FAIL"


def test_verdict_is_insufficient_on_tiny_samples():
    v = verdict(n_actionable=12, significance_conservative={}, significance_independent={},
                surrogate=None)
    assert v["verdict"] == "INSUFFICIENT"


def test_verdict_is_inconclusive_when_promising_but_underpowered():
    v = verdict(n_actionable=120, significance_conservative={"verdict": "SIGNIFICANT"},
                significance_independent={"n_nonoverlap": 120, "mean_r": 0.3},
                surrogate=_sur(0.02, True, 0.3, 0.0))
    assert v["verdict"] == "INCONCLUSIVE"
    assert any("200" in r for r in v["reasons"])


def test_verdict_passes_only_when_every_condition_holds():
    v = verdict(n_actionable=250, significance_conservative={"verdict": "SIGNIFICANT"},
                significance_independent={"n_nonoverlap": 250, "mean_r": 0.3},
                surrogate=_sur(0.01, True, 0.3, 0.0))
    assert v == {"verdict": "PASS", "reasons": []}


def test_nothing_passes_without_the_control():
    v = verdict(n_actionable=250, significance_conservative={"verdict": "SIGNIFICANT"},
                significance_independent={"n_nonoverlap": 250, "mean_r": 0.3}, surrogate=None)
    assert v["verdict"] != "PASS"
    assert any("control not run" in r for r in v["reasons"])
