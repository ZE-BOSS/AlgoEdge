"""
backend/risk/engine.py

Main Risk Engine Orchestrator.
Uses all sub-components to validate, size, and manage trades.
Identical logic used in both live trading and backtesting.
Source: RiskManagement_Spec.md
"""

import json
from datetime import datetime
from typing import Any

from backend.risk.breakeven_manager import BreakevenManager
from backend.risk.circuit_breaker import CircuitBreaker
from backend.risk.multi_tp import MultiTPManager
from backend.risk.position_sizer import (
    calculate_lot_size,
    calculate_risk_dollars,
    get_confluence_scaled_risk,
    get_last_sizing_diagnostics,
    get_pip_size,
)
from backend.risk.trailing_manager import TrailingManager
from backend.utils.logger import get_logger

logger = get_logger(__name__)


class RiskEngine:
    """
    Orchestrates all risk logic for live and backtesting.
    What you backtest = what runs live.
    Source: RiskManagement_Spec.md Section 7
    """

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.multi_tp = MultiTPManager(config)
        self.breakeven = BreakevenManager(config)
        self.trailing = TrailingManager(config)
        self.is_backtesting = config.get("is_backtest", False)
        self.circuit = CircuitBreaker(config, is_backtest=self.is_backtesting)

        # Risk params
        self.risk_pct = config.get("risk_per_trade_pct", 1.0)
        self.min_rr = config.get("min_rr", 3.0)
        # [15.4] `min_rr` gates on blended_rr (task 5.7) — the VOLUME-WEIGHTED RR
        # across the TP ladder, not the last TP's RR. Those two numbers differ a
        # lot, and the difference can make a config that looks reasonable reject
        # 100% of signals with nothing to distinguish it from "the market gave no
        # setups".
        #
        # Concretely: TP 1.5/3/5 at 50/30/20 blends to 2.65, so a min_rr of 3
        # refuses every signal ever generated, before sizing. That is exactly the
        # shipped default combination. Checked once here rather than left to be
        # inferred from an empty funnel.
        self._warn_if_rr_unreachable()
        self.sl_buffer_pips = config.get("sl_buffer_pips", 5.0)
        self.compounding_enabled = config.get("compounding_enabled", False)
        # [Phase 2 sizing-truth] real, user-editable settings that used to be
        # hardcoded module constants — see core/config_schema.py::RiskParams.
        self.max_margin_utilisation_pct = config.get("max_margin_utilisation_pct")
        self.max_account_leverage = config.get("max_account_leverage")
        self.min_deployable_risk_pct = config.get("min_deployable_risk_pct", 0.0)
        self.min_stop_spread_multiple = config.get("min_stop_spread_multiple")
        # [15.2] Economic stop floor: stop >= N x (spread + 2 x slippage).
        # 0 = disabled, which is the shipped default — see the field's
        # docstring in core/config_schema.py for the measurement behind it.
        self.min_stop_cost_multiple = config.get("min_stop_cost_multiple", 0.0)
        self.confluence_risk_tiers = config.get("confluence_risk_tiers")
        self.reject_below_confluence = config.get("reject_below_confluence", True)
        self.post_split_risk_tolerance_pct = config.get("post_split_risk_tolerance_pct", 5.0)
        self.open_risk_weight = config.get("open_risk_weight", 0.5)
        # [3.5/E3] RiskParams.min_sl_pips — defined (default 10.0 in
        # RiskParams) but never actually reached minimum_stop_distance(); now
        # wired so it works when a caller passes it. Engine-level fallback
        # deliberately stays 0.0 (disabled) rather than jumping to
        # RiskParams' 10.0 default: this is a REJECTION gate — activating an
        # unrequested global floor by default could newly reject signals that
        # were accepted under every run to date, the opposite direction from
        # this codebase's live "too few trades" complaint. Pass min_sl_pips
        # explicitly in risk_config to turn it on.
        self.min_sl_pips = config.get("min_sl_pips", 0.0)
        # [4.6/5.4] Whether trailing may only begin after break-even. Default
        # False matches live's prior behaviour and Part 4's stated use case
        # (trailing at 1R with no BE requirement).
        self.trail_require_be_first = config.get("trail_require_be_first", False)
        # [5.4] RR | TP_HIT | EITHER | NONE — default RR matches today's
        # unconditional unrealized_r >= trail_activation_rr behaviour.
        self.trail_mode = config.get("trail_mode", "RR")
        self.trail_trigger_tp_level = config.get("trail_trigger_tp_level", 1)
        # [9.5/9.6] Portfolio governor — both 0 (disabled) by default.
        self.max_cluster_risk_pct = config.get("max_cluster_risk_pct", 0.0)
        self.max_net_direction_risk_pct = config.get("max_net_direction_risk_pct", 0.0)
        self.symbol_cluster_overrides = config.get("symbol_cluster_overrides") or {}
        self.strategy_risk_budget_pct = config.get("strategy_risk_budget_pct") or {}
        # is_backtesting is kept for informational purposes / future guards.
        # Both live and backtest modes use MT5 data when available, with
        # InstrumentProfile as fallback — so use_live_mt5 is always True.
        # self.is_backtesting is now populated earlier


    def _warn_if_rr_unreachable(self) -> None:
        """
        Log loudly when `min_rr` cannot be satisfied by the configured TP ladder.

        Best-effort and never raises: it reads the same config keys MultiTPManager
        does and reproduces its blend, so a config shape this does not recognise
        simply produces no warning rather than a wrong one.
        """
        try:
            cfg = self.config
            tp_count = int(cfg.get("tp_count", 1) or 1)
            rrs = [float(cfg.get(f"tp{i}_rr", 0.0) or 0.0) for i in range(1, tp_count + 1)]
            if not rrs or max(rrs) <= 0:
                return

            weights = cfg.get("tp_volume_pcts") or cfg.get("tp_splits")
            if isinstance(weights, str):
                weights = [float(x) for x in weights.split(",") if x.strip()]
            if not weights:
                weights = [100.0 / tp_count] * tp_count
            weights = [float(w) for w in weights][:tp_count]
            total = sum(weights) or 1.0
            blended = sum((w / total) * rr for w, rr in zip(weights, rrs))

            if blended < float(self.min_rr):
                # f-string, not %-args: this project logs through loguru, which
                # does not do %-style interpolation — the placeholders would be
                # printed literally and the numbers lost.
                logger.error(
                    f"[RISK] min_rr={float(self.min_rr):.2f} can NEVER be met by this TP "
                    f"ladder: TPs {rrs} at volumes {weights} blend to {blended:.2f}. Every "
                    f"signal will be rejected with 'insufficient_rr' before sizing, and the "
                    f"rejection funnel will look identical to a market that produced no "
                    f"setups. Either lower min_rr below {blended:.2f}, raise the TP RRs, or "
                    f"shift volume towards the further targets."
                )
        except Exception as e:
            logger.debug(f"[RISK] RR feasibility check skipped: {e}")

    def evaluate_signal(
        self,
        signal_data: dict[str, Any],
        account_balance: float,
        current_time: datetime | None = None,
        initial_balance: float | None = None,
    ) -> tuple[bool, str, list[Any]]:
        """
        Evaluate if a signal is safe to trade, and if so, calculate sizes and TPs.
        Returns (is_approved, reason, tp_levels).
        """
        symbol = signal_data.get("symbol", "")
        direction = signal_data.get("direction", "")
        entry = signal_data.get("entry_price", 0.0)
        sl = signal_data.get("stop_loss", 0.0)

        base_balance = initial_balance if initial_balance is not None else account_balance

        # [12.10/Part14] Extract the signal's real entry timeframe — checked
        # top-level FIRST. Was metadata-only, but no caller (backtester,
        # portfolio engine, or live bot_service.py) ever actually populated
        # metadata.timeframe, even though every strategy correctly sets the
        # real value on TradeSignal.timeframe — so this silently fell through
        # to "M15" on EVERY signal from EVERY strategy, regardless of actual
        # entry timeframe. For an M5 strategy (VWAP, CRT's LTF trigger) this
        # made circuit_breaker.check_symbol's post-close cooldown use 900s
        # (M15) candle boundaries instead of the correct 300s (M5) — up to
        # 3x longer than intended, i.e. exactly the "cache persists longer
        # than it should, sometimes blocks trades" symptom reported. Callers
        # now set signal_data["timeframe"] directly (see bot_service.py /
        # backtester/engine.py / portfolio_engine.py); the metadata fallback
        # is kept for any caller that still nests it there.
        timeframe = signal_data.get("timeframe") or signal_data.get("metadata", {}).get("timeframe", "M15")
        # [2.18/2.19] resolved once, early, so both check_all (per-strategy daily
        # trades/concurrent positions) and the TP-split section below use the
        # same value rather than two independent lookups drifting apart.
        strategy_id_for_cb = signal_data.get("metadata", {}).get("strategy_id", "UNKNOWN")
        # [12.5/12.6/Part14] Populated only by a slot-aware dispatcher (12.7/12.8);
        # every other caller leaves these None and check_symbol falls back to
        # today's symbol-wide-only checks exactly as before.
        _slot_meta = signal_data.get("metadata", {})
        slot_id_for_cb = _slot_meta.get("slot_id")
        slot_max_positions = _slot_meta.get("slot_max_positions")
        slot_max_losses_per_day = _slot_meta.get("slot_max_losses_per_day")

        cb_ok, cb_reason = self.circuit.check_symbol(
            symbol, timeframe, current_time,
            slot_id=slot_id_for_cb,
            slot_max_positions=slot_max_positions,
            slot_max_losses_per_day=slot_max_losses_per_day,
        )
        if not cb_ok:
            logger.warning(json.dumps({
                "event": "risk_rejected",
                "reason": "circuit_breaker_symbol",
                "details": cb_reason
            }))
            return False, cb_reason, []

        can_trade, reason = self.circuit.check_all(base_balance, current_time, strategy_id=strategy_id_for_cb)
        if not can_trade:
            logger.warning(json.dumps({
                "event": "risk_rejected",
                "reason": "circuit_breaker_blocked",
                "details": reason
            }))
            return False, reason, []

        # 1b. Prop Firm drawdown hard block (only when account_mode = 'prop_firm')
        if hasattr(self, "prop_firm_validator") and self.prop_firm_validator and self.prop_firm_validator.enabled:
            pf_blocked, pf_reason = self.prop_firm_validator.should_block_trading()
            if pf_blocked:
                logger.warning(json.dumps({
                    "event": "risk_rejected",
                    "reason": "prop_firm_drawdown_block",
                    "details": pf_reason
                }))
                return False, pf_reason, []

        # 2. Minimum RR Check and Direction Validation
        from backend.risk.multi_tp import _is_buy
        is_buy = _is_buy(direction)
        
        if is_buy and sl >= entry:
            logger.warning(json.dumps({
                "event": "risk_rejected",
                "reason": "invalid_sl",
                "direction": "BUY",
                "entry": entry,
                "sl": sl
            }))
            return False, "BUY Stop Loss must be below entry", []
            
        if not is_buy and sl <= entry:
            logger.warning(json.dumps({
                "event": "risk_rejected",
                "reason": "invalid_sl",
                "direction": "SELL",
                "entry": entry,
                "sl": sl
            }))
            return False, "SELL Stop Loss must be above entry", []
            
        risk = abs(entry - sl)
        if risk == 0:
            logger.warning(json.dumps({
                "event": "risk_rejected",
                "reason": "zero_risk",
                "entry": entry,
                "sl": sl
            }))
            return False, "Risk is zero (entry == SL)", []

        # 3. Position Sizing
        # IMPORTANT: We have completely removed compounding per user request.
        # Position sizing MUST always use the static `initial_balance` (e.g. $25,000)
        # so that risk size does not balloon as the account grows.
        size_modifier = signal_data.get("metadata", {}).get("size_modifier", 1.0)

        # [9.3/9.4] MarketContext-derived size_modifier — opt-in: only applied
        # when a caller has populated metadata.market_context (a MarketContext
        # instance or an equivalent dict). No strategy engine does this yet,
        # so this multiplies by 1.0 (no-op) for every signal today; wiring a
        # strategy to populate it is a separate per-engine task (see
        # market_context.py's module docstring).
        _mc = signal_data.get("metadata", {}).get("market_context")
        if _mc is not None:
            from backend.services.market_context import MarketContext, market_context_size_modifier
            if not isinstance(_mc, MarketContext):
                try:
                    _mc = MarketContext(**_mc)
                except Exception:
                    _mc = None
            if _mc is not None:
                size_modifier *= market_context_size_modifier(_mc, direction)

        # Confluence-scaled risk (RiskManagement_Spec.md §5.3): scale the effective
        # risk_pct down when the signal's confluence_score is below the optimal tier,
        # and reject outright below the minimum-confidence threshold. confluence_score
        # is a top-level TradeSignal field — check there first (how the backtester's
        # signal dicts carry it), falling back to metadata for any caller that nests it.
        confluence_score = signal_data.get("confluence_score")
        if confluence_score is None:
            confluence_score = signal_data.get("metadata", {}).get("confluence_score")

        # [12.6/Part14] Slot-level risk_per_trade_pct override, resolved before
        # confluence scaling (which then applies on top of whichever base is
        # in effect — slot-specific or global). None (the default; no caller
        # populates this yet until 12.7/12.8 wire slot dispatch) reproduces
        # today's global-only behaviour exactly.
        slot_risk_per_trade_pct = signal_data.get("metadata", {}).get("slot_risk_per_trade_pct")
        base_risk_pct = slot_risk_per_trade_pct if slot_risk_per_trade_pct is not None else self.risk_pct

        if confluence_score is not None:
            effective_base_risk_pct = get_confluence_scaled_risk(
                base_risk_pct, confluence_score,
                tiers=self.confluence_risk_tiers,
                reject_below_confluence=self.reject_below_confluence,
            )
            if effective_base_risk_pct <= 0:
                logger.warning(json.dumps({
                    "event": "risk_rejected",
                    "reason": "confluence_score_too_low",
                    "confluence_score": confluence_score,
                }))
                return False, f"Confluence score {confluence_score} below minimum threshold for risk deployment", []
        else:
            effective_base_risk_pct = base_risk_pct

        # Both live and backtest use MT5 data when available → InstrumentProfile fallback.
        # This matches how _calc_pnl() works (MT5 first via get_symbol_info).

        requested_risk_dollars = base_balance * (effective_base_risk_pct / 100.0) * size_modifier

        # Predictive Drawdown Guard - Dynamic Scaling
        max_daily_dd = self.circuit.max_daily_drawdown_pct
        max_weekly_dd = self.circuit.max_weekly_drawdown_pct
        # [I3] which DD guard, if any, actually reduced the risk budget this call —
        # feeds sizing_diagnostics.binding_constraint below.
        dd_binding: str | None = None

        # [9.7] This strategy's own share of the budget, if configured — checked
        # ADDITIONALLY to (never instead of) the account-wide budget below, and
        # scoped to REALISED P&L only (see RiskParams.strategy_risk_budget_pct).
        strategy_budget_pct = self.strategy_risk_budget_pct.get(strategy_id_for_cb)

        if max_daily_dd > 0 and base_balance > 0:
            # [2.21/R9] open_risk_weight (default 0.5): count only a fraction of
            # still-open risk as "already lost" — the old 100% weighting let 2-3
            # open positions at 1% risk exhaust an entire week's drawdown budget
            # by Wednesday purely on unrealised (not actual) loss.
            open_risk = getattr(self.circuit, "get_open_risk", lambda: 0.0)() * self.open_risk_weight
            max_loss_dollars = base_balance * (max_daily_dd / 100.0)
            already_lost_dollars = -self.circuit.daily_pnl + open_risk
            remaining_daily_risk = max_loss_dollars - already_lost_dollars

            if remaining_daily_risk <= 0:
                logger.warning(json.dumps({"event": "risk_rejected", "reason": "daily_drawdown_exhausted"}))
                return False, f"Daily drawdown limit of {max_daily_dd}% is fully exhausted.", []

            if strategy_budget_pct is not None:
                strategy_max_loss = max_loss_dollars * (strategy_budget_pct / 100.0)
                strategy_already_lost = -getattr(self.circuit, "strategy_daily_pnl", {}).get(strategy_id_for_cb, 0.0)
                strategy_remaining = strategy_max_loss - strategy_already_lost
                if strategy_remaining <= 0:
                    logger.warning(json.dumps({"event": "risk_rejected", "reason": "strategy_daily_risk_budget_exhausted", "strategy_id": strategy_id_for_cb}))
                    return False, (
                        f"{strategy_id_for_cb}'s daily risk budget ({strategy_budget_pct}% of the "
                        f"{max_daily_dd}% daily drawdown budget) is fully exhausted."
                    ), []
                remaining_daily_risk = min(remaining_daily_risk, strategy_remaining)

            if remaining_daily_risk < requested_risk_dollars:
                logger.info(f"Scaling down risk from ${requested_risk_dollars:.2f} to ${remaining_daily_risk:.2f} to honor {max_daily_dd}% daily drawdown.")
                requested_risk_dollars = remaining_daily_risk
                dd_binding = "daily_dd"

        if max_weekly_dd > 0 and base_balance > 0:
            open_risk = getattr(self.circuit, "get_open_risk", lambda: 0.0)() * self.open_risk_weight
            max_weekly_loss_dollars = base_balance * (max_weekly_dd / 100.0)
            already_lost_weekly = -self.circuit.weekly_pnl + open_risk
            remaining_weekly_risk = max_weekly_loss_dollars - already_lost_weekly

            if remaining_weekly_risk <= 0:
                logger.warning(json.dumps({"event": "risk_rejected", "reason": "weekly_drawdown_exhausted"}))
                return False, f"Weekly drawdown limit of {max_weekly_dd}% is fully exhausted.", []

            if strategy_budget_pct is not None:
                strategy_max_loss_w = max_weekly_loss_dollars * (strategy_budget_pct / 100.0)
                strategy_already_lost_w = -getattr(self.circuit, "strategy_weekly_pnl", {}).get(strategy_id_for_cb, 0.0)
                strategy_remaining_w = strategy_max_loss_w - strategy_already_lost_w
                if strategy_remaining_w <= 0:
                    logger.warning(json.dumps({"event": "risk_rejected", "reason": "strategy_weekly_risk_budget_exhausted", "strategy_id": strategy_id_for_cb}))
                    return False, (
                        f"{strategy_id_for_cb}'s weekly risk budget ({strategy_budget_pct}% of the "
                        f"{max_weekly_dd}% weekly drawdown budget) is fully exhausted."
                    ), []
                remaining_weekly_risk = min(remaining_weekly_risk, strategy_remaining_w)

            if remaining_weekly_risk < requested_risk_dollars:
                logger.info(f"Scaling down risk from ${requested_risk_dollars:.2f} to ${remaining_weekly_risk:.2f} to honor {max_weekly_dd}% weekly drawdown.")
                requested_risk_dollars = remaining_weekly_risk
                dd_binding = "weekly_dd"

        # Calculate lot size based on (potentially scaled down) requested_risk_dollars
        max_risk_hard_cap_pct_val = self.config.get("max_risk_hard_cap_pct", 3.0)
        
        # Determine the effective risk percentage for the sizing function
        effective_risk_pct = (requested_risk_dollars / base_balance) * 100.0
        
        total_lots = calculate_lot_size(
            base_balance, effective_risk_pct, entry, sl, symbol,
            max_risk_hard_cap_pct=max_risk_hard_cap_pct_val,
            max_margin_utilisation_pct=self.max_margin_utilisation_pct,
            max_account_leverage=self.max_account_leverage,
            min_deployable_risk_pct=self.min_deployable_risk_pct,
            min_stop_spread_multiple=self.min_stop_spread_multiple,
            cost_config=self.config,
            global_min_sl_pips=self.min_sl_pips,
            min_stop_cost_multiple=self.min_stop_cost_multiple,
        )
        # [I3] internal stages of the call just made — read back immediately so no
        # other sizing call on this context can overwrite it first.
        sizer_diag = get_last_sizing_diagnostics() or {}

        # Calculate actual dollar risk from the sizer output (before TP splits)
        pre_split_risk_dollars = calculate_risk_dollars(total_lots, entry, sl, symbol)

        if total_lots == 0.0:
            # [Task 1.19 / Part 11 §C5] calculate_lot_size can return 0 for several
            # distinct reasons (no instrument profile, stop tighter than the min
            # viable distance, post-cap floor below volume_min) that all used to
            # collapse into one generic "zero_lot_size" — indistinguishable from
            # "no setup today" in the funnel. sizing_diagnostics.refused_reason
            # (set in position_sizer.py) already carries the specific one; surface
            # it here instead of discarding it.
            specific_reason = sizer_diag.get("refused_reason") or "zero_lot_size"
            logger.warning(json.dumps({
                "event": "risk_rejected",
                "reason": specific_reason,
                "balance": account_balance
            }))
            reason_text = {
                "no_instrument_profile": f"No instrument profile or live MT5 data for {symbol} — cannot size a position",
                "stop_below_min_viable": "Stop distance is inside the minimum viable stop (spread/stops-level would consume it)",
                "below_broker_min_lot": "Calculated lot size is below this broker's minimum tradeable volume",
                "zero_tick_or_distance": f"Zero tick_size/tick_value/sl_distance for {symbol}",
                "margin_ceiling_below_min_risk": (
                    f"Margin ceiling truncates realised risk on {symbol} below "
                    f"min_deployable_risk_pct — this account cannot express the requested "
                    f"risk on this symbol at max_margin_utilisation_pct"
                ),
            }.get(specific_reason, "Lot size calculation returned 0")
            return False, reason_text, []

        # 3.5 Prop Firm lot validation — hard rejects if the aggregate lot cap would be
        # breached (see PropFirmValidator.validate_trade).
        if hasattr(self, "prop_firm_validator") and self.prop_firm_validator and self.prop_firm_validator.enabled:
            pf_lot_ok, pf_lot_reason, _ = self.prop_firm_validator.validate_trade(symbol, total_lots)
            if not pf_lot_ok:
                logger.warning(json.dumps({
                    "event": "risk_rejected",
                    "reason": "prop_firm_lot_cap_breach",
                    "details": pf_lot_reason
                }))
                return False, pf_lot_reason, []

        # 4. Multi-Position Splits (TP1/TP2/TP3)
        # [2.12/A9] was hardcoded 1.05 — now RiskParams.post_split_risk_tolerance_pct.
        max_risk_cap_dollars = requested_risk_dollars * (1.0 + self.post_split_risk_tolerance_pct / 100.0)
        liquidity_target = signal_data.get("liquidity_target")
        strategy_id = strategy_id_for_cb
        tp_levels = self.multi_tp.calculate_tp_levels(
            entry, sl, direction, total_lots, symbol, liquidity_target, strategy_id,
            max_risk_cap_dollars=max_risk_cap_dollars,
        )
        # [2.13] requested vs. placed TP count — read back immediately, same
        # pattern as sizer_diag above, so a caller can tell "the risk cap
        # forced fewer TPs than configured" apart from an outright rejection.
        tp_levels_requested = self.multi_tp.last_tp_levels_requested
        tp_levels_placed = self.multi_tp.last_tp_levels_placed

        if not tp_levels:
            return False, "No valid TP levels calculated", []

        actual_total_lots = sum(tp.volume for tp in tp_levels)
        actual_risk_dollars = calculate_risk_dollars(actual_total_lots, entry, sl, symbol)

        # Reject outright if the post-split risk blew past the requested budget by more than a
        # small tolerance. A previous version of this check only logged a warning and let ANY
        # overshoot through — including cases where a corrupted SL (e.g. a stop distance many
        # orders of magnitude too large) produced risk 100-800x the requested budget, which was
        # then nonsensically approved as "within tolerance".
        # [2.12/A9] tolerance_frac replaces the hardcoded 1.05/1.10/1.01 constants —
        # RiskParams.post_split_risk_tolerance_pct (default 5.0) is the real,
        # user-editable ceiling; the reject threshold is double it (same 2x ratio
        # the old 1.05/1.10 pair had) and the warn-only threshold is one tenth of it.
        tolerance_pct = self.post_split_risk_tolerance_pct
        overshoot_pct = (actual_risk_dollars / requested_risk_dollars - 1.0) * 100.0 if requested_risk_dollars > 0 else 0.0
        if actual_risk_dollars > (requested_risk_dollars * (1.0 + 2.0 * tolerance_pct / 100.0)):
            # multi_tp.py distinguishes "overshoot survives only because lot_min flooring
            # can't go any lower" (a small-account/SL-distance limitation) from a
            # genuinely bad SL distance. Surface the clearer reason when that's the case.
            floor_overshoot = getattr(self.multi_tp, "_last_overshoot_reason", None) == "account_too_small_for_sl_distance"
            reason_code = "account_too_small_for_sl_distance" if floor_overshoot else "post_split_risk_overshoot"
            logger.warning(json.dumps({
                "event": "risk_rejected",
                "reason": reason_code,
                "requested_risk_dollars": round(requested_risk_dollars, 2),
                "actual_risk_dollars": round(actual_risk_dollars, 2),
                "overshoot_pct": round(overshoot_pct, 1),
                "actual_total_lots": actual_total_lots,
                "balance": account_balance,
            }))
            if floor_overshoot:
                return False, (
                    f"Account too small for this SL distance on {symbol}: the broker's minimum "
                    f"lot size alone risks ${actual_risk_dollars:.2f}, exceeding the requested "
                    f"${requested_risk_dollars:.2f} budget by {overshoot_pct:.0f}% — rejecting "
                    f"(not a bad SL/TP distance; increase balance/risk% or use a smaller symbol)"
                ), []
            return False, (
                f"Post-split risk ${actual_risk_dollars:.2f} exceeds requested "
                f"${requested_risk_dollars:.2f} by {overshoot_pct:.0f}% — rejecting "
                f"(likely a bad SL/TP distance for {symbol})"
            ), []
        elif actual_risk_dollars > (requested_risk_dollars * (1.0 + 0.2 * tolerance_pct / 100.0)):
            logger.warning(json.dumps({
                "event": "risk_warning_post_split_overshoot",
                "requested_risk_dollars": round(requested_risk_dollars, 2),
                "actual_risk_dollars": round(actual_risk_dollars, 2),
                "actual_total_lots": actual_total_lots,
                "balance": account_balance,
                "note": "Residual overshoot after TP scaling — within tolerance"
            }))

        # 4b. Portfolio governor — cluster exposure + directional netting caps [9.5/9.6]
        if (self.max_cluster_risk_pct > 0 or self.max_net_direction_risk_pct > 0) and base_balance > 0:
            from backend.risk.portfolio_governor import resolve_net_direction_key
            cluster, eff_direction = resolve_net_direction_key(symbol, direction, self.symbol_cluster_overrides)

            if self.max_cluster_risk_pct > 0 and hasattr(self.circuit, "get_cluster_open_risk"):
                prospective = self.circuit.get_cluster_open_risk(cluster, self.symbol_cluster_overrides) + actual_risk_dollars
                prospective_pct = prospective / base_balance * 100.0
                if prospective_pct > self.max_cluster_risk_pct:
                    logger.warning(json.dumps({
                        "event": "risk_rejected", "reason": "cluster_exposure_cap",
                        "cluster": cluster, "prospective_pct": round(prospective_pct, 3),
                        "max_cluster_risk_pct": self.max_cluster_risk_pct,
                    }))
                    return False, (
                        f"Cluster exposure cap breached: {cluster} would reach "
                        f"{prospective_pct:.2f}% > {self.max_cluster_risk_pct}% of balance"
                    ), []

            if self.max_net_direction_risk_pct > 0 and hasattr(self.circuit, "get_net_direction_open_risk"):
                prospective = self.circuit.get_net_direction_open_risk(cluster, eff_direction, self.symbol_cluster_overrides) + actual_risk_dollars
                prospective_pct = prospective / base_balance * 100.0
                if prospective_pct > self.max_net_direction_risk_pct:
                    logger.warning(json.dumps({
                        "event": "risk_rejected", "reason": "net_direction_cap",
                        "cluster": cluster, "direction": eff_direction,
                        "prospective_pct": round(prospective_pct, 3),
                        "max_net_direction_risk_pct": self.max_net_direction_risk_pct,
                    }))
                    return False, (
                        f"Directional netting cap breached: {cluster} {eff_direction} would reach "
                        f"{prospective_pct:.2f}% > {self.max_net_direction_risk_pct}% of balance"
                    ), []

        # 5. Validate minimum RR
        # [5.6/5.7/F6] `last_tp_rr` alone is trivially satisfied whenever the
        # last TP sits at a high RR (e.g. tp3=5R) regardless of how little
        # volume actually reaches it — it was never a real per-trade edge
        # filter (see min_rr's own docstring). `blended_rr` is the
        # volume-weighted RR across every configured TP, which is what
        # `min_rr` now gates on; `last_tp_rr` is kept as a displayed-only
        # figure (last TP RR does not by itself say anything about the trade).
        last_tp_price = tp_levels[-1].tp_price
        last_tp_reward = abs(last_tp_price - entry)
        last_tp_rr = last_tp_reward / risk

        total_tp_volume = sum(tp.volume for tp in tp_levels) or 1.0
        blended_rr = sum((tp.volume / total_tp_volume) * tp.rr_multiplier for tp in tp_levels)
        # blended_rr_be: TPs at/below the BE trigger RR keep their full
        # contribution (they close before BE arms); TPs above it are assumed
        # to scratch at breakeven (0R) once BE has moved the stop — the
        # realistic case, not the optimistic "every TP fills" one.
        be_trigger_rr_for_blend = self.config.get("be_trigger_rr", 1.5)
        blended_rr_be = sum(
            (tp.volume / total_tp_volume) * tp.rr_multiplier
            for tp in tp_levels
            if tp.rr_multiplier <= be_trigger_rr_for_blend
        )

        if blended_rr < self.min_rr:
            logger.warning(json.dumps({
                "event": "risk_rejected",
                "reason": "insufficient_rr",
                "min_rr": self.min_rr,
                "blended_rr": round(blended_rr, 3),
                "last_tp_rr": round(last_tp_rr, 2),
                "entry": entry,
                "sl": sl,
                "last_tp": last_tp_price
            }))
            return False, f"Blended RR {blended_rr:.2f} below minimum {self.min_rr}", []

        group_id = signal_data.get("group_id", "unknown")
        # Removed state modification: self.circuit.position_opened(group_id, len(tp_levels), symbol=symbol)
        # State tracking is now handled directly by the executing engine (live trading) to avoid ghost trades.
        
        if hasattr(self, "prop_firm_validator") and self.prop_firm_validator:
            self.prop_firm_validator.record_trade_opened(symbol, total_lots)
        
        # [I3] Sizing diagnostics — Task 0.3. Attached to signal_data (already the
        # dict that flows into the position/trade record via _create_position's
        # "original_signal" field) rather than changing this method's return
        # signature, so every existing caller keeps working unchanged.
        realised_risk_pct = (actual_risk_dollars / base_balance * 100.0) if base_balance > 0 else 0.0
        binding_constraint = "none"
        if confluence_score is not None and effective_base_risk_pct < base_risk_pct - 1e-9:
            binding_constraint = "confluence"
        elif dd_binding:
            binding_constraint = dd_binding
        elif sizer_diag.get("broker_volume_clamp"):
            # The symbol's own volume_min/volume_max bound before either safety
            # guard even ran — e.g. XRPUSD's lot_max=1000 capping a risk-based
            # calculation that wanted far more, or a tiny-stop calculation
            # wanting less than volume_min.
            binding_constraint = sizer_diag["broker_volume_clamp"]
        elif (
            sizer_diag.get("pre_cap_lots") is not None
            and sizer_diag.get("post_hardcap_lots") is not None
            and sizer_diag["post_hardcap_lots"] < sizer_diag["pre_cap_lots"] - 1e-9
        ):
            binding_constraint = "hard_cap"
        elif (
            sizer_diag.get("post_hardcap_lots") is not None
            and sizer_diag.get("post_margin_lots") is not None
            and sizer_diag["post_margin_lots"] < sizer_diag["post_hardcap_lots"] - 1e-9
        ):
            binding_constraint = "margin"
        elif abs(actual_total_lots - total_lots) > 1e-9:
            # multi_tp's lot_min flooring inflated the split back up, or its
            # max_lot_sizes cap trimmed it down, after the sizer already returned.
            binding_constraint = "lot_min" if actual_total_lots > total_lots else "lot_max"

        signal_data.setdefault("metadata", {})["sizing_diagnostics"] = {
            "requested_risk_pct": round(base_risk_pct, 4),  # [12.6] slot override, or global if none
            "confluence_scaled_pct": round(effective_base_risk_pct, 4),
            "dd_scaled_pct": round(effective_risk_pct, 4),
            "raw_lot": sizer_diag.get("raw_lot"),
            "broker_volume_clamp": sizer_diag.get("broker_volume_clamp"),
            "pre_cap_lots": sizer_diag.get("pre_cap_lots"),
            "post_hardcap_lots": sizer_diag.get("post_hardcap_lots"),
            "post_margin_lots": sizer_diag.get("post_margin_lots"),
            "margin_truncation_pct": sizer_diag.get("margin_truncation_pct"),
            "final_lots": round(actual_total_lots, 4),
            "realised_risk_pct": round(realised_risk_pct, 4),
            "binding_constraint": binding_constraint,
            # [2.13] Distinguishes "the risk cap forced fewer TPs than tp_count
            # requested" from any other rejection path.
            "tp_levels_requested": tp_levels_requested,
            "tp_levels_placed": tp_levels_placed,
            # [5.6/F6] Volume-weighted RR — what min_rr actually gates on now —
            # plus the BE-scratch-adjusted variant and the (cosmetic) last-TP RR.
            "blended_rr": round(blended_rr, 3),
            "blended_rr_be": round(blended_rr_be, 3),
            "last_tp_rr": round(last_tp_rr, 3),
        }

        logger.info(json.dumps({
            "event": "trade_approved",
            "direction": direction,
            "symbol": symbol,
            "entry": entry,
            "sl": sl,
            "risk_dollars": actual_risk_dollars,
            "lots": total_lots,
            "tp_count": len(tp_levels),
            "last_tp_rr": round(last_tp_rr, 2),
            "binding_constraint": binding_constraint,
        }))
        return True, "APPROVED", tp_levels

    def manage_open_position(
        self,
        position: dict[str, Any],
        current_price: float,
        atr_value: float = 0.0,
        swing_points: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Evaluate BE and trailing stops for an open position.
        Returns list of required actions.
        Source: RiskManagement_Spec.md Section 3.4 — Trailing Stop State Machine
        """
        actions = []
        direction = position.get("direction", "BUY")
        entry = position.get("entry_price", 0.0)
        current_sl = position.get("stop_loss", 0.0)
        # [4.1/F1] `original_sl` did not exist on any position dict prior to
        # this Phase 4 pass — every call here fell through to `current_sl`,
        # which BE/trailing had already mutated, collapsing the risk-distance
        # denominator (unrealized_r read ~15-20R instead of ~1.5R and
        # trail_activation_rr always trivially passed). `initial_stop_loss` is
        # the field both backtester/engine.py and portfolio_engine.py have
        # always written; `original_sl` is now ALSO written directly
        # (backtester/engine.py::_create_position, portfolio_engine.py) as the
        # canonical name this function reads — both are accepted so a position
        # dict from either write site resolves correctly.
        original_sl = position.get("original_sl") or position.get("initial_stop_loss") or current_sl
        pip_value = get_pip_size(position.get("symbol", ""))
        tp_level = position.get("tp_level", 1)
        be_applied = position.get("be_applied", False)
        tp1_hit = position.get("tp1_hit", False)
        trail_method = position.get("trail_method")
        highest = position.get("highest_price", current_price)
        lowest = position.get("lowest_price", current_price)
        
        from backend.risk.multi_tp import _is_buy
        is_buy = _is_buy(direction)

        # Update highest/lowest price tracking
        if is_buy:
            if current_price > highest:
                actions.append({"action": "UPDATE_HIGHEST", "price": current_price})
        else:
            if current_price < lowest:
                actions.append({"action": "UPDATE_LOWEST", "price": current_price})

        # Step 1: Check Break-Even (if not already applied)
        if not be_applied:
            new_sl = self.breakeven.check_breakeven(
                entry_price=entry,
                current_price=current_price,
                current_sl=current_sl,
                stop_loss=original_sl,
                direction=direction,
                pip_value=pip_value,
                live_spread=position.get("live_spread", pip_value),
                atr=atr_value if atr_value > 0 else abs(entry - original_sl) * 0.1,
                be_already_applied=be_applied,
                tp1_hit=tp1_hit,
            )
            if new_sl is not None:
                actions.append({"action": "MODIFY_SL", "new_sl": new_sl, "reason": "BREAKEVEN"})
                return actions  # BE takes priority on this tick

        # Step 2: Check Trailing (only if trail method is assigned)
        # [4.6/D7/F3] Was hardcoded `if trail_method and be_applied:` — trailing
        # could never start before break-even, even when the user wanted an
        # earlier trail. Now gated on `trail_require_be_first` (default False).
        be_gate_ok = (not self.trail_require_be_first) or be_applied
        if trail_method and be_gate_ok and self.trail_mode != "NONE":
            # [5.4/Part4] `trail_mode` selects the activation condition:
            # RR = today's behaviour (unrealized_r threshold only), TP_HIT =
            # a specific TP level closing, EITHER = whichever comes first.
            # trail_trigger_rr is the new name; trail_activation_rr is kept as
            # the value actually read for backward compatibility (5.1's alias).
            risk_distance = abs(entry - original_sl)
            trail_trigger_rr = self.config.get("trail_trigger_rr", self.config.get("trail_activation_rr", 1.0))
            rr_reached = False
            if risk_distance > 0:
                if is_buy:
                    unrealized_r = (current_price - entry) / risk_distance
                else:
                    unrealized_r = (entry - current_price) / risk_distance
                rr_reached = unrealized_r >= trail_trigger_rr

            # TP_HIT activation only understands TP1 today — `tp1_hit` is the
            # only per-level hit signal threaded onto the position dict
            # (matching be_mode's existing TP_HIT scope). A configured
            # trail_trigger_tp_level other than 1 cannot be evaluated here
            # and is treated as not-yet-reached rather than guessed.
            tp_hit_reached = tp1_hit if self.trail_trigger_tp_level == 1 else False

            if self.trail_mode == "RR":
                activated = rr_reached
            elif self.trail_mode == "TP_HIT":
                activated = tp_hit_reached
            else:  # EITHER
                activated = rr_reached or tp_hit_reached

            if not activated:
                return actions  # Trailing not yet activated

            # §2.2 fix: pass tp_level for per-TP multiplier resolution
            new_sl = self.trailing.calculate_trailing_sl(
                method=trail_method,
                direction=direction,
                current_price=current_price,
                current_sl=current_sl,
                pip_value=pip_value,
                highest_price=highest,
                lowest_price=lowest,
                atr_value=atr_value,
                swing_points=swing_points,
                tp_level=tp_level,
            )
            if new_sl is not None:
                actions.append({"action": "MODIFY_SL", "new_sl": new_sl, "reason": "TRAIL"})

        return actions

    def on_position_opened(self, group_id: str, sub_trade_count: int, symbol: str = "", strategy_id: str = "", direction: str = "", slot_id: str = ""):
        """Track a new position opening (unused in backtest)."""
        self.circuit.position_opened(group_id, sub_trade_count, symbol, strategy_id=strategy_id, direction=direction, slot_id=slot_id)

    def on_position_closed(self, group_id: str, pnl: float, current_time: datetime | None = None):
        """Update circuit breaker state after a position closes (unused in backtest)."""
        self.circuit.position_closed(group_id, pnl, current_time)

    def on_backtest_position_closed(self, group_id: str, pnl: float, current_time: datetime | None = None, symbol: str = "", lots: float = 0.0):
        """Feed closed trade PnL directly into Circuit Breaker during backtesting."""
        if hasattr(self.circuit, "record_backtest_close"):
            self.circuit.record_backtest_close(group_id, pnl, current_time)
            
        if hasattr(self, "prop_firm_validator") and self.prop_firm_validator:
            self.prop_firm_validator.record_trade_closed(symbol, lots, pnl)
