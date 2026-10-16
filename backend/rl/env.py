"""
backend/rl/env.py

The environment. It WRAPS the existing backtester - it does not replace it.

THE DECISION THIS FILE IS
-------------------------
A fresh simulator would not have the lambda spike fills for Boom and Crash, the
measured per-symbol spreads and swaps, the minimum-stop floor that refuses a
stop inside 2x the spread, next-bar-open fills, or the per-symbol cost model
recorded on every run. An agent trained against a kinder simulator produces a
number that dies on contact with the app. This project has measured that
directly: a naive bar model faked +0.46R on RANDOM entries in jump markets, and
8 of 15 paired cells flip SIGN between Deriv and FundedNext on the same rule.

So `BacktestEngine` runs the episode, and this module only decides what to
propose to it.

HOW ONE EPISODE WORKS, AND WHY IT IS NOT CIRCULAR
-------------------------------------------------
    1. features        X[j] from bars up to and including j   (causal, probed)
    2. scale           Z = scaler.transform(X)                (fold-fitted)
    3. entry head      side[j] in {long, flat, short}         (vectorised)
    4. signals         stamped at t[j], so the engine fills at bar j+1's open
    5. BacktestEngine  the real one: fills, costs, stops, targets, risk gates
    6. exit head       called by the engine through `on_position_bar`
    7. reward          per closed trade, components kept apart

Step 3 can run before step 5 because the ENTRY head observes market features
only. Position state belongs to the exit head, and the exit head is called from
inside the engine, which is where the position actually exists. That is not a
convenience - it is the same separation section 4.3 requires, and it is what
lets one pass of the real engine produce a complete, correct episode.

The entry bar stamp is worth stating precisely, because this project has the
scar: a route that stamped the signal with bar `i` instead of bar `i-1` made
every entry fill one bar late and cost 10R over 152 GBPJPY trades. Features at
index `j` are computed from bars up to `j`, the signal is stamped `t[j]`, and
the engine fills at the first bar whose open is after `t[j]` - bar `j+1`. Same
timing as live.

THE TWO FALSIFICATION CONTROLS LIVE HERE
----------------------------------------
`shuffle_features` breaks the link between an observation and the bar it
describes, keeping the bars, the fills and the costs exactly as they are. A
leak-free pipeline must then land at break-even minus costs; anything that
makes money is reading the future through the ENVIRONMENT rather than through
the features, which the prefix probe cannot see.

`shift_entry` delays every entry by N bars. A genuine edge degrades; a leak
collapses.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from backend.rl import exit_policy as xp
from backend.rl import features as feat
from backend.rl import policy as pol
from backend.rl import reward as rw
from backend.rl.spec import AgentSpec
from backend.strategies.base_strategy import BaseStrategy, TradeAction
from backend.utils.logger import get_logger

logger = get_logger(__name__)

# The engine's own runaway guard. Mirrored here so a degenerate policy that
# wants to trade every bar is stopped before it builds a 50,000-signal list.
MAX_SIGNALS_PER_EPISODE = 5000


# -- the data an episode runs on --------------------------------------------


@dataclass
class EpisodeData:
    """One symbol's bars, and the raw features over them.

    The features are computed ONCE per dataset rather than per policy
    evaluation: a population optimiser evaluates hundreds of policies on the
    same bars, and recomputing 26 rolling features each time is most of the
    runtime for none of the benefit. They are raw (unscaled) because the scaler
    belongs to the fold, not to the data.
    """
    symbol: str
    timeframe: str
    frame: pd.DataFrame
    X: np.ndarray
    names: tuple[str, ...]
    atr: np.ndarray = field(default_factory=lambda: np.zeros(0))
    broker: str = ""
    # MODE B only: what the base strategy found on these bars, keyed by the bar
    # index it was found on. Generated ONCE here rather than per rollout -
    # the setups do not depend on the policy, and a population optimiser
    # evaluates hundreds of policies against the same ones.
    base_signals: dict = field(default_factory=dict)
    base_strategy: str = ""

    @classmethod
    def build(cls, symbol: str, timeframe: str, frame: pd.DataFrame,
              names=None, *, broker: str = "", base_strategy: str = "",
              config=None, frames_by_tf: dict | None = None) -> "EpisodeData":
        frame = frame.copy()
        if "time" not in frame.columns:
            frame["time"] = pd.DatetimeIndex(frame.index).as_unit("s").asi8
        names = tuple(names or feat.DEFAULT_FEATURES)
        data = cls(symbol=symbol, timeframe=timeframe, frame=frame,
                   X=feat.matrix(frame, names), names=names,
                   atr=feat.atr(frame, 14).to_numpy(), broker=broker,
                   base_strategy=base_strategy)
        if base_strategy:
            from backend.rl import base_signals as bs

            frames = bs.index_frames(frames_by_tf or {timeframe: frame})
            data.base_signals = bs.by_bar(
                bs.generate(symbol, timeframe, frames, base_strategy, config))
        return data

    def __len__(self) -> int:
        return len(self.frame)

    @property
    def times(self) -> np.ndarray:
        return self.frame["time"].to_numpy(dtype=np.int64)

    def coverage(self) -> dict:
        """The same record `datasets.Dataset.coverage` writes - symbol,
        timeframe, first and last bar, and the bars' content hash - which
        `trained_on_json` stores and the overlap check reads.

        The distil route called this on an EpisodeData, which never had it, so
        every Distil failed with AttributeError once it got past loading bars
        (found by the end-to-end audit on 2026-10-16)."""
        from backend.rl.datasets import content_hash

        t = self.times
        return {"symbol": self.symbol, "timeframe": self.timeframe,
                "start": int(t[0]) if len(t) else 0, "end": int(t[-1]) if len(t) else 0,
                "hash": content_hash(self.frame)}

    def slice(self, start: int, end: int) -> "EpisodeData":
        """A fold. The features are SLICED, not recomputed.

        That is deliberate and it is the conservative direction: a feature
        recomputed on a fold would start its warm-up inside the fold and see
        less history than the live engine does, which would make the fold's
        numbers better than reality rather than worse. Slicing keeps each bar's
        observation identical to the one the full run produced, and every one
        of those is prefix-stable by `leakguard`.
        """
        start, end = max(0, int(start)), min(len(self), int(end))
        # The base signals are RE-INDEXED onto the slice, not regenerated. The
        # strategy saw what it saw; re-running it on a fold would start its
        # warm-up inside the fold and find different setups, which would make
        # the agent and its own baseline disagree about what the setups were.
        rebased = {j - start: dict(sig, bar_index=j - start)
                   for j, sig in (self.base_signals or {}).items()
                   if start <= j < end}
        return EpisodeData(symbol=self.symbol, timeframe=self.timeframe,
                           frame=self.frame.iloc[start:end].copy(),
                           X=self.X[start:end].copy(), names=self.names,
                           atr=self.atr[start:end].copy(), broker=self.broker,
                           base_signals=rebased, base_strategy=self.base_strategy)


# -- the exit-head strategy the engine calls --------------------------------


class RLPositionStrategy(BaseStrategy):
    """A `BaseStrategy` whose only job is to answer `on_position_bar`.

    The engine takes a `strategy` argument purely to call that hook, so the
    exit head reaches the real simulation through the path three shipped
    strategies already use. `on_bar` is never called here - the entry decisions
    were made before the engine started.
    """

    LIVE_POSITION_EXITS = True
    POSITION_BAR_WINDOW = 60

    def __init__(self, config: Any, *, policy: pol.LinearPolicy, Z: np.ndarray,
                 times: np.ndarray, atr: np.ndarray, spec: AgentSpec,
                 charter=None, enabled: bool = True, reason: str = xp.RL_EXIT,
                 owner: BaseStrategy | None = None):
        super().__init__(config)
        self.policy = policy
        self.Z = Z
        self._times = np.asarray(times, dtype=np.int64)
        self._atr = np.asarray(atr, dtype=float)
        self.spec = spec
        self.charter = charter
        self.enabled = bool(enabled)
        self.reason = reason
        # When set, this instance is a SUPERVISOR: the owner is asked first and
        # its answer wins (plan 4.9 precedence rule 2).
        self.owner = owner
        self.decisions: list[dict] = []
        self.is_backtesting = True

    def get_required_timeframes(self) -> list[str]:
        return [self.spec.timeframe]

    async def on_bar(self, symbol, timeframe, candles):
        return None

    # -- the hook -----------------------------------------------------------

    def on_position_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame,
                        position: dict) -> TradeAction | None:
        owner_action = None
        if self.owner is not None and hasattr(self.owner, "on_position_bar"):
            try:
                owner_action = self.owner.on_position_bar(symbol, timeframe, candles, position)
            except Exception as exc:                      # noqa: BLE001
                logger.debug(f"[RL] owner on_position_bar raised: {exc}")
        if owner_action is not None:
            return owner_action           # rule 2: the owner's measured exit wins
        if not self.enabled or self.policy is None:
            return None

        d = self._decide(candles, position)
        if d is None or not d.acts:
            return None
        ticket = int(position.get("ticket") or position.get("id") or 0)
        action = d.to_trade_action(ticket)
        if action is None:
            return None
        if action.action == "MODIFY_SL" and not xp.tightens(
                action.new_sl, position.get("stop_loss"), position.get("direction", "BUY")):
            return None
        self.decisions.append({
            "time": int(self._bar_time(candles)), "symbol": symbol, **d.to_dict(),
            "group_id": position.get("group_id"),
        })
        return action

    def on_foreign_position_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame,
                                position: dict, owner: Any = None) -> TradeAction | None:
        """Supervising a position another strategy opened.

        `owner` carries the opening strategy's id and its entry gate vector AS
        RECORDED AT ENTRY - `GateRecorder` already persists `passed_gates`,
        `gate_vector` and `confluence_tags` on the trade, and re-deriving them
        here would be a lookahead, because they would be computed from the
        charter and features as they are NOW.
        """
        if not self.enabled or self.policy is None:
            return None
        if not self.spec.may_supervise(str((owner or {}).get("strategy_id", "")), symbol):
            return None
        d = self._decide(candles, position)
        if d is None or not d.acts:
            return None
        ticket = int(position.get("ticket") or position.get("id") or 0)
        action = xp.supervise(None, d, position, ticket)
        if action is None:
            return None
        self.decisions.append({
            "time": int(self._bar_time(candles)), "symbol": symbol, "foreign": True,
            "owner": str((owner or {}).get("strategy_id", "")), **d.to_dict(),
            "group_id": position.get("group_id"),
        })
        return action

    # -- internals ----------------------------------------------------------

    def _bar_time(self, candles: pd.DataFrame) -> int:
        from backend.strategies.core.daily_atr import last_epoch
        return last_epoch(candles)

    def _index_of(self, bar_time: int) -> int | None:
        """Which row of `Z` this bar is.

        A search rather than a counter: the engine hands this hook a SLICE, and
        a counter would drift the moment a bar produced no call (no open
        position) - which is most bars. Getting this wrong would feed the exit
        head another bar's observation, which is a lookahead when that bar is
        later.
        """
        k = int(np.searchsorted(self._times, int(bar_time), side="left"))
        if k >= len(self._times) or self._times[k] != int(bar_time):
            return None
        return k

    def _decide(self, candles: pd.DataFrame, position: dict) -> xp.ExitDecision | None:
        bar_time = self._bar_time(candles)
        k = self._index_of(bar_time)
        if k is None or k >= len(self.Z):
            return None
        price = float(candles["close"].iloc[-1])
        entry_time = position.get("entry_time")
        bars_held = 0
        if entry_time is not None:
            try:
                ts = int(pd.Timestamp(entry_time).timestamp()) if not isinstance(
                    entry_time, (int, float)) else int(entry_time)
                j = int(np.searchsorted(self._times, ts, side="left"))
                bars_held = max(0, k - j)
            except Exception:
                bars_held = 0
        atr_value = float(self._atr[k]) if k < len(self._atr) else None
        return xp.decide(
            self.policy, self.Z[k], position, price=price, bars_held=bars_held,
            max_hold_bars=self.spec.max_hold_bars, min_confidence=self.spec.min_confidence,
            atr_value=atr_value, charter=self.charter, reason=self.reason,
        )


# -- what one episode produced ----------------------------------------------


@dataclass
class Episode:
    """One run's result, with the pieces a report needs kept separate."""
    symbol: str
    n_bars: int = 0
    n_signals: int = 0
    rewards: list = field(default_factory=list)
    breakdown: rw.RewardBreakdown = field(default_factory=rw.RewardBreakdown)
    trades: list = field(default_factory=list)
    exit_decisions: list = field(default_factory=list)
    cost_model: dict = field(default_factory=dict)
    cost_drag_r: float = 0.0
    significance: dict = field(default_factory=dict)
    final_balance: float = 0.0
    initial_balance: float = 0.0
    rejection_funnel: dict = field(default_factory=dict)
    # (bar index, action index, reward) per DECISION bar - what a policy-gradient
    # optimiser needs and what a population method ignores. Action index is into
    # `policy.ENTRY_ACTIONS`, so FLAT bars are present with reward 0: a policy
    # that learns nothing from the bars it declined to trade would only ever
    # learn to trade more.
    transitions: list = field(default_factory=list)
    start_time: int = 0
    end_time: int = 0
    label: str = ""

    @property
    def score(self) -> float:
        """What the optimiser maximises: the decomposed reward's total."""
        return self.breakdown.total

    @property
    def mean_r(self) -> float:
        return self.breakdown.mean_r

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol, "label": self.label, "n_bars": self.n_bars,
            "n_signals": self.n_signals, "n_trades": self.breakdown.n_trades,
            "score": round(self.score, 6), "reward": self.breakdown.to_dict(),
            "cost_drag_r": round(self.cost_drag_r, 6),
            "cost_model": self.cost_model, "significance": self.significance,
            "initial_balance": self.initial_balance, "final_balance": self.final_balance,
            "exit_decisions": len(self.exit_decisions),
            "start_time": self.start_time, "end_time": self.end_time,
        }


# -- the environment --------------------------------------------------------


class TradingEnv:
    """One symbol, one timeframe, the real engine.

    Not a Gymnasium env, and the docstring says so rather than pretending: the
    engine is a batch simulator, so an episode is a trajectory produced in one
    pass rather than a per-step `step()` loop. `train.py` optimises over whole
    episodes, which is what a population method needs anyway. The PPO path
    feeds these same trajectories to `stable_baselines3` as on-policy batches
    when torch is installed - the environment does not change, only the
    optimiser does.
    """

    def __init__(self, spec: AgentSpec, data: EpisodeData, risk_config: dict,
                 *, scaler: feat.Scaler | None = None, charter=None,
                 initial_balance: float = 10_000.0):
        self.spec = spec
        self.data = data
        self.risk_config = dict(risk_config or {})
        # RL evaluation must not inherit per-symbol exit tables that were
        # chosen on history including the test window (plan 4.11). Forced here
        # rather than left to the caller, and recorded on the episode.
        self.risk_config["use_strategy_exit_defaults"] = False
        # This IS a backtest. `RiskEngine` and `CircuitBreaker` both read this
        # flag, and without it a training run gets the live breaker's behaviour
        # - including its state file - on a simulated account.
        self.risk_config.setdefault("is_backtest", True)
        self.charter = charter
        self.initial_balance = float(initial_balance)
        self.scaler = scaler
        # The frozen direction model, when this agent has one. Fitted by
        # `fit_scaler` on the SAME training rows, because a director fitted on
        # anything else is the leak this whole arrangement exists to avoid.
        self.director = None
        self._Z: np.ndarray | None = None

    # -- scaling ------------------------------------------------------------

    def fit_scaler(self, train_slice: slice | None = None, *, label: str = "") -> feat.Scaler:
        """Fit on the TRAINING FOLD and keep it. The leak this closes is the one
        that looks most innocent in a diff."""
        X = self.data.X if train_slice is None else self.data.X[train_slice]
        self.scaler = feat.Scaler().fit(
            X, self.data.names,
            fitted_on=label or f"{self.data.symbol} rows "
                               f"{0 if train_slice is None else train_slice.start}-"
                               f"{len(self.data.X) if train_slice is None else train_slice.stop}")
        self._Z = None
        if self.spec.director:
            self.fit_director(train_slice, label=label)
        return self.scaler

    def fit_director(self, train_slice: slice | None = None, *, label: str = "",
                     rows: np.ndarray | None = None):
        """Fit the frozen direction model on the SAME training rows.

        Same rows as the scaler, and that is not a convenience: a director
        fitted on a wider span than the policy's training fold puts the test
        fold's future into every observation the policy ever sees, which no
        probe over the features can detect because the features are innocent.
        """
        from backend.rl.director import Director

        if rows is None:
            start = 0 if train_slice is None else int(train_slice.start or 0)
            stop = len(self.data.X) if train_slice is None else int(train_slice.stop)
            rows = np.arange(start, stop, dtype=np.int64)
        base = self.scaler.transform(self.data.X)
        close = self.data.frame["close"].to_numpy(dtype=float)
        self.director = Director(horizon=int(self.spec.director_horizon)).fit(
            base, close, self.data.atr, rows, names=self.data.names,
            fitted_on=label or f"rows {rows[0]}-{rows[-1]}")
        self._Z = None
        return self.director

    def _adopt_director(self, policy: pol.LinearPolicy) -> None:
        """Use the director the POLICY was trained against.

        The policy's input width includes the director's two columns, so the
        two cannot be separated: every env that runs a director policy - the
        trainer's windows, its evaluation, the test fold, the gate, distill -
        has to see the same director. Taking it from the policy means none of
        them can forget to, which is how every director run used to die.
        """
        model = getattr(policy, "director_model", None)
        d = model() if callable(model) else None
        if d is None:
            return
        if self.director is None or self.director.content_hash != d.content_hash:
            self.director = d
            self._Z = None

    def observations_for(self, policy: pol.LinearPolicy) -> np.ndarray:
        """`Z` as THIS policy sees it - with its director's columns when it has one.

        Use this, not `.Z`, anywhere a policy is applied to observations
        outside `rollout` (charter fidelity, distillation). A fresh env's `.Z`
        has no director until a rollout adopts one, so the gate's fidelity
        check fed a director policy 25 columns instead of 27 and died on the
        matmul (end-to-end audit, 2026-10-16).
        """
        self._adopt_director(policy)
        return self.Z

    @property
    def Z(self) -> np.ndarray:
        if self._Z is None:
            if self.scaler is None or not self.scaler.fitted:
                raise RuntimeError(
                    "the scaler has not been fitted on a training fold - fit it before "
                    "running an episode, or the observation carries the test fold's scale")
            from backend.rl.director import augment

            # The director's call and confidence ride ALONGSIDE every market
            # feature rather than replacing any, so the policy can learn where
            # to believe it - which is the point of a controller on top of a
            # predictor rather than simply trading the predictor's output.
            self._Z = augment(self.scaler.transform(self.data.X), self.director,
                              self.spec.min_confidence)
        return self._Z

    # -- the episode --------------------------------------------------------

    def rollout(self, policy: pol.LinearPolicy, *, exit_head: bool = True,
                shuffle_features: bool = False, shift_entry: int = 0,
                cost_multiplier: float = 1.0, seed: int = 0,
                label: str = "", keep_per_trade: bool = False,
                explore: bool = False, transitions: bool = False) -> Episode:
        """Run one episode through the real engine and score it.

        `cost_multiplier` is the 2x cost-stress run from the validation gate. It
        multiplies the engine's own measured spread and slippage rather than
        inventing new numbers, so the stressed run is the same cost model twice
        as expensive, not a different cost model.
        """
        from backend.backtester.engine import BacktestEngine

        data = self.data
        n = len(data)
        ep = Episode(symbol=data.symbol, n_bars=n, label=label,
                     initial_balance=self.initial_balance)
        if n < 60:
            return ep

        self._adopt_director(policy)
        Z = self.Z
        if shuffle_features:
            # The env control. Permute WHICH bar each observation describes and
            # change nothing else: same bars, same fills, same costs, same
            # risk gates. Any edge that survives is coming from the
            # environment, not from the features.
            rng = np.random.default_rng(seed or 1)
            Z = Z[rng.permutation(len(Z))]

        rng = np.random.default_rng(seed or 1) if explore else None
        side, conf, chosen = policy.entry_decisions(
            Z, self.spec.min_confidence, rng=rng)
        # The two risk numbers the agent chooses: where its stop goes and what
        # it is aiming at. Indices into the menu the admin pre-registered - a
        # value outside that menu is not expressible.
        stop_idx, rr_idx = policy.risk_choices(Z, rng=rng)
        signals, decisions = self._build_signals(
            side, conf, shift_entry=shift_entry, stop_idx=stop_idx, rr_idx=rr_idx)
        ep.n_signals = len(signals)
        if not signals:
            # Still record the decisions. A batch of nothing but FLAT carries
            # real information - "doing nothing scored zero here" - and
            # returning early without it left a policy-gradient optimiser with
            # no gradient at all on its first iteration, which is where every
            # policy starts.
            if transitions:
                ep.transitions = [(j, int(a), 0.0) for j, a in enumerate(chosen)]
            return ep

        risk_config = stress_costs(self.risk_config, data.symbol, cost_multiplier)

        engine = BacktestEngine(risk_config)
        # Trades are scored, never drawn: skip the trade viewer's chart slices.
        engine.group_chart_data = False
        strategy = RLPositionStrategy(
            config=risk_config, policy=policy, Z=Z, times=data.times, atr=data.atr,
            spec=self.spec, charter=self.charter, enabled=bool(exit_head and self.spec.exit_head),
        )
        results = engine.run(
            data.frame, signals, self.initial_balance, None, None, strategy, None, None)

        ep.trades = results.get("grouped_trades") or []
        ep.exit_decisions = strategy.decisions
        ep.cost_model = results.get("cost_model") or {}
        ep.significance = results.get("significance") or {}
        ep.rejection_funnel = results.get("rejection_funnel") or {}
        ep.final_balance = float(results.get("final_balance") or self.initial_balance)
        times = data.times
        ep.start_time, ep.end_time = int(times[0]), int(times[-1])

        ep.rewards = self._score_trades(ep.trades, decisions)
        ep.breakdown = rw.aggregate(ep.rewards, keep_per_trade=keep_per_trade)
        ep.cost_drag_r = self._cost_drag(ep.trades)
        if transitions:
            ep.transitions = self._transitions(chosen, decisions, ep)
        return ep

    def _transitions(self, chosen: np.ndarray, decisions: dict, ep: Episode) -> list:
        """Credit each decision bar with what that decision earned.

        A bar whose signal the RISK LAYER refused scores zero, not negative.
        The agent proposed and the risk layer disposed; training it to avoid
        refusals would be training it to model the drawdown caps, which is the
        one thing section 4.3 keeps out of its observation on purpose.
        """
        by_bar: dict[int, float] = {}
        for g, r in zip(ep.trades, ep.rewards):
            bar = ((g.get("original_signal") or {}).get("metadata") or {}).get("rl_bar_index")
            if bar is None:
                bar = (decisions.get(self._epoch(g.get("entry_time"))) or {}).get("bar_index")
            if bar is not None:
                by_bar[int(bar)] = by_bar.get(int(bar), 0.0) + r.total
        from backend.rl.reward import penalty_total

        penalised = getattr(self, "_penalised_bars", {}) or {}
        out = []
        for j, a in enumerate(chosen):
            reward = float(by_bar.get(j, 0.0))
            if j in penalised:
                reward -= penalty_total(penalised[j])
            out.append((j, int(a), reward))
        return out

    # -- signal construction ------------------------------------------------

    def _build_signals(self, side: np.ndarray, conf: np.ndarray, *, shift_entry: int = 0,
                       stop_idx=None, rr_idx=None
                       ) -> tuple[list[dict], dict[int, dict]]:  # noqa: C901
        """Turn per-bar decisions into the engine's signal list.

        The stop and the target are NOT the policy's. They come from the risk
        config - an ATR multiple for the stop and `min_rr` for the target -
        exactly as every other strategy builds them, so the agent proposes a
        direction and the risk layer disposes of everything that costs money.
        """
        data = self.data
        times = data.times
        closes = data.frame["close"].to_numpy(dtype=float)
        atr = data.atr
        # The slot's settings are the FALLBACK, used when this agent has no
        # risk head (a supervisor never opens anything, so it needs none).
        default_stop = float(self.risk_config.get("stop_atr_multiple", 1.5) or 1.5)
        default_rr = float(self.risk_config.get("min_rr", 2.0) or 2.0)
        stop_menu = tuple(self.spec.stop_atr_choices or (default_stop,))
        rr_menu = tuple(self.spec.rr_choices or (default_rr,))
        uses_risk_head = bool(self.spec.risk_head and stop_idx is not None
                              and rr_idx is not None)
        # The 0-100 confluence score the risk layer reads. Deliberately NOT the
        # policy's continuous confidence: `confluence_risk_tiers` sizes
        # positions by this number, so letting the policy move it smoothly
        # would hand the agent a position-sizing dial through the back door -
        # exactly what section 4.3 says it must not have. With no charter it is
        # the flat 80 every measured rule-based strategy in the book emits;
        # with a charter it is the APPROVED tier, which the admin set.
        tier_scores = {"A": 90, "B": 80, "C": 70}
        flat_score = int(self.risk_config.get("rl_confluence_score", 80) or 80)
        warmup = max(60, int(self.risk_config.get("rl_warmup_bars", 300) or 300))

        signals: list[dict] = []
        decisions: dict[int, dict] = {}
        # Bars where the policy wanted a trade the APPROVED charter does not
        # describe. The trade is NOT taken - an unapproved setup must not reach
        # the market - but the DECISION is charged, so the policy learns not to
        # want it. Dropping it silently would leave the agent free to keep
        # proposing trades nobody signed off, at no cost.
        self._penalised_bars: dict[int, tuple] = {}
        names = list(data.names)
        charter = self.charter
        Zr = self.Z

        last_bar = -10_000
        min_gap = int(self.risk_config.get("min_bars_between_entries", 0) or 0)
        cap = self._signal_cap(len(side), times)

        # MODE B: the base strategy decides WHERE the setups are; the agent
        # decides which to take. So the candidate bars are its signals, not
        # every bar, and the direction is the strategy's rather than the
        # policy's - the agent is optimising the rule, not replacing it.
        base = data.base_signals or {}
        mode_b = bool(self.spec.base_strategy and base)
        candidates = sorted(base) if mode_b else range(warmup, len(side) - 1)

        for j in candidates:
            if j < warmup or j >= len(side) - 1:
                continue
            s = int(side[j])
            if s == 0:
                continue
            if mode_b:
                # The agent must actively AGREE with the setup to take it.
                # Disagreeing is a skip, not a reversal: in mode B the
                # strategy owns the direction and the agent owns the
                # selection, the trade shape and the exit.
                base_sig = base[j]
                base_side = 1 if str(base_sig.get("direction", "BUY")).upper() == "BUY" else -1
                if s != base_side:
                    continue
            if min_gap and (j - last_bar) < min_gap:
                continue
            k = j + max(0, int(shift_entry))
            if k >= len(times) - 1:
                continue
            a = float(atr[j])
            if not np.isfinite(a) or a <= 0:
                continue
            direction = "BUY" if s > 0 else "SELL"
            entry = float(closes[j])
            if uses_risk_head:
                stop_mult = float(stop_menu[int(stop_idx[j]) % len(stop_menu)])
                rr = float(rr_menu[int(rr_idx[j]) % len(rr_menu)])
            else:
                stop_mult, rr = default_stop, default_rr
            risk = a * stop_mult
            stop = entry - risk if s > 0 else entry + risk
            target = entry + rr * risk if s > 0 else entry - rr * risk

            meta: dict[str, Any] = {"rl_confidence": round(float(conf[j]), 4),
                                    "rl_bar_index": j,
                                    # The complete statement of what the agent
                                    # decided about risk on this trade. The lot
                                    # size that follows is the risk layer's.
                                    "rl_stop_atr_multiple": round(stop_mult, 4),
                                    "rl_rr": round(rr, 4)}
            if charter is not None:
                from backend.rl.adherence import observation_dict, score_decision
                # Market columns only - see `observation_dict`'s callers in
                # exit_policy: the director's two columns sit after them, and
                # the full row made every charter-scored rollout of a director
                # agent raise (the gate failed on it, 2026-10-16).
                obs = observation_dict(names, Zr[j][:len(names)])
                score = score_decision(charter, obs, direction)
                meta.update({
                    "passed_gates": True,
                    # A DICT keyed by gate name, which is what `GateRecorder`
                    # produces and what `trade_grouper` reads with
                    # `dict(_md["gate_vector"])`. A list of bools raises
                    # `TypeError: cannot convert dictionary update sequence
                    # element #0 to a sequence` the moment a charter is
                    # attached - which no test caught, because the env fixture
                    # had no charter and this branch never ran.
                    "gate_vector": {g.name: bool(g.name in score.gates_held)
                                    for g in charter.gates},
                    "confluence_tags": list(score.gates_held),
                    "rl_adherence": round(score.adherence, 4),
                    "rl_tier": score.tier,
                    "rl_in_charter": score.in_charter,
                })
                dec = {"adherence": score.adherence, "tier": score.tier,
                       "in_charter": score.in_charter, "bar_index": j}
                if not score.in_charter:
                    self._penalised_bars[j] = ("outside_charter",)
                    continue
                conf_score = tier_scores.get(str(score.tier or ""), flat_score)
            else:
                dec = {"adherence": 0.0, "tier": None, "in_charter": True, "bar_index": j}
                conf_score = flat_score

            # Keyed by the FILL bar, not the signal bar. The engine fills a
            # signal stamped t[k] on the first bar whose open is after it -
            # bar k+1 - and `entry_time` on the resulting trade is THAT bar.
            # Keying by t[k] silently matched nothing, so every trade scored
            # zero adherence and the 0.30 term did nothing at all.
            decisions[int(times[k + 1])] = dec
            if mode_b:
                # The base strategy's own confluences, AS IT RECORDED THEM.
                # `GateRecorder` already stamps these; re-deriving them would
                # read today's parameters onto a past setup.
                bm = base[j].get("metadata") or {}
                meta.update({
                    "base_strategy": self.spec.base_strategy,
                    "base_gate_vector": bm.get("gate_vector"),
                    "base_confluence_tags": bm.get("confluence_tags"),
                    "base_confluence_score": base[j].get("confluence_score"),
                })
            signals.append({
                "symbol": data.symbol,
                "direction": direction,
                # Bar j's own time, so the engine fills at bar j+1's open. See
                # the module note: stamping bar j+1 instead cost 10R over 152
                # GBPJPY trades the last time it happened here.
                "time": int(times[k]),
                "entry_price": entry,
                "stop_loss": stop,
                "take_profit": target,
                "timeframe": data.timeframe,
                "confluence_score": int(conf_score),
                "metadata": meta,
                "strategy_name": f"RL::{self.spec.name}",
            })
            last_bar = j
            if len(signals) >= cap:
                logger.debug(
                    f"[RL] {data.symbol}: signal cap {cap} reached - this policy wants "
                    "to trade far more often than the risk layer can act on, which is a "
                    "degenerate policy rather than a result")
                break
        return signals, decisions

    def _signal_cap(self, n_bars: int, times: np.ndarray) -> int:
        """How many signals are worth building at all.

        Bounded by what the engine COULD act on: `max_daily_trades` a day, over
        the days this window covers, with slack. Beyond that every extra signal
        is refused by the daily cap or by `max_positions_per_symbol` - it
        changes nothing about the result and costs real time, because each one
        is evaluated and logged by the risk engine.

        Measured on real Crash 1000 bars: an untrained policy filled the old
        flat 5,000-signal cap on an 8,000-bar window and one rollout took
        17.8s, which put the full five-seed, eight-path gate at 11.8 hours.
        Nearly all of those signals were rejected as "same direction already
        open". With this cap the same rollout produces the same trades.
        """
        per_day = int(self.risk_config.get("max_daily_trades", 5) or 5)
        if len(times) > 1:
            days = max(1.0, (int(times[-1]) - int(times[0])) / 86400.0)
        else:
            days = 1.0
        # x4 slack: a signal can be refused for reasons other than the daily
        # cap (concurrency, the minimum stop, the circuit breaker), and those
        # refusals are part of the result rather than waste.
        return int(min(MAX_SIGNALS_PER_EPISODE, max(100, per_day * days * 4)))

    # -- mode B's baseline ---------------------------------------------------

    def baseline_episode(self, *, cost_multiplier: float = 1.0,
                         label: str = "baseline") -> Episode:
        """The base strategy as it trades today: its own signals, untouched.

        This is what a mode-B agent has to beat, and it is the reason naming a
        base strategy is worth doing. "Did the agent make money" is a weak
        question; "did it make more than the rule it was built on, on the same
        setups and the same bars" is not.

        No policy is involved — the agent's exit head is absent too, so the
        comparison isolates everything the agent does rather than only its
        entries.
        """
        from backend.backtester.engine import BacktestEngine

        data = self.data
        ep = Episode(symbol=data.symbol, n_bars=len(data), label=label,
                     initial_balance=self.initial_balance)
        signals = [dict(sig) for _, sig in sorted((data.base_signals or {}).items())]
        if not signals:
            return ep
        ep.n_signals = len(signals)
        cfg = stress_costs(self.risk_config, data.symbol, cost_multiplier)
        _eng = BacktestEngine(cfg)
        _eng.group_chart_data = False
        res = _eng.run(data.frame, signals, self.initial_balance,
                                      None, None, None, None, None)
        ep.trades = res.get("grouped_trades") or []
        ep.cost_model = res.get("cost_model") or {}
        ep.significance = res.get("significance") or {}
        ep.final_balance = float(res.get("final_balance") or self.initial_balance)
        ep.rewards = self._score_trades(ep.trades, {})
        ep.breakdown = rw.aggregate(ep.rewards)
        return ep

    # -- scoring ------------------------------------------------------------

    def _score_trades(self, groups: list[dict], decisions: dict[int, dict]) -> list:
        """One `TradeReward` per closed trade, components intact."""
        from backend.analytics.metrics import compute_trade_metrics

        out = []
        for g in groups:
            r = self._trade_r(g, compute_trade_metrics)
            if r is None:
                continue
            entry_t = self._epoch(g.get("entry_time"))
            dec = decisions.get(entry_t) or {}
            counterfactual = None
            if str(g.get("exit_reason", "")).startswith(("RL_", xp.SUPERVISOR_PREFIX)):
                counterfactual = self._hold_to_the_end(g)
            out.append(rw.TradeReward(
                r_net=r,
                adherence=float(dec.get("adherence") or 0.0),
                # None unless the AGENT ended this trade early. A trade that
                # ran to its stop, target or max hold took everything on offer
                # by definition, and there is no counterfactual to pay or
                # withhold a bonus against.
                counterfactual_r=counterfactual,
                adherence_floor=float(getattr(self.charter, "adherence_floor", 0.6)),
                money_weight=self.spec.money_weight,
                adherence_weight=self.spec.adherence_weight,
                downside_penalty=self.spec.downside_penalty,
                tag=str(g.get("exit_reason") or ""),
            ))
        return out

    @staticmethod
    def _trade_r(group: dict, metrics_fn) -> float | None:
        """R against the ORIGINAL risk at entry.

        The project's own definition, and the reason it is not the current stop
        is a measured reporting bug: break-even moves `stop_loss` to entry, the
        denominator collapses, and one SPX500 trade reported +243.51R on a plain
        1.5R target. Spread and slippage are inside these fill prices already;
        commission and swap are money-side and are reported separately as
        `cost_drag_r` rather than folded in, so one R definition serves the
        whole project.
        """
        try:
            m = metrics_fn(group)
        except Exception:
            return None
        r = m.get("realized_rr")
        if r is None or not np.isfinite(float(r)):
            return None
        return float(r)

    def _hold_to_the_end(self, group: dict) -> float | None:
        """What the trade would have returned had it run to its original stop
        or target - the `exit_quality` counterfactual (plan 4.5).

        Computed from bars AFTER the decision, which is legitimate in a reward
        and fatal in an observation. It lives here and not in `features.py`,
        and `tests/test_rl_env.py` asserts the observation matrix is unchanged
        whether or not this is computed.

        Pessimistic on an ambiguous bar: when a bar's range covers both the stop
        and the target, the STOP is taken. That is the same assumption the
        engine's own fill model makes, and the alternative flatters every
        counterfactual - which would reward the agent for NOT exiting early.
        """
        entry = float(group.get("entry_price") or 0.0)
        stop = float((group.get("original_signal") or {}).get("stop_loss")
                     or group.get("initial_stop_loss") or group.get("stop_loss") or 0.0)
        target = float((group.get("original_signal") or {}).get("take_profit")
                       or group.get("take_profit") or 0.0)
        if not entry or not stop or entry == stop:
            return None
        is_long = str(group.get("direction", "BUY")).upper() == "BUY"
        risk = abs(entry - stop)
        t0 = self._epoch(group.get("entry_time"))
        if not t0:
            return None
        times = self.data.times
        j = int(np.searchsorted(times, t0, side="left"))
        if j >= len(times) - 1:
            return None
        highs = self.data.frame["high"].to_numpy(dtype=float)
        lows = self.data.frame["low"].to_numpy(dtype=float)
        closes = self.data.frame["close"].to_numpy(dtype=float)
        end = min(len(times) - 1, j + int(self.spec.max_hold_bars))
        for k in range(j + 1, end + 1):
            if is_long:
                if lows[k] <= stop:
                    return -1.0
                if target and highs[k] >= target:
                    return abs(target - entry) / risk
            else:
                if highs[k] >= stop:
                    return -1.0
                if target and lows[k] <= target:
                    return abs(entry - target) / risk
        last = closes[end]
        return ((last - entry) if is_long else (entry - last)) / risk

    def _cost_drag(self, groups: list[dict]) -> float:
        """Commission and swap, in R, reported rather than folded into R.

        Hiding them inside the R definition is how a cost model stops being
        auditable - this project has already shipped a $7/lot commission figure
        that turned out to be fictional. So they are measured against the same
        denominator R uses (the capital genuinely at risk at entry, from the
        engine's own `calculate_risk_dollars`) and printed next to it.
        """
        from backend.risk.position_sizer import calculate_risk_dollars

        total, n = 0.0, 0
        for g in groups:
            for st in (g.get("sub_trades") or [g]):
                comm = abs(float(st.get("commission") or 0.0))
                swap = abs(float(st.get("swap") or 0.0))
                if not (comm or swap):
                    continue
                entry = float(st.get("entry_price") or 0.0)
                stop = float((st.get("original_signal") or {}).get("stop_loss")
                             or st.get("initial_stop_loss") or st.get("stop_loss") or 0.0)
                vol = float(st.get("volume") or 0.0)
                if not (entry and stop and vol) or entry == stop:
                    continue
                try:
                    risk_dollars = float(calculate_risk_dollars(
                        vol, entry, stop, st.get("symbol") or g.get("symbol") or ""))
                except Exception:
                    continue
                if risk_dollars > 0:
                    total += (comm + swap) / risk_dollars
                    n += 1
        return float(total / n) if n else 0.0

    @staticmethod
    def _epoch(value) -> int:
        if value is None:
            return 0
        if isinstance(value, (int, float)):
            return int(value)
        try:
            return int(pd.Timestamp(value).timestamp())
        except Exception:
            return 0


# -- the risk config an episode runs under ----------------------------------


def default_risk_config(symbol: str, **overrides) -> dict:
    """The same risk config the Backtester page would hand the engine.

    Built through `build_merged_risk_config` rather than hand-assembled.
    A second hand-copied version would drift from the page, and then an agent
    would be measured under settings the app does not actually run - which is
    the failure this project has already had twice: a fresh `UserConfigV2`
    silently ran dataclass defaults, and the Strategy Lab ran defaults for its
    whole life because pydantic dropped the keys it did not know.

    The import is lazy because the route will import this module back.
    """
    from backend.api.routes.backtest import BacktestRequest, build_merged_risk_config

    req = BacktestRequest(symbol=symbol, strategy_id="ORB_v1", **{
        k: v for k, v in overrides.items() if k in BacktestRequest.model_fields})
    cfg = build_merged_risk_config(req)
    cfg.update({k: v for k, v in overrides.items()
                if k not in BacktestRequest.model_fields})
    # Section 4.11: RL evaluation never inherits per-symbol exit tables chosen
    # on history that includes the test window.
    cfg["use_strategy_exit_defaults"] = False
    return cfg


# -- cost stress ------------------------------------------------------------


def stress_costs(risk_config: dict, symbol: str, multiplier: float = 1.0) -> dict:
    """The validation gate's 2x cost run, done through the engine's own model.

    NOT a new cost model and not a flat haircut. `resolve_effective_costs`
    returns what this run would actually be charged - the measured per-symbol
    spread, slippage, commission and swap, with provenance - and this writes
    those same numbers back multiplied, as explicit user settings. So the
    stressed run is the real cost model priced twice as high, and the resolved
    `sources` on the result say "USER" for exactly the fields that were
    stressed, which is what makes the stressed run auditable afterwards.

    A multiplier of 1.0 returns the config unchanged rather than round-tripping
    it, so the ordinary run is byte-identical to one built without this.
    """
    cfg = dict(risk_config or {})
    m = float(multiplier or 1.0)
    if m == 1.0:
        return cfg
    from backend.backtester.engine import resolve_effective_costs

    resolved = resolve_effective_costs(symbol, cfg)
    for field in ("spread_pips", "slippage_pips", "exit_slippage_pips",
                  "commission_per_lot", "swap_long_per_lot_per_day",
                  "swap_short_per_lot_per_day"):
        value = resolved.get(field)
        if value is None:
            continue
        cfg[field] = float(value) * m
    # `stops_level_pips` is the broker's own minimum distance, not a cost: a
    # stressed run must still be refused the same stops the real broker refuses,
    # and doubling it would change which trades exist rather than what they cost.
    cfg["cost_stress_multiplier"] = m
    return cfg


# -- supervision: the three-column comparison -------------------------------


@dataclass
class SupervisionResult:
    """Unsupervised, supervised, and the difference - from ONE bar sequence.

    Two separate runs would drift apart on fills and on the risk engine's
    state, and the difference between two drifted runs is not the supervisor's
    effect. So both halves take the same signal list, the same frame and the
    same costs; only the strategy object differs.
    """
    strategy_id: str
    symbol: str
    unsupervised: Episode
    supervised: Episode
    closed_early: list = field(default_factory=list)
    let_run: list = field(default_factory=list)

    @property
    def delta_r(self) -> float:
        return self.supervised.mean_r - self.unsupervised.mean_r

    @property
    def saved_r(self) -> float:
        """Mean `saved_R`: what supervision changed per trade."""
        return self.delta_r

    @property
    def cutting_winners(self) -> bool:
        """The winner-protection test from the gate.

        Mean R of the trades the supervisor closed early must be at or below the
        mean R of the trades it let run. If it is higher, the agent is
        systematically cutting its winners - the one failure mode a supervisor
        has, and the one this project has already measured twice (break-even at
        1.5R turned 16 of 113 BTCUSD trades into exits the research never took).
        """
        if not self.closed_early or not self.let_run:
            return False
        return float(np.mean(self.closed_early)) > float(np.mean(self.let_run))

    def to_dict(self) -> dict:
        return {
            "strategy_id": self.strategy_id, "symbol": self.symbol,
            "unsupervised": self.unsupervised.to_dict(),
            "supervised": self.supervised.to_dict(),
            "delta_r": round(self.delta_r, 6),
            "n_closed_early": len(self.closed_early),
            "mean_r_closed_early": round(float(np.mean(self.closed_early)), 6)
            if self.closed_early else None,
            "mean_r_let_run": round(float(np.mean(self.let_run)), 6) if self.let_run else None,
            "cutting_winners": self.cutting_winners,
            "measurable": len(self.supervised.trades) >= 100,
        }


def supervise_run(env: TradingEnv, owner: BaseStrategy, signals: list[dict],
                  policy: pol.LinearPolicy, *, agent_id: str, version: int = 1,
                  cost_multiplier: float = 1.0) -> SupervisionResult:
    """Run the owner's signals twice: alone, then with the agent watching.

    The agent never opens anything here. It is handed the owning strategy's
    positions bar by bar and may only close or tighten - and the owner is asked
    first, so a measured rule is never overridden.
    """
    from backend.backtester.engine import BacktestEngine
    from backend.analytics.metrics import compute_trade_metrics

    data = env.data
    risk_config = stress_costs(env.risk_config, data.symbol, cost_multiplier)

    def run(strategy) -> Episode:
        engine = BacktestEngine(dict(risk_config))
        engine.group_chart_data = False
        res = engine.run(data.frame, [dict(s) for s in signals], env.initial_balance,
                         None, None, strategy, None, None)
        ep = Episode(symbol=data.symbol, n_bars=len(data), n_signals=len(signals),
                     initial_balance=env.initial_balance)
        ep.trades = res.get("grouped_trades") or []
        ep.cost_model = res.get("cost_model") or {}
        ep.significance = res.get("significance") or {}
        ep.final_balance = float(res.get("final_balance") or env.initial_balance)
        ep.rewards = [rw.TradeReward(r_net=r, adherence=0.0)
                      for r in (TradingEnv._trade_r(g, compute_trade_metrics) for g in ep.trades)
                      if r is not None]
        ep.breakdown = rw.aggregate(ep.rewards)
        return ep

    base = run(owner)
    sup_strategy = RLPositionStrategy(
        config=risk_config, policy=policy, Z=env.Z, times=data.times, atr=data.atr,
        spec=env.spec, charter=env.charter, enabled=True,
        reason=xp.supervisor_reason(agent_id, version), owner=owner)
    sup_strategy.is_backtesting = True
    with_agent = run(sup_strategy)

    prefix = xp.SUPERVISOR_PREFIX
    closed_early, let_run = [], []
    for g, r in zip(with_agent.trades, [t.r_net for t in with_agent.rewards]):
        (closed_early if str(g.get("exit_reason", "")).startswith(prefix) else let_run).append(r)
    with_agent.exit_decisions = sup_strategy.decisions

    return SupervisionResult(strategy_id=getattr(owner, "strategy_id", type(owner).__name__),
                             symbol=data.symbol, unsupervised=base, supervised=with_agent,
                             closed_early=closed_early, let_run=let_run)


# -- CPCV folds -------------------------------------------------------------


@dataclass(frozen=True)
class Fold:
    """One combinatorially-purged fold: which rows train, which test."""
    path: int
    train: tuple[tuple[int, int], ...]
    test: tuple[tuple[int, int], ...]

    @property
    def test_rows(self) -> int:
        return sum(b - a for a, b in self.test)

    @property
    def train_rows(self) -> int:
        return sum(b - a for a, b in self.train)


def cpcv_folds(n: int, groups: int = 8, test_groups: int = 2, embargo: int = 288
               ) -> list[Fold]:
    """Combinatorially purged cross-validation, with purge AND embargo.

    `groups` contiguous blocks; every combination of `test_groups` of them is a
    test path, and training is everything else MINUS an embargo of `embargo`
    bars on BOTH sides of each test block.

    The embargo is the half people skip. Purging alone removes training
    observations whose own label window overlaps a test block; it does nothing
    about a training observation that sits just before a test block and whose
    label runs into it. With `max_hold_bars` of 288, a training bar 200 bars
    before a test block still has a label that lands inside it. So the embargo
    is `max_hold_bars` wide and applied on both sides.
    """
    from itertools import combinations

    n, groups = int(n), max(2, int(groups))
    test_groups = max(1, min(int(test_groups), groups - 1))
    embargo = max(0, int(embargo))
    edges = [int(round(i * n / groups)) for i in range(groups + 1)]
    blocks = [(edges[i], edges[i + 1]) for i in range(groups)]

    out: list[Fold] = []
    for path, combo in enumerate(combinations(range(groups), test_groups)):
        test = tuple(blocks[i] for i in sorted(combo))
        forbidden = []
        for a, b in test:
            forbidden.append((max(0, a - embargo), min(n, b + embargo)))
        train = []
        for i, (a, b) in enumerate(blocks):
            if i in combo:
                continue
            spans = [(a, b)]
            for fa, fb in forbidden:
                nxt = []
                for sa, sb in spans:
                    if fb <= sa or fa >= sb:
                        nxt.append((sa, sb))
                        continue
                    if sa < fa:
                        nxt.append((sa, fa))
                    if fb < sb:
                        nxt.append((fb, sb))
                spans = nxt
            train += [(a2, b2) for a2, b2 in spans if b2 - a2 > 0]
        out.append(Fold(path=path, train=tuple(train), test=test))
    return out


def rows_of(spans) -> np.ndarray:
    """The row indices a set of spans covers."""
    if not spans:
        return np.zeros(0, dtype=np.int64)
    return np.concatenate([np.arange(a, b, dtype=np.int64) for a, b in spans])


def n_paths(groups: int, test_groups: int) -> int:
    return math.comb(int(groups), int(test_groups))
