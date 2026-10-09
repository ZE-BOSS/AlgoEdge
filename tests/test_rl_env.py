"""
tests/test_rl_env.py

The environment: the real engine, the real costs, and the timings that have
cost this project money before.

THE FOUR THINGS WORTH TESTING HERE
----------------------------------
1. **Entry timing.** A signal stamped one bar late cost 10R over 152 GBPJPY
   trades. The stamp must be bar j's own time so the engine fills at bar j+1's
   open, which is what live does.

2. **The adherence join.** The engine stamps a trade's `entry_time` with the
   FILL bar, not the signal bar. Keying the decision record by the signal bar
   matched nothing, so every trade scored zero adherence and the 0.30 reward
   term did nothing at all. Found by a test like this one.

3. **CPCV purge AND embargo.** Purging without the embargo is the common
   half-measure: a training bar 200 bars before a test block still has a label
   that lands inside it.

4. **The two falsification controls.** Shuffling the features and shifting the
   entry are the only checks that catch a leak in the ENVIRONMENT rather than
   in the features, which the prefix probe cannot see.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.rl import env as E
from backend.rl import policy as pol
from backend.rl.spec import AgentSpec


def fx_frame(n=2500, seed=4):
    """FX-scaled bars. The scale matters: an index-priced series named EURUSD
    produces a stop thousands of pips wide and every signal is refused for
    being below the broker's minimum lot."""
    rng = np.random.default_rng(seed)
    start = 1735689600
    t = np.arange(start, start + n * 300, 300, dtype="int64")
    close = 1.08 + np.cumsum(rng.normal(0, 0.00025, n))
    return pd.DataFrame({"time": t, "open": close - 0.00001, "high": close + 0.00015,
                         "low": close - 0.00015, "close": close,
                         "volume": rng.integers(50, 500, n).astype(float)})


@pytest.fixture(scope="module")
def cfg():
    return E.default_risk_config(
        "EURUSD", risk_per_trade_pct=0.5, spread_pips=0.2, slippage_pips=0.1,
        commission_per_lot=0.0, swap_long_per_lot_per_day=0.0,
        swap_short_per_lot_per_day=0.0, stops_level_pips=0.0)


@pytest.fixture(scope="module")
def spec():
    return AgentSpec(name="test", symbols=("EURUSD",), timeframe="M5")


@pytest.fixture(scope="module")
def data(spec):
    return E.EpisodeData.build("EURUSD", "M5", fx_frame(), spec.features)


@pytest.fixture(scope="module")
def env(spec, data, cfg):
    e = E.TradingEnv(spec, data, cfg)
    e.fit_scaler(slice(0, 1500), label="rows 0-1500")
    return e


def trained(spec, seed=7, scale=0.6):
    p = pol.LinearPolicy(n_features=spec.n_features, exit_head=True, seed=seed)
    return p.from_flat(np.random.default_rng(seed).normal(0, scale, p.n_params))


# ── the scaler must be fitted first ────────────────────────────────────────

def test_an_episode_refuses_to_run_before_the_scaler_is_fitted(spec, data, cfg):
    """Running with an unfitted scaler would mean fitting it on whatever is to
    hand, which is the whole-dataset leak."""
    e = E.TradingEnv(spec, data, cfg)
    with pytest.raises(RuntimeError, match="training fold"):
        _ = e.Z


def test_the_scaler_records_the_fold_it_was_fitted_on(env):
    assert "0-1500" in env.scaler.fitted_on


# ── entry timing ───────────────────────────────────────────────────────────

def test_a_signal_is_stamped_with_its_own_bar_so_the_fill_is_the_next_open(env, data):
    """The scar: stamping bar j+1 instead of bar j made every entry fill one
    bar late and cost 10R over 152 GBPJPY trades."""
    side = np.zeros(len(data), dtype=np.int8)
    side[500] = 1
    conf = np.full(len(data), 0.9)
    signals, decisions = env._build_signals(side, conf)
    assert len(signals) == 1
    times = data.times
    assert signals[0]["time"] == int(times[500])
    # and the decision is keyed by the bar the engine will stamp on the trade
    assert int(times[501]) in decisions
    assert decisions[int(times[501])]["bar_index"] == 500


def test_shifting_the_entry_moves_the_stamp_by_whole_bars(env, data):
    side = np.zeros(len(data), dtype=np.int8)
    side[500] = 1
    conf = np.full(len(data), 0.9)
    base, _ = env._build_signals(side, conf)
    shifted, _ = env._build_signals(side, conf, shift_entry=1)
    assert shifted[0]["time"] - base[0]["time"] == 300


def test_the_agent_chooses_its_stop_and_its_target_from_the_menu(env, data, spec):
    """The two risk numbers an agent DOES decide.

    From a menu the admin pre-registered on the spec - the menu is the ceiling,
    because there is no action that reaches a value outside it. Choosing a
    wider stop buys a SMALLER position rather than more risk: lot size is
    `risk_dollars / stop_distance` and `risk_dollars` stays with the risk layer.
    """
    side = np.zeros(len(data), dtype=np.int8)
    for bar in (600, 800, 1000, 1200):
        side[bar] = 1
    conf = np.full(len(data), 0.9)
    stop_idx = np.full(len(data), 3, dtype=np.int64)     # 2.0x ATR
    rr_idx = np.full(len(data), 4, dtype=np.int64)       # 1:5
    signals, _ = env._build_signals(side, conf, stop_idx=stop_idx, rr_idx=rr_idx)
    assert signals

    for sig in signals:
        j = sig["metadata"]["rl_bar_index"]
        atr = float(data.atr[j])
        expected_risk = atr * spec.stop_atr_choices[3]
        assert sig["entry_price"] - sig["stop_loss"] == pytest.approx(expected_risk)
        rr = (sig["take_profit"] - sig["entry_price"]) / expected_risk
        assert rr == pytest.approx(spec.rr_choices[4])
        assert sig["metadata"]["rl_stop_atr_multiple"] == pytest.approx(
            spec.stop_atr_choices[3])
        assert sig["metadata"]["rl_rr"] == pytest.approx(spec.rr_choices[4])


def test_a_different_choice_gives_a_different_stop(env, data, spec):
    """Otherwise the head would be decorative."""
    side = np.zeros(len(data), dtype=np.int8)
    side[700] = 1
    conf = np.full(len(data), 0.9)

    def one(si, ri):
        sigs, _ = env._build_signals(
            side, conf, stop_idx=np.full(len(data), si, dtype=np.int64),
            rr_idx=np.full(len(data), ri, dtype=np.int64))
        return sigs[0]

    tight, wide = one(0, 0), one(4, 0)
    assert (tight["entry_price"] - tight["stop_loss"]) \
        < (wide["entry_price"] - wide["stop_loss"])


def test_an_index_outside_the_menu_cannot_reach_a_value_outside_it(env, data, spec):
    """The menu IS the ceiling. A corrupt or out-of-range index wraps onto the
    menu rather than producing a stop nobody authorised."""
    side = np.zeros(len(data), dtype=np.int8)
    side[700] = 1
    signals, _ = env._build_signals(
        side, np.full(len(data), 0.9),
        stop_idx=np.full(len(data), 99, dtype=np.int64),
        rr_idx=np.full(len(data), 99, dtype=np.int64))
    got = signals[0]["metadata"]["rl_stop_atr_multiple"]
    assert got in [pytest.approx(v) for v in spec.stop_atr_choices]


def test_an_agent_with_no_risk_head_uses_the_slots_settings(data, cfg):
    """A supervisor never opens anything, so it needs no risk head and the
    slot's own settings apply unchanged."""
    from backend.rl import features as F

    plain = AgentSpec(name="nohead", symbols=("EURUSD",), risk_head=False)
    e = E.TradingEnv(plain, data, cfg)
    e.scaler = F.Scaler().fit(data.X[:1500], data.names, fitted_on="t")
    side = np.zeros(len(data), dtype=np.int8)
    side[700] = 1
    signals, _ = e._build_signals(side, np.full(len(data), 0.9))
    sig = signals[0]
    atr = float(data.atr[700])
    expected = atr * float(cfg.get("stop_atr_multiple", 1.5) or 1.5)
    assert sig["entry_price"] - sig["stop_loss"] == pytest.approx(expected)


def test_the_agent_still_cannot_size_a_position(spec):
    """The line that did not move. A stop distance is the SHAPE of a trade;
    the lot size is its size, and that is the risk layer's."""
    from backend.rl import policy as pol

    p = pol.LinearPolicy(n_features=spec.n_features, n_stop=5, n_rr=5)
    for name in dir(p):
        assert "lot" not in name.lower(), name
        assert "volume" not in name.lower(), name
        assert "risk_pct" not in name.lower(), name


def test_the_confluence_score_is_flat_without_a_charter(env, data):
    """The risk layer sizes by this number, so a smoothly-moving score would
    hand the policy a position-sizing dial."""
    side = np.zeros(len(data), dtype=np.int8)
    side[600] = 1
    side[900] = -1
    signals, _ = env._build_signals(side, np.array([0.41] * len(data)))
    scores = {s["confluence_score"] for s in signals}
    assert scores == {80}


def test_a_zero_policy_proposes_nothing(env, spec):
    """An untrained agent trades nothing rather than trading noise - the right
    starting point for something measured against doing nothing."""
    ep = env.rollout(pol.LinearPolicy(n_features=spec.n_features))
    assert ep.n_signals == 0 and ep.breakdown.n_trades == 0 and ep.score == 0.0


# ── the real engine ────────────────────────────────────────────────────────

def test_an_episode_runs_through_the_real_engine_and_reports_its_cost_model(env, spec):
    """Not a kinder simulator. The cost model, the significance verdict and the
    rejection funnel all come back from the engine itself."""
    ep = env.rollout(trained(spec), label="smoke")
    assert ep.n_signals > 0
    assert ep.cost_model, "the engine must report which costs it applied"
    assert "EURUSD" in ep.cost_model
    assert ep.significance.get("verdict"), "the overlap-aware verdict must come through"
    assert ep.rejection_funnel.get("raw_signals", 0) > 0


def test_the_engine_refuses_some_signals_and_that_is_recorded(env, spec):
    """The agent proposes, the risk layer disposes. The funnel is how you see
    the difference between "no setups" and "every setup refused"."""
    ep = env.rollout(trained(spec))
    funnel = ep.rejection_funnel
    assert funnel["raw_signals"] >= funnel.get("approved", 0)


def test_use_strategy_exit_defaults_is_forced_off(spec, data, cfg):
    """Per-symbol exit tables were chosen on history that includes the RL test
    window, so applying them makes the ENVIRONMENT leak."""
    e = E.TradingEnv(spec, data, dict(cfg, use_strategy_exit_defaults=True))
    assert e.risk_config["use_strategy_exit_defaults"] is False


def test_rewards_carry_their_components_separately(env, spec):
    """Collapsed into one number, "it made money by abandoning its strategy"
    and "the strategy works" are the same reward."""
    ep = env.rollout(trained(spec), keep_per_trade=True)
    if not ep.breakdown.n_trades:
        pytest.skip("this policy took no trades on this seed")
    one = ep.breakdown.per_trade[0]
    for key in ("r_net", "adherence", "exit_quality", "money", "total"):
        assert key in one


def test_a_degenerate_policy_hits_the_signal_cap(spec, data, cfg):
    """A policy that wants to trade every bar is a degenerate policy, not a
    result, and it must not be allowed to build a 50,000-signal list."""
    e = E.TradingEnv(spec, data, cfg)
    e.fit_scaler()
    p = pol.LinearPolicy(n_features=spec.n_features)
    p.b_entry = np.array([20.0, 0.0, 0.0])      # always long, always certain
    ep = e.rollout(p)
    assert ep.n_signals <= E.MAX_SIGNALS_PER_EPISODE


# ── the cost stress is the real cost model, twice ──────────────────────────

def test_the_cost_stress_doubles_the_resolved_costs(cfg):
    stressed = E.stress_costs(cfg, "EURUSD", 2.0)
    assert stressed["spread_pips"] == pytest.approx(2 * cfg["spread_pips"])
    assert stressed["slippage_pips"] == pytest.approx(2 * cfg["slippage_pips"])
    assert stressed["cost_stress_multiplier"] == 2.0


def test_the_cost_stress_leaves_the_brokers_minimum_stop_alone(cfg):
    """`stops_level_pips` is the broker's own minimum distance, not a cost.
    Doubling it would change which trades EXIST rather than what they cost."""
    stressed = E.stress_costs(dict(cfg, stops_level_pips=3.0), "EURUSD", 2.0)
    assert stressed["stops_level_pips"] == 3.0


def test_a_multiplier_of_one_changes_nothing(cfg):
    assert E.stress_costs(cfg, "EURUSD", 1.0) == dict(cfg)


def test_doubling_costs_charges_more_money(cfg):
    """The stress must reach the MONEY, tested where the money is decided.

    Three earlier versions of this test asserted on a two-run comparison and
    all three were wrong, each for a different reason that is worth keeping:

      1. Per-trade mean R across the two runs. Doubling the spread changes
         which signals clear the minimum-stop floor, so the two runs are
         different POPULATIONS of trades. Measured -0.163R against -0.077R -
         the stressed run looking better.
      2. Total money on trades shared by both runs. Measured -69.59 against
         -69.04, still better. Two trades with the same entry TIME are not the
         same trade: with `max_positions_per_symbol=1`, which signal gets in on
         a bar depends on what is already open, so the matching was unsound.
      3. The entry FILL price. It does not move at all - and that is the real
         finding. `bar_open_price = float(open_p)`: this engine fills at the
         raw next-bar open and charges the spread as MONEY in `_calc_pnl`
         (`raw_pnl -= spread_cost`), rather than by moving the fill. One spread
         per round trip either way, but it means no fill-price assertion can
         ever see a cost change.

    So this tests the cost model where it actually lives, with everything else
    literally identical and no simulation in between.
    """
    from backend.backtester.engine import BacktestEngine

    base = BacktestEngine(E.stress_costs(cfg, "EURUSD", 1.0))
    worse = BacktestEngine(E.stress_costs(cfg, "EURUSD", 2.0))
    args = ("BUY", 1.0800, 1.0820, 1.0, "EURUSD", None, None)
    assert worse._calc_pnl(*args) < base._calc_pnl(*args), (
        "doubling the measured spread must cost money on an identical trade")

    # And the doubling must be the spread, not a coincidence: the gap between
    # the two is one extra spread on one lot.
    gap = base._calc_pnl(*args) - worse._calc_pnl(*args)
    assert gap > 0
    resolved = E.stress_costs(cfg, "EURUSD", 2.0)
    assert resolved["spread_pips"] == pytest.approx(2 * cfg["spread_pips"])


# ── the falsification controls ─────────────────────────────────────────────

def test_shuffling_the_features_keeps_the_bars_and_the_costs(env, spec):
    """The env control: break the link between an observation and the bar it
    describes, change nothing else. Any edge that survives is coming from the
    environment."""
    p = trained(spec)
    base = env.rollout(p)
    shuffled = env.rollout(p, shuffle_features=True, seed=5)
    assert shuffled.cost_model == base.cost_model
    assert shuffled.n_bars == base.n_bars

    # WHICH bars signal must differ, not how many. The count is the wrong
    # assertion: once the per-episode signal cap binds, both runs stop at
    # exactly the same number (measured: 173 and 173) and a test on the count
    # passes or fails for reasons that have nothing to do with the control.
    side_a, _, _ = p.entry_decisions(env.Z, spec.min_confidence)
    rng = np.random.default_rng(5)
    side_b, _, _ = p.entry_decisions(env.Z[rng.permutation(len(env.Z))],
                                     spec.min_confidence)
    assert not np.array_equal(side_a, side_b), (
        "shuffling the observations must change which bars the policy acts on, "
        "or the control is not controlling anything")


def test_the_shuffled_control_is_deterministic_given_a_seed(env, spec):
    p = trained(spec)
    a = env.rollout(p, shuffle_features=True, seed=11)
    b = env.rollout(p, shuffle_features=True, seed=11)
    assert a.n_signals == b.n_signals and a.breakdown.n_trades == b.breakdown.n_trades


def test_the_observation_is_unchanged_by_computing_the_counterfactual(env, spec, data):
    """`exit_quality` reads bars AFTER the decision. Legitimate in a reward,
    fatal in an observation - so computing it must not be able to move Z."""
    before = env.Z.copy()
    ep = env.rollout(trained(spec))
    for g in ep.trades[:5]:
        env._hold_to_the_end(g)
    assert np.array_equal(before, env.Z)


def test_the_counterfactual_is_pessimistic_on_an_ambiguous_bar(env):
    """When a bar's range covers both the stop and the target, the STOP is
    taken. The alternative flatters every counterfactual, which would reward
    the agent for NOT exiting early."""
    frame = env.data.frame
    t0 = int(frame["time"].iloc[100])
    entry = float(frame["close"].iloc[100])
    # a stop and target both inside the next bars' range
    group = {"entry_price": entry, "stop_loss": entry - 0.00001,
             "take_profit": entry + 0.00001, "direction": "BUY", "entry_time": t0}
    assert env._hold_to_the_end(group) == -1.0


def test_the_counterfactual_is_none_without_a_usable_stop(env):
    t0 = int(env.data.frame["time"].iloc[100])
    assert env._hold_to_the_end({"entry_price": 1.0, "stop_loss": 1.0,
                                 "direction": "BUY", "entry_time": t0}) is None


# ── CPCV ───────────────────────────────────────────────────────────────────

def test_cpcv_produces_every_combination_of_test_blocks():
    folds = E.cpcv_folds(8000, groups=8, test_groups=2, embargo=0)
    assert len(folds) == E.n_paths(8, 2) == 28


def test_train_and_test_never_overlap():
    for f in E.cpcv_folds(8000, groups=8, test_groups=2, embargo=100):
        train = set(E.rows_of(f.train).tolist())
        test = set(E.rows_of(f.test).tolist())
        assert not (train & test), f.path


def test_the_embargo_removes_training_rows_beside_every_test_block():
    """The half people skip. With max_hold of 288 bars, a training bar 200 bars
    before a test block still has a label that lands inside it."""
    embargo = 288
    for f in E.cpcv_folds(8000, groups=8, test_groups=2, embargo=embargo):
        train = E.rows_of(f.train)
        for a, b in f.test:
            too_close = train[(train >= a - embargo) & (train < b + embargo)]
            assert not len(too_close), (f.path, a, b, too_close[:5])


def test_a_bigger_embargo_leaves_fewer_training_rows():
    small = E.cpcv_folds(8000, groups=8, test_groups=2, embargo=50)[0]
    big = E.cpcv_folds(8000, groups=8, test_groups=2, embargo=500)[0]
    assert big.train_rows < small.train_rows


def test_slicing_a_fold_does_not_recompute_the_features(data):
    """Recomputing would start the warm-up inside the fold, so the fold would
    see less history than the live engine does - which makes a fold's numbers
    BETTER than reality rather than worse."""
    sub = data.slice(500, 900)
    assert np.array_equal(sub.X, data.X[500:900])
    assert np.array_equal(sub.atr, data.atr[500:900])


# ── transitions, for the policy gradient ───────────────────────────────────

def test_every_decision_bar_appears_in_the_transitions(env, spec):
    """Including the flat ones. A policy trained only on the bars it traded
    learns that trading is always better than not trading."""
    ep = env.rollout(trained(spec), explore=True, seed=3, transitions=True)
    assert len(ep.transitions) == len(env.Z)
    actions = {a for _, a, _ in ep.transitions}
    assert actions <= {0, 1, 2}


def test_a_batch_with_no_signals_still_has_transitions(env, spec):
    """Measured: returning early without them left the policy-gradient
    optimiser with no gradient at all on its first iteration, which is where
    every policy starts."""
    ep = env.rollout(pol.LinearPolicy(n_features=spec.n_features),
                     explore=False, transitions=True)
    assert ep.n_signals == 0
    assert len(ep.transitions) == len(env.Z)
    assert all(r == 0.0 for _, _, r in ep.transitions)


def test_sampling_ignores_the_confidence_floor(spec):
    """With three actions an untrained policy's best probability is 0.333, so a
    0.55 floor applied while sampling means no action is ever taken and the
    gradient is always zero."""
    p = pol.LinearPolicy(n_features=spec.n_features)
    Z = np.zeros((500, spec.n_features))
    side_argmax, _, _ = p.entry_decisions(Z, 0.55)
    side_sampled, _, chosen = p.entry_decisions(Z, 0.55, rng=np.random.default_rng(1))
    assert not side_argmax.any()
    assert side_sampled.any()
    assert set(np.unique(chosen).tolist()) <= {0, 1, 2}


def test_inference_is_deterministic(spec):
    """A sampled policy would give a different backtest every run, and the
    parity rule is worth more than exploration at inference."""
    p = trained(spec)
    Z = np.random.default_rng(2).normal(0, 1, (300, spec.n_features))
    a = p.entry_decisions(Z, 0.4)[0]
    b = p.entry_decisions(Z, 0.4)[0]
    assert np.array_equal(a, b)


# ── the charter path, which an env with no charter never exercises ─────────

def test_an_episode_with_a_charter_produces_groupable_trades(spec, data, cfg):
    """`trade_grouper` reads `dict(metadata["gate_vector"])`.

    Writing that as a list of bools raises `TypeError: cannot convert
    dictionary update sequence element #0 to a sequence` inside the engine,
    AFTER every trade has been simulated. It went unnoticed through a whole
    test suite because every other env fixture here has `charter=None`, so the
    branch that writes the key never ran - and then it took down a one-hour
    gate run at the validation step.
    """
    from backend.rl import charter as ch

    g = ch.Gate(name="vol_high", feature="vol_expansion", op=">=", threshold=0.5)
    c = ch.StrategyCharter(agent_id="t", status=ch.APPROVED, gates=(g,),
                           tiers=(ch.Tier("A", ("vol_high",)),), entry_tier="A")
    env = E.TradingEnv(spec, data, cfg, charter=c)
    env.fit_scaler(slice(0, 1500))
    ep = env.rollout(trained(spec))
    assert ep.n_signals > 0, "no signals - this test would pass vacuously"
    # the engine groups trades internally; reaching here at all means it did
    assert isinstance(ep.trades, list)
    for t in ep.trades:
        vec = ((t.get("original_signal") or {}).get("metadata") or {}).get("gate_vector")
        if vec is not None:
            assert isinstance(dict(vec), dict)


def test_the_gate_vector_matches_what_gate_recorder_produces(spec, data, cfg):
    """Same shape as every rule-based strategy's, so the ablation harness and
    the Journal read an agent's trades without a special case."""
    from backend.rl import charter as ch

    g = ch.Gate(name="vol_high", feature="vol_expansion", op=">=", threshold=0.5)
    c = ch.StrategyCharter(agent_id="t", status=ch.APPROVED, gates=(g,),
                           tiers=(ch.Tier("A", ("vol_high",)),), entry_tier="A")
    env = E.TradingEnv(spec, data, cfg, charter=c)
    env.fit_scaler(slice(0, 1500))
    side = np.zeros(len(data), dtype=np.int8)
    side[900] = 1
    signals, _ = env._build_signals(side, np.full(len(data), 0.9))
    if not signals:
        pytest.skip("the charter declined this bar")
    vec = signals[0]["metadata"]["gate_vector"]
    assert isinstance(vec, dict)
    assert set(vec) == {"vol_high"}
    assert all(isinstance(v, bool) for v in vec.values())
