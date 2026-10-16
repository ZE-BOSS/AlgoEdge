"""
backend/strategies/strategy_rl/engine.py

One `BaseStrategy` that serves any approved agent.

WHY THIS IS A STRATEGY AND NOT A NEW SUBSYSTEM
----------------------------------------------
Everything the agent needs already exists on this interface:

  `on_bar`               -> the entry decision, on `BarFeed` windows, so the
                            backtest and the live scan loop hand it the exact
                            same bar sequence.
  `on_position_bar`      -> the exit head, called once per closed bar per open
                            position by both backtesters and, via
                            `LIVE_POSITION_EXITS`, by `position_manager`.
  `on_foreign_position_bar` -> supervision of another strategy's trade.
  `gate()` / `_tag_signal` -> `passed_gates`, `gate_vector` and
                            `confluence_tags` on every signal, which is the
                            explainability surface the charter is scored on.

So an approved agent appears in the Backtester's strategy list, the slot editor
and the Strategy Lab as `RL::<name>@<version>` and nothing else in the app has
to know it is different.

THE PARITY PROBLEM THIS FILE HAS TO SOLVE
-----------------------------------------
Training computes features over the WHOLE frame at once, vectorised. Live
computes them on a rolling window. For trailing features with finite memory
those agree exactly; for the exponentially-weighted ones (ATR, the EMAs) a
truncated window differs from the full history by the weight the EWM still
places on bars that fell off the start. That difference is small and it is
NOT zero, which is the kind of thing this project has been bitten by - so
`WINDOW_BARS` is set from the slowest feature's memory rather than from a round
number, and `tests/test_rl_live_parity.py` measures the residual instead of
assuming it.

WHAT IT CANNOT DO
-----------------
No action here sets a lot size, a stop distance or a risk percentage. The stop
and target come from the slot's own ATR settings, exactly as every other
strategy builds them, and `confluence_score` is the APPROVED charter's tier -
never the policy's continuous confidence, because the risk layer sizes by that
number and a smoothly-moving score would be a position-sizing dial.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from backend.rl import adherence as adh
from backend.rl import charter as ch
from backend.rl import exit_policy as xp
from backend.rl import features as feat
from backend.rl import registry as rlreg
from backend.strategies.base_strategy import BaseStrategy, TradeAction, TradeSignal
from backend.strategies.core.daily_atr import last_epoch
from backend.utils.logger import get_logger

logger = get_logger(__name__)

# ATR(14) and ATR(50) are EWMs; rv60 and donchian_60 need 60; EMA(50) needs 50.
# 1,200 bars puts the residual weight on the dropped history below 1e-9 for
# every one of them - measured in tests/test_rl_live_parity.py rather than
# assumed. A round 500 would leave ATR(50) visibly different from the frame the
# agent was trained on.
PARITY_WINDOW_BARS = 1200

TIER_SCORES = {"A": 90, "B": 80, "C": 70}
DEFAULT_SCORE = 80


class RLStrategy(BaseStrategy):
    """Base for the per-agent classes `registry._register` creates.

    `AGENT_ID` is set on the subclass. Constructing it without one is a
    programming error rather than a configuration problem, so it raises.
    """

    AGENT_ID: str = ""
    strategy_id: str = "RL::unknown@1"
    LIVE_POSITION_EXITS = True
    POSITION_BAR_WINDOW = PARITY_WINDOW_BARS
    SUPERVISES_OTHER_STRATEGIES = False

    def __init__(self, config: Any):
        super().__init__(config)
        if not self.AGENT_ID:
            raise ValueError("RLStrategy must be subclassed with an AGENT_ID - "
                             "use registry.register_approved()")
        # Enforcement point: `cached` only holds agents whose charter is
        # APPROVED, so an unapproved agent cannot be constructed at all.
        self.agent = rlreg.cached(self.AGENT_ID)
        self.spec = self.agent.spec
        self.policy = self.agent.policy
        self.scaler = self.agent.scaler
        self.charter = self.agent.charter
        # The director rides inside the policy (see `LinearPolicy.director`):
        # its two columns are part of the policy's input width.
        self.director = (getattr(self.agent, "director", None)
                         or self.policy.director_model())
        self.SUPERVISES_OTHER_STRATEGIES = bool(self.spec.supervises)
        self._last_signal_bar: dict[str, int] = {}
        # Live adherence is recorded per closed trade by `notify_outcome`; the
        # per-decision scores are held here until then.
        self._pending: dict[str, adh.DecisionScore] = {}

    # -- plumbing -----------------------------------------------------------

    def get_required_timeframes(self) -> list[str]:
        return [self.spec.timeframe]

    @property
    def WINDOW_BARS(self) -> dict[str, int]:  # noqa: N802 — read by strategies.windows
        return {self.spec.timeframe: PARITY_WINDOW_BARS}

    async def initialize(self):
        return None

    async def on_tick(self, symbol: str, tick: dict[str, Any]) -> None:
        return None

    # -- observation --------------------------------------------------------

    def _observe(self, candles: pd.DataFrame) -> tuple[np.ndarray, dict[str, float]] | None:
        """The scaled observation for the LAST bar of this window.

        The scaler is the agent's own, fitted on its training fold and frozen.
        Re-fitting it here on the window would be the whole-dataset scaling leak
        in its most innocent-looking form - a line that reads like a
        normalisation and is actually a lookahead.
        """
        if candles is None or len(candles) < 60:
            return None
        X = feat.matrix(candles, self.spec.features)
        if not len(X):
            return None
        scaled = self.scaler.transform(X[-1:])
        # The charter's gates are named for MARKET features, so adherence is
        # scored on those columns alone - the director's two columns are an
        # input to the policy, not a condition anyone approved.
        obs = adh.observation_dict(list(self.spec.features), scaled[0])
        if self.director is not None:
            from backend.rl.director import augment
            scaled = augment(scaled, self.director, self.spec.min_confidence)
        return scaled[0], obs

    # -- the entry decision -------------------------------------------------

    async def on_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame
                     ) -> TradeSignal | None:
        self.begin_candidate(symbol, timeframe,
                             bar_time=candles.index[-1] if candles is not None
                             and len(candles) else None)
        if timeframe != self.spec.timeframe:
            return None
        if not self.gate("symbol_permitted", symbol in self.spec.symbols,
                         f"{symbol} is not an instrument this charter covers"):
            return None
        observed = self._observe(candles)
        if not self.gate("enough_history", observed is not None):
            return None
        z, obs = observed

        direction, confidence, _probs = self.policy.act_entry(z, self.spec.min_confidence)
        if not self.gate("policy_wants_a_trade", direction is not None,
                         f"confidence {confidence:.3f} against "
                         f"{self.spec.min_confidence:.2f}"):
            return None

        score = adh.score_decision(self.charter, obs, direction)
        # The charter decides whether this setup is one the admin approved. A
        # trade outside it is not a low-adherence trade to be taken and scored
        # badly - it is a trade nobody approved.
        if not self.gate("in_charter", score.in_charter,
                         f"tier {score.tier or 'none'}, missing "
                         f"{', '.join(score.gates_skipped) or 'nothing'}"):
            return None
        for gate_name in (g.name for g in self.charter.in_scope(direction)):
            self.gate(gate_name, gate_name in score.gates_held)

        if adh.monitor.suspended(self.charter):
            # Suspended means suspended. It does not quietly fall back to
            # trading without the charter's gates.
            self.log_event(
                f"{self.strategy_id} is suspended for adherence drift - no signal taken",
                level="WARN", category="RL")
            return None

        entry = float(candles["close"].iloc[-1])
        atr = float(feat.atr(candles, 14).iloc[-1])
        if not self.gate("atr_available", np.isfinite(atr) and atr > 0):
            return None
        # Where the stop goes and what the trade is aiming at: the agent's two
        # risk decisions, each an index into the menu the admin pre-registered
        # on the spec. With no risk head the slot's own settings apply.
        if self.spec.risk_head:
            si, ri = self.policy.risk_choice(z)
            menu_stop = tuple(self.spec.stop_atr_choices)
            menu_rr = tuple(self.spec.rr_choices)
            stop_mult = float(menu_stop[si % len(menu_stop)])
            min_rr = float(menu_rr[ri % len(menu_rr)])
        else:
            stop_mult = float(getattr(self.config, "stop_atr_multiple", 0) or
                              self._cfg("stop_atr_multiple", 1.5))
            min_rr = float(self._cfg("min_rr", 2.0))
        risk = atr * stop_mult
        sgn = 1.0 if direction == "BUY" else -1.0
        signal = TradeSignal(
            strategy_id=self.strategy_id, symbol=symbol, direction=direction,
            signal_type="RL_ENTRY", timeframe=timeframe, entry_price=entry,
            stop_loss=entry - sgn * risk, take_profit=entry + sgn * min_rr * risk,
            confluence_score=TIER_SCORES.get(score.tier or "", DEFAULT_SCORE),
            timestamp=float(last_epoch(candles)),
            metadata={
                "agent_id": self.AGENT_ID,
                "charter_version": self.charter.version,
                "charter_hash": self.charter.content_hash,
                "rl_confidence": round(float(confidence), 4),
                "rl_adherence": round(score.adherence, 4),
                "rl_tier": score.tier,
                "rl_stop_atr_multiple": round(stop_mult, 4),
                "rl_rr": round(min_rr, 4),
                "rl_gates_held": list(score.gates_held),
                "rl_gates_skipped": list(score.gates_skipped),
            },
        )
        self._pending[f"{symbol}:{int(last_epoch(candles))}"] = score
        if not self.is_backtesting:
            # The R:R is the only risk number the agent chose. Everything else
            # on this trade - the lot size, the risk percent, the drawdown caps
            # - belongs to the risk layer and is not in its action space.
            from backend.rl import notify as rl_notify
            rl_notify.trade_opened(self.AGENT_ID, symbol, direction, min_rr,
                                   score.tier, score.adherence,
                                   stop_atr_multiple=stop_mult)
        return self._tag_signal(signal)

    def _cfg(self, name: str, default):
        """A risk setting, from wherever this strategy was constructed with.

        The engine is constructed with a risk_config dict in a backtest and a
        user config object live, so both shapes are read. Reading only one is
        how a setting silently falls back to a dataclass default - which has
        happened twice in this codebase.
        """
        cfg = self.config
        if isinstance(cfg, dict):
            v = cfg.get(name)
        else:
            v = getattr(cfg, name, None)
        try:
            return type(default)(v) if v is not None else default
        except (TypeError, ValueError):
            return default

    # -- the exit head ------------------------------------------------------

    def on_position_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame,
                        position: dict) -> TradeAction | None:
        if not self.spec.exit_head:
            return None
        observed = self._observe(candles)
        if observed is None:
            return None
        z, _obs = observed
        price = float(candles["close"].iloc[-1])
        atr = float(feat.atr(candles, 14).iloc[-1])
        decision = xp.decide(
            self.policy, z, position, price=price,
            bars_held=self._bars_held(candles, position),
            max_hold_bars=self.spec.max_hold_bars,
            min_confidence=self.spec.min_confidence,
            atr_value=atr if np.isfinite(atr) and atr > 0 else None,
            atr_multiple=float(self._cfg("atr_trail_multiplier", 1.0)),
            charter=self.charter)
        if not decision.acts:
            return None
        action = decision.to_trade_action(int(position.get("ticket") or 0))
        if action is None:
            return None
        if action.action == "MODIFY_SL" and not xp.tightens(
                action.new_sl, position.get("stop_loss"), position.get("direction", "BUY")):
            return None
        self.log_event(
            f"{self.strategy_id} {decision.action} on {symbol} "
            f"(confidence {decision.confidence:.2f}, gates "
            f"{', '.join(decision.gates) or 'none'})", category="RL")
        if not self.is_backtesting:
            from backend.rl import notify as rl_notify
            rl_notify.position_action(
                self.AGENT_ID, symbol, decision.action, decision.reason,
                decision.gates, decision.confidence,
                foreign_owner=str(position.get("strategy_id") or ""))
        return action

    def supervises(self, strategy_id: str, symbol: str) -> bool:
        """Scoped by the AgentSpec, which was frozen before training.

        An agent trained on Crash 1000 does not get to supervise GBPJPY, and an
        agent that was never measured as a supervisor supervises nothing: the
        spec's `supervises` list is empty by default and `may_supervise`
        returns False for an empty list.
        """
        return self.spec.may_supervise(strategy_id, symbol)

    def on_foreign_position_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame,
                                position: dict, owner: Any = None) -> TradeAction | None:
        """Supervising a position another strategy opened (plan 4.9).

        The OWNER has already been asked by the time this is called - both
        backtesters and `position_manager` consult the owning strategy first and
        only fall through to supervisors when it had nothing to say. So a
        measured rule is never overridden; the agent can only act earlier.

        `owner` carries the opening strategy's id and its entry gate vector AS
        RECORDED AT ENTRY. Re-deriving that vector here would be a lookahead:
        it would be computed from the charter and the features as they are now.
        """
        if not self.SUPERVISES_OTHER_STRATEGIES:
            return None
        owner_id = str((owner or {}).get("strategy_id") or position.get("strategy_id") or "")
        if not self.spec.may_supervise(owner_id, symbol):
            return None
        action = self.on_position_bar(symbol, timeframe, candles, position)
        if action is None:
            return None
        # Attributed in the Journal, so you can tell afterwards which exits were
        # the agent's. Without this the result cannot be attributed at all.
        action.close_reason = xp.supervisor_reason(self.AGENT_ID, self.charter.version)
        return action

    def _bars_held(self, candles: pd.DataFrame, position: dict) -> int:
        entry = position.get("entry_time")
        if entry is None:
            return 0
        try:
            ts = int(entry) if isinstance(entry, (int, float)) \
                else int(pd.Timestamp(entry).timestamp())
        except Exception:
            return 0
        times = (candles["time"].to_numpy(dtype="int64") if "time" in candles.columns
                 else pd.DatetimeIndex(candles.index).as_unit("s").asi8)
        return int(max(0, len(times) - 1 - int(np.searchsorted(times, ts, side="left"))))

    # -- live adherence -----------------------------------------------------

    def notify_outcome(self, symbol: str, group_id: str, is_win: bool, pnl: float) -> None:
        """Record the closed trade's adherence and check for drift.

        Per closed TRADE, not per bar: crediting adherence per bar would make
        "never trade" the highest-adherence policy, and the drift monitor would
        then never fire on an agent that had stopped trading its charter.
        """
        score = None
        for key in list(self._pending):
            if key.startswith(f"{symbol}:"):
                score = self._pending.pop(key)
                break
        if score is None:
            return
        if self.charter.status != ch.APPROVED:
            return
        verdict = adh.monitor.record(self.charter, score)
        if verdict.suspend:
            self.log_event(verdict.message, level="ERROR", category="RL")
            if not self.is_backtesting:
                # Recorded, not sent. This runs in a worker thread with no
                # session and no event loop; `suspension.drain` does the
                # database write and the email from the jobs loop. The agent is
                # already suspended in-process either way - the safety property
                # does not wait for the email.
                from backend.rl import suspension
                suspension.record(self.AGENT_ID, self.charter.version, verdict)
        elif verdict.recovered:
            self.log_event(verdict.message, level="WARN", category="RL")
