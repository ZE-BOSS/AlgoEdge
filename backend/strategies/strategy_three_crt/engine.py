"""
backend/strategies/strategy_six_crt/engine.py

CRT (Candle Range Theory) Strategy Orchestrator
Source: CRT_Strategy_Spec.md
"""

from datetime import datetime, time
from typing import Any

import pandas as pd
import pytz

from backend.services.bot_service import bot_service
from backend.strategies.base_strategy import BaseStrategy, TradeSignal
from backend.strategies.core.markings import (
    ROLE_CONFLUENCE,
    ROLE_CONTEXT,
    ROLE_INVALIDATION,
    ROLE_TRIGGER,
    MarkingCollector,
    ts,
)
from backend.strategies.core.market_structure import MarketStructureDetector
from backend.strategies.registry import register_strategy
from backend.utils.logger import get_logger

logger = get_logger(__name__)

SPEC_DEFAULTS = {
    "htf_timeframe": "H1",
    "ltf_timeframe": "M5",
    "target_r_multiple": 1.5,
    "max_trades_per_session": 1,
    "session_start": "09:30",
    "session_cutoff": "12:00"
}

@register_strategy("CRT_v1")
class CRTEngine(BaseStrategy):
    def __init__(self, config: Any):
        super().__init__(config)
        self.params = getattr(config, 'crt', None)
        self.context: dict[str, Any] = {}

        # Per-setup CRT state, namespaced by symbol (same pattern as
        # strategy_apa/engine.py's self.state[symbol]). Current call sites only
        # ever instantiate one CRTEngine per symbol, so this wasn't actively
        # broken — but keeping state un-namespaced was fragile against that
        # assumption ever changing, so state for every symbol lives in its own
        # dict here instead of directly on `self`.
        self.state: dict[str, dict[str, Any]] = {}

    def _init_state(self, symbol: str) -> dict[str, Any]:
        if symbol not in self.state:
            self.state[symbol] = {
                "ms_detector": MarketStructureDetector(swing_length=5, min_bos_count=1),
                "c1": None,
                "c2_trigger": None,
                "c2_trigger_htf_bars_survived": 0,  # [6.10/S12]
                "trades_today": 0,
                "last_trade_date": None,
                # Live bar-time deduplication: mirrors backtester's prev_time_by_tf
                # guard. Prevents the 60-second scan loop from re-presenting the
                # same closed HTF candle and triggering the c2_trigger timeout
                # prematurely.
                "last_htf_bar_time": {},
            }
        return self.state[symbol]

    async def initialize(self):
        logger.info("CRTEngine initialized")

    def get_required_timeframes(self) -> list[str]:
        htf = self.params.htf_timeframe if getattr(self, 'params', None) else SPEC_DEFAULTS['htf_timeframe']
        ltf = self.params.ltf_timeframe if getattr(self, 'params', None) else SPEC_DEFAULTS['ltf_timeframe']
        return [htf, ltf]

    def _is_within_session(self, current_time: datetime, symbol: str) -> bool:
        """Check if current time is within NY session. Bypass for synthetics if configured."""
        # Bypass for synthetics (Crash/Boom/Volatility/Jump)
        is_synthetic = any(k in symbol.upper() for k in ["CRASH", "BOOM", "VOLATILITY", "JUMP", "STEP"])
        bypass_synthetics = self.params.bypass_session_synthetics if getattr(self, 'params', None) else True
        if is_synthetic and bypass_synthetics:
            return True
            
        ny_tz = pytz.timezone('America/New_York')
        if current_time.tzinfo is None:
            current_time = current_time.tz_localize('UTC')
        ny_time = current_time.astimezone(ny_tz).time()
        
        start_str = self.params.session_start if getattr(self, 'params', None) else SPEC_DEFAULTS['session_start']
        cutoff_str = self.params.session_cutoff if getattr(self, 'params', None) else SPEC_DEFAULTS['session_cutoff']
        
        try:
            start = time(int(start_str.split(':')[0]), int(start_str.split(':')[1]))
            cutoff = time(int(cutoff_str.split(':')[0]), int(cutoff_str.split(':')[1]))
            return start <= ny_time <= cutoff
        except:
            return True

    def _get_htf_bias(self, state: dict[str, Any]) -> str:
        """Use MS detector to get bias on HTF."""
        return state["ms_detector"].get_bias()

    async def on_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame) -> TradeSignal | None:
        # [T1.3] Open a confluence-telemetry record for this bar.
        # No-op unless self.gates.enabled (live default is off).
        self.begin_candidate(
            symbol, timeframe,
            bar_time=candles.index[-1] if candles is not None and len(candles) else None,
        )
        if not self.is_backtesting:
            self.run_logs = []

        if len(candles) < 10:
            return None

        state = self._init_state(symbol)

        htf = self.params.htf_timeframe if getattr(self, 'params', None) else SPEC_DEFAULTS['htf_timeframe']
        ltf = self.params.ltf_timeframe if getattr(self, 'params', None) else SPEC_DEFAULTS['ltf_timeframe']

        current_bar = candles.iloc[-1]
        dt = current_bar.name if isinstance(current_bar.name, pd.Timestamp) else pd.to_datetime('now')

        if state["last_trade_date"] != dt.date():
            state["trades_today"] = 0
            state["last_trade_date"] = dt.date()
            state["ms_detector"] = MarketStructureDetector(swing_length=5, min_bos_count=1)
            state["c1"] = None
            state["c2_trigger"] = None

        max_trades = self.params.max_trades_per_session if getattr(self, 'params', None) else SPEC_DEFAULTS['max_trades_per_session']
        if not self.gate("session_trade_cap", state["trades_today"] < max_trades):
            return None

        in_session = self.gate("session_filter", self._is_within_session(dt, symbol))

        if timeframe == htf:
            # ── Bar-time deduplication (live mode) ──────────────────────────────
            # The live scan loop calls on_bar(H1) every 60s with the same closed
            # candle for an entire hour.  Guard against this so the c2_trigger
            # timeout only fires when a genuinely NEW H1 candle closes.
            _current_bar_ts = current_bar.name
            _last_htf_ts = state["last_htf_bar_time"].get(htf)
            if _last_htf_ts is not None and _current_bar_ts == _last_htf_ts:
                return None  # Same H1 bar — preserve c2_trigger, skip HTF logic
            state["last_htf_bar_time"][htf] = _current_bar_ts
            # ────────────────────────────────────────────────────────────────────

            # Update HTF market structure
            state["ms_detector"].update(candles)

            # Evaluate C1/C2 logic
            if state["c1"] is None:
                state["c1"] = {"high": float(current_bar['high']), "low": float(current_bar['low'])}
                self.log_event(f"New C1 Candidate set on HTF: High {state['c1']['high']}, Low {state['c1']['low']}")
            else:
                if state["c2_trigger"] is not None:
                    # [6.10/S12] Grace window — was 0 (any trigger not fired before
                    # the very next HTF close was discarded outright).
                    grace = getattr(self.params, "trigger_grace_bars", 0) or 0
                    survived = state.get("c2_trigger_htf_bars_survived", 0)
                    if survived < grace:
                        state["c2_trigger_htf_bars_survived"] = survived + 1
                        self.log_event(
                            f"Trigger not yet fired at HTF close ({survived + 1}/{grace} grace bars used) — keeping setup alive.",
                        )
                        return None
                    # Trigger timeout - invalidate if the LTF trigger still hasn't fired
                    self.log_event("Trigger timeout - LTF did not fire within the grace window. Invalidating setup.")
                    state["c2_trigger"] = None
                    state["c2_trigger_htf_bars_survived"] = 0
                    state["c1"] = {"high": float(current_bar['high']), "low": float(current_bar['low'])}
                    return None

                # We have a C1, check if this bar is a valid C2
                c1_high = state["c1"]["high"]
                c1_low = state["c1"]["low"]
                c2_high = float(current_bar['high'])
                c2_low = float(current_bar['low'])
                c2_close = float(current_bar['close'])

                # Check for ambiguous intrabar double sweep
                if not self.gate("c2_not_ambiguous", not (c2_high > c1_high and c2_low < c1_low)):
                    self.log_event("Ambiguous C2 (swept both sides of C1). Invalidating and setting new C1.")
                    state["c1"] = {"high": float(current_bar['high']), "low": float(current_bar['low'])}
                    return None

                bias = self._get_htf_bias(state)

                # [6.8/S9] MarketStructureDetector.trend only ever produces "NEUTRAL" /
                # "BULLISH" / "BEARISH" — it never returns "FLAT" — so check against
                # "NEUTRAL" (a flat/undetermined bias) instead. Was a hard BLOCK here
                # (254/900 evaluations on the forensic NDX log, the single largest
                # rejection category); now configurable via bias_neutral_mode.
                bias_neutral_mode = getattr(self.params, "bias_neutral_mode", "REDUCED_SIZE") if getattr(self, 'params', None) else "BLOCK"
                if not self.gate("htf_bias_confirmed", not (bias == "NEUTRAL" and bias_neutral_mode == "BLOCK"),
                                  detail=f"bias={bias}"):
                    self.log_event("HTF bias is NEUTRAL (no confirmed trend) — BLOCK mode, skipping C2 evaluation, setting new C1 candidate.")
                    state["c1"] = {"high": float(current_bar['high']), "low": float(current_bar['low'])}
                    return None

                valid_bullish = (c2_low < c1_low < c2_close < c1_high)
                valid_bearish = c2_high > c1_high and (c1_low < c2_close < c1_high)
                # The core CRT confluence: C2 sweeps C1's extreme then closes
                # back inside the range.
                self.gate("c2_sweep_and_reclaim", valid_bullish or valid_bearish)

                # A NEUTRAL bias no longer hard-fails the direction match when
                # bias_neutral_mode allows it through — the C2 sweep's own direction
                # substitutes for HTF confirmation, at reduced size unless ALLOW.
                bias_confirmed_bullish = bias == "BULLISH"
                bias_confirmed_bearish = bias == "BEARISH"
                neutral_allows = bias == "NEUTRAL" and bias_neutral_mode in ("REDUCED_SIZE", "ALLOW")
                size_modifier = 1.0
                if bias == "NEUTRAL" and bias_neutral_mode == "REDUCED_SIZE":
                    size_modifier = getattr(self.params, "bias_neutral_size_modifier", 0.5)

                if valid_bullish and (bias_confirmed_bullish or neutral_allows):
                    if in_session:
                        self.log_event(f"Valid Bullish C2 sweep! HTF Bias: {bias}. Trigger level set to {c2_high}.")
                        state["c2_trigger"] = {
                            "direction": "BUY", "level": c2_high, "c1_extreme": c1_high,
                            "size_modifier": size_modifier, "bias_at_trigger": bias,
                            # [V1] Range geometry captured HERE, on the HTF bar that
                            # actually formed it — `state["c1"]` is cleared before the
                            # LTF branch runs, so without this the chart could only
                            # ever show the entry, never the range it broke out of.
                            "geom": {
                                "c1_high": c1_high, "c1_low": c1_low,
                                "c2_high": c2_high, "c2_low": c2_low,
                                "c2_close": c2_close,
                                "c2_time": ts(candles.index[-1]),
                                "htf": htf,
                            },
                        }
                        state["c2_trigger_htf_bars_survived"] = 0
                    else:
                        self.log_event("Valid C2 but out of session. Ignoring.")
                        state["c1"] = {"high": float(current_bar['high']), "low": float(current_bar['low'])}
                elif valid_bearish and (bias_confirmed_bearish or neutral_allows):
                    if in_session:
                        self.log_event(f"Valid Bearish C2 sweep! HTF Bias: {bias}. Trigger level set to {c2_low}.")
                        state["c2_trigger"] = {
                            "direction": "SELL", "level": c2_low, "c1_extreme": c1_low,
                            "size_modifier": size_modifier, "bias_at_trigger": bias,
                            # [V1] See the bullish branch — same reason.
                            "geom": {
                                "c1_high": c1_high, "c1_low": c1_low,
                                "c2_high": c2_high, "c2_low": c2_low,
                                "c2_close": c2_close,
                                "c2_time": ts(candles.index[-1]),
                                "htf": htf,
                            },
                        }
                        state["c2_trigger_htf_bars_survived"] = 0
                    else:
                        self.log_event("Valid C2 but out of session. Ignoring.")
                        state["c1"] = {"high": float(current_bar['high']), "low": float(current_bar['low'])}
                else:
                    self.log_event("C2 did not match C1/bias criteria. Setting new C1 candidate.")
                    state["c1"] = {"high": float(current_bar['high']), "low": float(current_bar['low'])}

        elif timeframe == ltf:
            # Monitor for trigger
            if state["c2_trigger"] is not None:
                current_price = float(current_bar['close'])
                direction = state["c2_trigger"]["direction"]
                trigger_lvl = state["c2_trigger"]["level"]
                tp = state["c2_trigger"]["c1_extreme"]
                # [6.8/S9] Captured before the trigger dict is cleared below.
                _trigger_size_modifier = state["c2_trigger"].get("size_modifier", 1.0)
                _trigger_bias = state["c2_trigger"].get("bias_at_trigger")
                _trigger_geom = state["c2_trigger"].get("geom") or {}

                triggered = False
                if (direction == "BUY" and current_price > trigger_lvl) or (direction == "SELL" and current_price < trigger_lvl):
                    triggered = True

                if triggered:
                    self.log_event(f"CRT LTF Trigger fired! Executing {direction}.")
                    state["c2_trigger"] = None # Clear trigger
                    state["c1"] = None
                    state["trades_today"] += 1

                    target_r = self.params.target_r_multiple if getattr(self, 'params', None) else SPEC_DEFAULTS['target_r_multiple']
                    tp_dist = abs(tp - current_price)
                    # Per spec (CRT_Strategy_Spec.md Section 6): SL is derived backward from TP.
                    sl_dist = tp_dist / target_r

                    # ── Minimum SL floor (addition above spec) ──────────────────────
                    # Small C1 candles produce spec-correct but tiny SL distances,
                    # which cause extreme lot sizes in the risk engine.
                    # Apply configurable floors: min_sl_pips (hard pips minimum)
                    # and sl_atr_mult (fraction of recent ATR). Whichever is larger wins.
                    from backend.risk.position_sizer import get_pip_size
                    pip_size = get_pip_size(symbol)
                    min_sl_pips_val = getattr(self.params, 'min_sl_pips', 15.0) if self.params else 15.0
                    sl_atr_mult_val = getattr(self.params, 'sl_atr_mult', 1.0) if self.params else 1.0

                    min_sl_from_pips = min_sl_pips_val * pip_size

                    # Standard ATR (Average True Range) over 14 periods
                    try:
                        import numpy as np
                        if len(candles) >= 15:
                            highs = candles['high'].values[-14:].astype(float)
                            lows  = candles['low'].values[-14:].astype(float)
                            prev_closes = candles['close'].values[-15:-1].astype(float)
                            
                            tr1 = highs - lows
                            tr2 = np.abs(highs - prev_closes)
                            tr3 = np.abs(lows - prev_closes)
                            
                            true_range = np.maximum(tr1, np.maximum(tr2, tr3))
                            atr_approx = float(true_range.mean())
                        else:
                            atr_approx = tp_dist  # fallback: use TP dist as proxy
                    except Exception:
                        atr_approx = tp_dist

                    min_sl_from_atr = atr_approx * sl_atr_mult_val
                    # Apply the most restrictive floor (largest SL distance wins)
                    sl_dist = max(sl_dist, min_sl_from_pips, min_sl_from_atr)

                    # If flooring the SL makes the TP distance shorter than 1R, extend TP
                    # so the trade still meets the target_r ratio (keeps the edge intact).
                    # This replaces the spec-mandated TP (C1's real opposite extreme) with a
                    # synthetic R-multiple target — flag it via tp_source so downstream
                    # consumers (backtests/analytics) can tell floor-adjusted signals apart
                    # from genuine CRT signals instead of this being silent.
                    tp_source = "c1_extreme"
                    if tp_dist < sl_dist * target_r:
                        if direction == "BUY":
                            tp = current_price + sl_dist * target_r
                        else:
                            tp = current_price - sl_dist * target_r
                        tp_source = "floored"
                    # ────────────────────────────────────────────────────────────────

                    if direction == "BUY":
                        sl = current_price - sl_dist
                    else:
                        sl = current_price + sl_dist

                    # ── Structural-TP declaration (audit §10.10) ─────────────────────
                    # CRT reverse-derives its SL from the TP (spec §6) precisely so the
                    # structural target — C1's opposite extreme — lands at exactly
                    # target_r_multiple. The RiskParams TP ladder then overrides
                    # `take_profit` with a 1.5R/3R/5R grid whose upper tiers sit BEYOND
                    # C1's extreme, i.e. beyond the only level this setup's thesis says
                    # price is travelling to. With tp_splits at 50/30/20 that strands
                    # 50% of every CRT position on targets the strategy does not believe
                    # in.
                    #
                    # Resolving that is a product decision that lives in risk/multi_tp.py
                    # (exempt CRT from the grid, or place the SL structurally and let the
                    # grid own targets) and is deliberately NOT made here. What IS fixed
                    # here is that the information was previously unrecoverable
                    # downstream: the fields below state, explicitly and per-signal, what
                    # the structural target was and at what R it sits, so the grid, the
                    # backtester and the reports can distinguish a structural CRT target
                    # from a grid-derived one instead of silently conflating them.
                    structural_tp_rr = (abs(tp - current_price) / sl_dist) if sl_dist > 0 else None

                    # [V1 §C.6] Chart markings. CRT's whole thesis is "C2 swept
                    # C1's extreme and closed back inside, so price is going to
                    # C1's opposite extreme" — none of which was visible on the
                    # chart before. Every element below is the actual number the
                    # setup used, not a re-derivation.
                    mk = MarkingCollector(ltf)
                    entry_t = ts(candles.index[-1])
                    c2_t = _trigger_geom.get("c2_time", entry_t)

                    if _trigger_geom:
                        mk.range_box(
                            f"C1 range ({_trigger_geom.get('htf', htf)})",
                            _trigger_geom["c1_high"], _trigger_geom["c1_low"], c2_t,
                            end_time=entry_t, role=ROLE_CONFLUENCE,
                            c1_high=_trigger_geom["c1_high"], c1_low=_trigger_geom["c1_low"],
                            range_size=round(abs(_trigger_geom["c1_high"] - _trigger_geom["c1_low"]), 6),
                        )
                        swept_level = _trigger_geom["c1_low"] if direction == "BUY" else _trigger_geom["c1_high"]
                        mk.liquidity(
                            "Swept liquidity (C1 extreme)", swept_level, c2_t,
                            role=ROLE_TRIGGER, end_time=entry_t,
                            side="sell-side" if direction == "BUY" else "buy-side",
                            swept_by=_trigger_geom["c2_low"] if direction == "BUY" else _trigger_geom["c2_high"],
                            sweep_depth=round(
                                abs(swept_level - (_trigger_geom["c2_low"] if direction == "BUY"
                                                  else _trigger_geom["c2_high"])), 6),
                        )
                        mk.structure(
                            "C2 sweep + close back inside", c2_t,
                            price=_trigger_geom["c2_close"], role=ROLE_TRIGGER,
                            timeframe=_trigger_geom.get("htf", htf),
                            c2_high=_trigger_geom["c2_high"], c2_low=_trigger_geom["c2_low"],
                            c2_close=_trigger_geom["c2_close"],
                            rule="c2_low < c1_low < c2_close < c1_high" if direction == "BUY"
                                 else "c2_high > c1_high and c1_low < c2_close < c1_high",
                        )

                    mk.level(
                        "C2 breakout trigger", trigger_lvl, entry_t, role=ROLE_TRIGGER,
                        color="rgba(16,185,129,0.9)",
                        crossed_at=round(current_price, 6),
                        direction=direction,
                    )
                    mk.structure(
                        f"HTF bias: {_trigger_bias}", c2_t, role=ROLE_CONFLUENCE,
                        timeframe=htf, bias=_trigger_bias,
                        neutral_mode=getattr(self.params, "bias_neutral_mode", None) if getattr(self, "params", None) else None,
                        size_modifier=_trigger_size_modifier,
                    )
                    mk.level(
                        "Structural TP (C1 opposite extreme)", tp, entry_t,
                        role=ROLE_CONTEXT, color="rgba(16,185,129,0.9)",
                        tp_source=tp_source, target_r=target_r,
                        rr=round(structural_tp_rr, 3) if structural_tp_rr is not None else None,
                    )
                    mk.level(
                        "Stop loss", sl, entry_t, role=ROLE_INVALIDATION,
                        color="rgba(239,68,68,0.9)",
                        derivation="TP distance / target_r (spec §6), then floored",
                        sl_distance=round(sl_dist, 6),
                        floor_pips=min_sl_pips_val, floor_atr_mult=sl_atr_mult_val,
                        floor_binding=(
                            "atr" if sl_dist == min_sl_from_atr and min_sl_from_atr > min_sl_from_pips
                            else ("pips" if sl_dist == min_sl_from_pips else "none")
                        ),
                    )

                    return self._tag_signal(TradeSignal(
                        strategy_id="CRT_v1",
                        symbol=symbol,
                        direction=direction,
                        signal_type="BREAKOUT_ENTRY",
                        entry_price=current_price,
                        stop_loss=sl,
                        take_profit=tp,
                        confluence_score=90,
                        timeframe=ltf,
                        timestamp=candles.index[-1].timestamp(),
                        metadata={
                            "reason": "CRT Setup C1/C2. NY Session.",
                            "htf": htf,
                            "tp_source": tp_source,
                            # [6.8/S9] size_modifier < 1.0 when this setup traded
                            # without HTF bias confirmation (bias_neutral_mode).
                            "size_modifier": _trigger_size_modifier,
                            "bias_at_trigger": _trigger_bias,
                            # The strategy's own target, kept intact regardless of what
                            # the risk grid later does to `take_profit`.
                            "structural_tp": float(tp),
                            "structural_tp_rr": round(structural_tp_rr, 3) if structural_tp_rr is not None else None,
                            "tp_is_structural": tp_source == "c1_extreme",
                            # Declares that this strategy's TP is thesis-bearing, not a
                            # placeholder: any R-grid tier beyond structural_tp_rr is
                            # unreachable by CRT's own hypothesis. Consumed by nothing
                            # today — honouring it requires risk/multi_tp.py, which is a
                            # separate, deliberate product decision (audit §10.10).
                            "strategy_owns_tp": True,
                            "max_meaningful_rr": round(structural_tp_rr, 3) if structural_tp_rr is not None else None,
                            # [V1] Chart geometry — see strategies/core/markings.py.
                            **mk.as_metadata(),
                        }
                    ))

        return None

    async def on_tick(self, symbol: str, tick: dict[str, Any]) -> None:
        pass
