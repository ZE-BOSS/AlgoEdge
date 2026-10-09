"""
tests/test_rl_validate.py

The gate, and the distillation it checks.

WHAT A VALIDATION SUITE IS FOR
------------------------------
Not to prove the gate passes something. To prove it REJECTS: a gate that
cannot fail is a rubber stamp, and this project's recurring failure has been a
confident false positive. So most of these tests construct something that
should be refused and check that it is.

The statistics get their own tests because both are easy to get subtly wrong in
a way that always flatters the result: a PBO computed over one candidate is
always zero (there is no selection, so no selection bias), and a deflated
Sharpe with the haircut dropped is just a Sharpe.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.rl import charter as ch
from backend.rl import distill
from backend.rl import env as E
from backend.rl import policy as pol
from backend.rl import validate as V
from backend.rl.spec import AgentSpec


# ── PBO ────────────────────────────────────────────────────────────────────

def test_pbo_is_one_when_the_in_sample_winner_always_loses_out_of_sample():
    IS = np.array([[1.0, 1.0], [0.0, 0.0]])
    OOS = np.array([[-1.0, -1.0], [1.0, 1.0]])
    assert V.probability_of_backtest_overfitting(IS, OOS) == pytest.approx(1.0)


def test_pbo_is_zero_when_the_in_sample_winner_also_wins_out_of_sample():
    IS = np.array([[1.0, 1.0], [0.0, 0.0]])
    OOS = np.array([[1.0, 1.0], [-1.0, -1.0]])
    assert V.probability_of_backtest_overfitting(IS, OOS) == pytest.approx(0.0)


def test_pbo_cannot_be_computed_from_one_candidate():
    """With one candidate there is no selection, so there is no selection bias
    to measure - and returning a flattering zero would be worse than refusing."""
    assert V.probability_of_backtest_overfitting(np.array([[1.0, 2.0]]),
                                                 np.array([[1.0, 2.0]])) is None


def test_pbo_cannot_be_computed_from_one_path():
    assert V.probability_of_backtest_overfitting(np.array([[1.0], [2.0]]),
                                                 np.array([[1.0], [2.0]])) is None


def test_the_pbo_matrices_are_seeds_by_paths():
    from backend.rl.train import SeedResult

    results = [SeedResult(seed=s, fold=f, train_score=s + f, test_mean_r=s - f)
               for s in (1, 2, 3) for f in (0, 1)]
    IS, OOS = V.pbo_matrices(results)
    assert IS.shape == (3, 2) and OOS.shape == (3, 2)
    assert IS[2, 1] == pytest.approx(4.0) and OOS[2, 1] == pytest.approx(2.0)


def test_one_seed_cannot_produce_pbo_matrices():
    from backend.rl.train import SeedResult

    assert V.pbo_matrices([SeedResult(seed=1, fold=f) for f in range(4)]) is None


# ── the deflated Sharpe ────────────────────────────────────────────────────

def test_more_attempts_raise_the_bar():
    """Try 140 configurations on noise and the best one has a respectable
    Sharpe. The haircut is what asks whether the observed one beats that."""
    few = V.expected_max_sharpe(5, 0.5)
    many = V.expected_max_sharpe(500, 0.5)
    assert many > few > 0


def test_wider_disagreement_across_trials_raises_the_bar():
    """Seeds that disagree are themselves evidence that the thing being
    measured is noise."""
    assert V.expected_max_sharpe(40, 1.0) > V.expected_max_sharpe(40, 0.2)


def test_a_sharpe_that_does_not_beat_the_noise_is_haircut_negative():
    weak = V.deflated_sharpe(0.4, n_trials=200, n_obs=300, trial_sd=0.8)
    assert weak["haircut"] < 0


def test_a_strong_sharpe_survives_the_haircut():
    strong = V.deflated_sharpe(3.0, n_trials=40, n_obs=500, trial_sd=0.3)
    assert strong["haircut"] > 0 and strong["probability"] > 0.5


def test_fat_tails_make_the_estimate_noisier():
    """Trade returns are not normal, and pretending they are flatters every
    result."""
    normal = V.deflated_sharpe(1.5, 40, 300, skew=0.0, kurtosis=3.0, trial_sd=0.4)
    fat = V.deflated_sharpe(1.5, 40, 300, skew=-1.5, kurtosis=12.0, trial_sd=0.4)
    assert fat["probability"] < normal["probability"]


def test_too_few_observations_gives_nothing():
    assert V.deflated_sharpe(2.0, 10, 2) is None


def test_the_inverse_normal_matches_known_values():
    assert V._norm_ppf(0.975) == pytest.approx(1.95996, abs=1e-4)
    assert V._norm_ppf(0.5) == pytest.approx(0.0, abs=1e-9)
    assert V._norm_ppf(0.01) == pytest.approx(-2.32635, abs=1e-4)


# ── the bars are data ──────────────────────────────────────────────────────

def test_every_bar_in_the_gate_is_written_down():
    for key in ("min_seeds", "max_pbo", "min_cpcv_paths", "cost_stress_multiplier",
                "min_adherence", "min_trades", "min_charter_fidelity"):
        assert key in V.BARS


def test_the_bars_match_what_the_plan_pre_registered():
    assert V.BARS["min_seeds"] == 5
    assert V.BARS["max_pbo"] == 0.20
    assert V.BARS["min_cpcv_paths"] == 8
    assert V.BARS["cost_stress_multiplier"] == 2.0
    assert V.BARS["min_adherence"] == 0.6
    assert V.BARS["min_trades"] == 100


def test_a_skipped_check_is_not_a_pass():
    rep = V.Report(agent_id="x")
    rep.add(V.Check("something", False, skipped=True, detail="no data"))
    assert rep.passed is False, "an empty report must not read as a pass"
    rep.add(V.Check("else", True))
    assert rep.passed is True
    assert [c.name for c in rep.failures] == []


def test_a_failure_is_named_in_the_report():
    rep = V.Report(agent_id="x")
    rep.add(V.Check("PBO", False, 0.45, 0.20, "too high"))
    rep.add(V.Check("seeds", True, 5.0, 5.0))
    assert rep.passed is False
    assert [c.name for c in rep.failures] == ["PBO"]
    assert "REJECTED" in rep.text()


# ── distillation ───────────────────────────────────────────────────────────

def test_a_split_separates_what_the_policy_decided():
    Z = np.zeros((400, 2))
    Z[:200, 0] = 2.0
    labels = np.r_[np.zeros(200, dtype=int), np.ones(200, dtype=int)]
    found = distill.best_split(Z[:, 0], labels)
    assert found is not None
    threshold, op, gain = found
    assert gain > 0.4 and 0.0 < threshold <= 2.0


def test_a_gate_that_fires_on_almost_every_bar_is_refused():
    """It would push adherence to 1.0 for free, which is the quiet way to make
    the 0.30 reward term meaningless.

    Built as a column that is 1.0 on 99% of bars: every candidate quantile
    lands on 1.0, so the only available gate would fire on 99% of bars.
    """
    Z = np.ones((500, 1))
    Z[:5, 0] = 0.0
    labels = np.random.default_rng(2).integers(0, 3, 500)
    assert distill.candidate_splits(Z, labels, ["ret_1"]) == []


def test_a_truly_constant_column_produces_no_gate_at_all():
    labels = np.random.default_rng(3).integers(0, 3, 500)
    assert distill.best_split(np.zeros(500), labels) is None


def test_distillation_produces_a_draft_and_never_an_approved_charter():
    """Approval is the admin's. The status machine is what stops a freshly
    distilled charter reaching a live slot."""
    spec = AgentSpec(name="d", symbols=("EURUSD",))
    Z = np.random.default_rng(3).normal(0, 1, (600, spec.n_features))
    p = pol.LinearPolicy(n_features=spec.n_features)
    p.from_flat(np.random.default_rng(4).normal(0, 0.8, p.n_params))
    c = distill.distil(p, Z, spec.features, spec, agent_id="d-1")
    assert c.status == ch.DRAFT
    assert c.permits["live"] is False
    assert c.gates, "a charter with no gates would be unscoreable"
    assert c.tiers and {t.name for t in c.tiers} == {"A", "B", "C"}


def test_every_distilled_gate_names_a_real_feature():
    """Otherwise adherence is scored against something the agent cannot see."""
    spec = AgentSpec(name="d", symbols=("EURUSD",))
    Z = np.random.default_rng(5).normal(0, 1, (600, spec.n_features))
    p = pol.LinearPolicy(n_features=spec.n_features)
    p.from_flat(np.random.default_rng(6).normal(0, 0.8, p.n_params))
    c = distill.distil(p, Z, spec.features, spec, agent_id="d-1")
    from backend.rl.features import FEATURES
    assert all(g.feature in FEATURES for g in c.gates)


def test_fidelity_is_balanced_so_never_trade_cannot_score_well():
    """Raw agreement is misleading when the policy trades on 5% of bars: a
    charter that says "never trade" would score 95%."""
    spec = AgentSpec(name="d", symbols=("EURUSD",))
    Z = np.random.default_rng(7).normal(0, 1, (800, spec.n_features))
    p = pol.LinearPolicy(n_features=spec.n_features)
    p.b_entry = np.array([3.0, 0.0, 0.0])               # almost always long
    never = ch.StrategyCharter(
        agent_id="d-1",
        gates=(ch.Gate(name="impossible", feature="ret_1", op=">=", threshold=99.0),),
        tiers=(ch.Tier("A", ("impossible",)),), entry_tier="A")
    fid = distill.fidelity(never, p, Z, spec.features, spec.min_confidence)
    assert fid["agreement"] < 0.1
    assert fid["balanced"] <= 0.5


def test_a_charter_that_reproduces_the_policy_scores_high():
    spec = AgentSpec(name="d", symbols=("EURUSD",))
    Z = np.zeros((600, spec.n_features))
    Z[:300, 0] = 3.0                                     # ret_1 high on the first half
    p = pol.LinearPolicy(n_features=spec.n_features)
    p.W_entry[0, 0] = 5.0                                # long when ret_1 is high
    c = distill.distil(p, Z, spec.features, spec, agent_id="d-1")
    fid = c.validation["fidelity"]
    assert fid["balanced"] > 0.7, fid


def test_the_thesis_states_its_own_sample_size():
    """An admin approving this is agreeing with a claim, so the claim carries
    its evidence."""
    spec = AgentSpec(name="d", symbols=("EURUSD",))
    Z = np.random.default_rng(8).normal(0, 1, (600, spec.n_features))
    p = pol.LinearPolicy(n_features=spec.n_features)
    p.from_flat(np.random.default_rng(9).normal(0, 0.8, p.n_params))
    c = distill.distil(p, Z, spec.features, spec, agent_id="d-1",
                       n_trades=213, mean_r=0.0412)
    assert "213 trades" in c.thesis and "0.0412" in c.thesis
    assert "balanced accuracy" in c.thesis


def test_the_measured_hit_rate_and_mean_r_reach_the_gates():
    spec = AgentSpec(name="d", symbols=("EURUSD",))
    Z = np.random.default_rng(10).normal(0, 1, (600, spec.n_features))
    p = pol.LinearPolicy(n_features=spec.n_features)
    p.from_flat(np.random.default_rng(11).normal(0, 0.8, p.n_params))
    bars = {i: 0.5 for i in range(0, 600, 7)}
    c = distill.distil(p, Z, spec.features, spec, agent_id="d-1", trade_bars=bars)
    assert any(g.hit_rate is not None for g in c.gates)
    assert any(g.mean_r is not None for g in c.gates)


# ── the whole gate, on a real episode ──────────────────────────────────────

def fx_frame(n=3000, seed=12):
    rng = np.random.default_rng(seed)
    start = 1735689600
    t = np.arange(start, start + n * 300, 300, dtype="int64")
    close = 1.08 + np.cumsum(rng.normal(0, 0.00025, n))
    return pd.DataFrame({"time": t, "open": close - 0.00001, "high": close + 0.00015,
                         "low": close - 0.00015, "close": close,
                         "volume": rng.integers(50, 500, n).astype(float)})


@pytest.mark.slow
def test_the_gate_rejects_an_untrained_agent_and_says_why():
    """The most important test in this file. An agent that does nothing must be
    REJECTED with named reasons, not quietly passed for having no failures."""
    spec = AgentSpec(name="nothing", symbols=("EURUSD",), max_cpcv_paths=8)
    data = E.EpisodeData.build("EURUSD", "M5", fx_frame(), spec.features)
    cfg = E.default_risk_config("EURUSD", risk_per_trade_pct=0.5, spread_pips=0.2,
                                slippage_pips=0.1, commission_per_lot=0.0,
                                swap_long_per_lot_per_day=0.0,
                                swap_short_per_lot_per_day=0.0, stops_level_pips=0.0)
    from backend.rl import features as F
    scaler = F.Scaler().fit(data.X[:2000], data.names, fitted_on="rows 0-2000")
    rep = V.validate(spec, pol.LinearPolicy(n_features=spec.n_features), scaler,
                     data, cfg, agent_id="nothing")
    assert rep.passed is False
    names = [c.name for c in rep.failures]
    assert "sample size" in names
    assert rep.trades == 0
    assert "REJECTED" in rep.text()


@pytest.mark.slow
def test_the_leak_probe_runs_inside_the_gate():
    spec = AgentSpec(name="probe", symbols=("EURUSD",), max_cpcv_paths=8)
    data = E.EpisodeData.build("EURUSD", "M5", fx_frame(1200), spec.features)
    cfg = E.default_risk_config("EURUSD", spread_pips=0.2, slippage_pips=0.1,
                                commission_per_lot=0.0, stops_level_pips=0.0)
    from backend.rl import features as F
    scaler = F.Scaler().fit(data.X[:800], data.names)
    rep = V.validate(spec, pol.LinearPolicy(n_features=spec.n_features), scaler,
                     data, cfg, agent_id="probe")
    probe = next(c for c in rep.checks if c.name == "leak probe")
    # The gate probes the features THIS agent uses, not the whole registry -
    # the whole registry is probed by tests/test_rl_features.py and by
    # GET /api/rl/leakcheck.
    assert probe.passed and probe.value == len(spec.features)


# ── the supervisor's gate ──────────────────────────────────────────────────

class _Ep:
    def __init__(self, mean_r, n):
        from backend.rl.reward import RewardBreakdown
        self.breakdown = RewardBreakdown(n_trades=n, mean_r=mean_r)
        self.trades = [{}] * n
        self.rewards = []

    def to_dict(self):
        return {"mean_r": self.breakdown.mean_r, "n_trades": self.breakdown.n_trades}

    @property
    def mean_r(self):
        return self.breakdown.mean_r


def _sup(strategy_id, delta, closed_early=(), let_run=(), n=150):
    return E.SupervisionResult(
        strategy_id=strategy_id, symbol="EURUSD",
        unsupervised=_Ep(0.05, n), supervised=_Ep(0.05 + delta, n),
        closed_early=list(closed_early), let_run=list(let_run))


def test_a_supervisor_that_helps_one_strategy_and_hurts_another_is_refused():
    """It gets scoped to the one it helps, not shipped because the average is
    positive."""
    rep = V.validate_supervisor([_sup("A", +0.03), _sup("B", -0.02)])
    assert rep.passed is False
    assert "harms no strategy" in [c.name for c in rep.failures]


def test_cutting_winners_is_caught_by_the_winner_protection_check():
    """The one failure mode a supervisor has. Break-even at 1.5R turned 16 of
    113 BTCUSD trades into exits the research never took."""
    cutter = _sup("A", +0.01, closed_early=[1.5, 2.0, 1.8], let_run=[0.1, -0.2, 0.3])
    assert cutter.cutting_winners is True
    rep = V.validate_supervisor([cutter])
    assert "winner protection" in [c.name for c in rep.failures]


def test_cutting_losers_passes_the_winner_protection_check():
    good = _sup("A", +0.04, closed_early=[-0.3, -0.4, -0.2], let_run=[0.5, 0.8, -1.0])
    assert good.cutting_winners is False
    rep = V.validate_supervisor([good])
    assert "winner protection" not in [c.name for c in rep.failures]


def test_fewer_than_a_hundred_supervised_trades_is_not_measurable():
    rep = V.validate_supervisor([_sup("A", +0.05, n=40)])
    assert "trade count" in [c.name for c in rep.failures]


def test_a_supervisor_with_no_runs_is_not_a_pass():
    assert V.validate_supervisor([]).passed is False
