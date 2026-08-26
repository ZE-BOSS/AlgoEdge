import pandas as pd
import pytz

from backend.core.config_schema import UserConfigV2
from backend.strategies.base_strategy import BaseStrategy, TradeSignal
from backend.strategies.core.markings import (
    ROLE_CONFLUENCE,
    ROLE_CONTEXT,
    ROLE_INVALIDATION,
    ROLE_TRIGGER,
    MarkingCollector,
    ts,
)
from backend.strategies.core.swing_structure import calculate_atr
from backend.strategies.registry import register_strategy
from backend.risk.position_sizer import get_pip_size
from backend.utils.logger import get_logger

logger = get_logger(__name__)

@register_strategy("NYOpenRetest_v1")
class NYOpenRetestEngine(BaseStrategy):
    """
    Strategy 3: 8:00 AM Session-Range Break & Retest
    """
    def __init__(self, config: UserConfigV2):
        super().__init__(config)
        self.params = config.ny_open_retest
        self.state = {}
        
    def _init_state(self, symbol: str):
        if symbol not in self.state:
            self.state[symbol] = {
                "status": "MARK_RANGE",
                "range_high": None,
                "range_low": None,
                "range_mid": None,
                "bias": None,
                "current_day": None
            }

    def get_required_timeframes(self) -> list[str]:
        # Spec (strategy-3-nyopen-break-retest.md): only M15 (range marking) and M5 (break+retest)
        return ["M15", "M5"]

    async def on_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame) -> TradeSignal | None:
        self._init_state(symbol)
        state = self.state[symbol]
        
        current_time = candles.index[-1]
        latest = candles.iloc[-1]
        
        # Reset state at the start of a new day
        current_day = current_time.date()
        if state["current_day"] != current_day:
            state["current_day"] = current_day
            state["status"] = "MARK_RANGE"
            state["range_high"] = None
            state["range_low"] = None
            state["range_mid"] = None
            state["bias"] = None

        ny_tz = pytz.timezone('America/New_York')
        if current_time.tzinfo is None:
            current_time = current_time.tz_localize('UTC')
        ny_time = current_time.astimezone(ny_tz)
        time_str = ny_time.strftime("%H:%M")

        # 1. Mark the Range on M15
        if timeframe == "M15":
            if state["status"] == "MARK_RANGE":
                # Only accumulate candles that fall inside [range_window_start, range_window_end)
                # — exclusive upper bound so exactly one M15 candle (e.g. 08:00-08:14:59) is
                # captured, not two (an inclusive end would also absorb the 08:15 candle).
                if self.params.range_window_start <= time_str < self.params.range_window_end:
                    state["range_high"] = max(state["range_high"], latest["high"]) if state["range_high"] else latest["high"]
                    state["range_low"] = min(state["range_low"], latest["low"]) if state["range_low"] else latest["low"]
                elif time_str >= self.params.range_window_end and state["range_high"] is not None:
                    state["range_mid"] = (state["range_high"] + state["range_low"]) / 2.0
                    state["range_marked_time"] = candles.index[-1]  # [V1] chart anchor
                    state["status"] = "AWAIT_BREAK"
                    self.log_event(f"[{symbol}] NY Open Range marked: {state['range_high']} - {state['range_low']} (Mid: {state['range_mid']})", category="NY_OPEN")


        # 2. Break and Retest on M5
        elif timeframe == "M5":
            # Session expiration check
            if time_str >= self.params.session_end and state["status"] not in ["MARK_RANGE", "DONE"]:
                state["status"] = "DONE"
                self.log_event(f"[{symbol}] Session ended, stopping for the day.", category="NY_OPEN")
                return None
                
            if state["status"] == "AWAIT_BREAK":
                if time_str < self.params.earliest_valid_break_time:
                    return None
                    
                if latest["close"] > state["range_high"]:
                    state["bias"] = "BUY"
                    state["status"] = "AWAIT_RETEST"
                    state["break_time"] = candles.index[-1]  # [V1] chart anchor
                    state["break_close"] = float(latest["close"])
                    state["breakout_extreme"] = candles.iloc[-10:]["low"].min()  # Lowest point of breakout swing
                    self.log_event(f"[{symbol}] NY Open bullish break detected. Awaiting retest to {state['range_mid']}", category="NY_OPEN")
                elif latest["close"] < state["range_low"]:
                    state["bias"] = "SELL"
                    state["status"] = "AWAIT_RETEST"
                    state["break_time"] = candles.index[-1]  # [V1] chart anchor
                    state["break_close"] = float(latest["close"])
                    state["breakout_extreme"] = candles.iloc[-10:]["high"].max() # Highest point of breakout swing
                    self.log_event(f"[{symbol}] NY Open bearish break detected. Awaiting retest to {state['range_mid']}", category="NY_OPEN")

            elif state["status"] == "AWAIT_RETEST":
                triggered = False
                if (state["bias"] == "BUY" and latest["low"] <= state["range_mid"]) or (state["bias"] == "SELL" and latest["high"] >= state["range_mid"]):
                    triggered = True
                    
                if triggered:
                    entry = state["range_mid"]
                    breakout_extreme = state.get("breakout_extreme")
                    if breakout_extreme is None:
                        # Safety guard: state is incomplete, skip this bar
                        return None
                    
                    # Convert points to price delta
                    pip_size = get_pip_size(symbol)
                    buffer = self.params.stop_buffer_points * pip_size

                    # breakout_extreme is captured near the breakout swing (close to
                    # range_high/low), while entry is range_mid — breakout_extreme can end up
                    # closer to entry than the buffer itself, putting the naive SL on the wrong
                    # side of entry (e.g. above entry on a BUY). Anchor off whichever of
                    # breakout_extreme/entry is further out, so the SL is always at least
                    # `buffer` away from entry on the correct side.
                    if state["bias"] == "BUY":
                        stop_loss = min(breakout_extreme, entry) - buffer
                    else:
                        stop_loss = max(breakout_extreme, entry) + buffer

                    # sl_buffer_atr_mult widens the structural SL further by a multiple of
                    # ATR(14), on top of the fixed-points buffer above (0.0 = disabled).
                    if getattr(self.params, "sl_buffer_atr_mult", 0.0):
                        atr = calculate_atr(candles, 14)
                        atr_buffer = self.params.sl_buffer_atr_mult * atr
                        if state["bias"] == "BUY":
                            stop_loss -= atr_buffer
                        else:
                            stop_loss += atr_buffer

                    # ── Target resolution (audit §10.9) ──────────────────────────
                    # This used to be `fixed_target_points * pip_size`, unconditionally.
                    # 50 "points" -> 50 PIPS on USDCHF, measured from range_mid inside a
                    # 09:30-11:00 window, on a pair whose entire AVERAGE DAILY RANGE is
                    # ~55 pips: effectively unreachable, so in the real runs every exit
                    # came from the SL, the dynamic_target_override swing, or the session
                    # close, and the documented target was decorative. An R-multiple is
                    # the only target formulation simultaneously correct on NQ, USDCHF
                    # and XAUUSD, so target_mode="rr" is now the default path.
                    #
                    # The R-multiple is measured AFTER stop_buffer_points and
                    # sl_buffer_atr_mult have both been applied above, so it reflects the
                    # stop the trade is actually taken on.
                    target_mode = str(getattr(self.params, "target_mode", "points") or "points").lower()
                    target = 0.0
                    target_source = "fixed_points"
                    if target_mode == "rr":
                        target_rr = getattr(self.params, "target_rr", 0.0) or 0.0
                        sl_dist = abs(entry - stop_loss)
                        target = sl_dist * target_rr
                        target_source = "rr"
                        if target <= 0:
                            # Degenerate config (target_rr <= 0) or a zero-width stop —
                            # fall back to the legacy points path rather than emitting a
                            # signal whose TP sits on top of the entry.
                            self.log_event(
                                f"[{symbol}] target_mode='rr' produced a non-positive target "
                                f"(sl_dist={sl_dist:.5f}, target_rr={target_rr}) — falling back "
                                f"to fixed_target_points.",
                                level="WARN",
                                category="NY_OPEN",
                            )
                            target = self.params.fixed_target_points * pip_size
                            target_source = "fixed_points_fallback"
                    else:
                        target = self.params.fixed_target_points * pip_size

                    take_profit = entry + target if state["bias"] == "BUY" else entry - target

                    # dynamic_target_override applies to WHICHEVER target results above:
                    # if the nearer HTF swing is closer than the resolved target, use it.
                    if getattr(self.params, 'dynamic_target_override', True):
                        recent_candles = candles.iloc[-50:]
                        if state["bias"] == "BUY":
                            recent_high = recent_candles["high"].max()
                            swing_dist = recent_high - entry
                            # Spec: use NEARER swing level if it's closer than the fixed target
                            # — avoids holding through a resistance level just to reach the fixed TP.
                            if 0 < swing_dist < target:
                                take_profit = recent_high
                                target_source = "dynamic_swing"
                        else:
                            recent_low = recent_candles["low"].min()
                            swing_dist = entry - recent_low
                            if 0 < swing_dist < target:
                                take_profit = recent_low
                                target_source = "dynamic_swing"

                    # [V1 section C.6] Chart markings. The confluence chain is a
                    # hard three-link sequence with no optional members (which is
                    # why confluence_score is a constant here) — so the chart's
                    # job is to show that each link resolved where it should.
                    mk = MarkingCollector(timeframe)
                    entry_t = ts(latest.get("time", candles.index[-1]))
                    range_t = ts(state.get("range_marked_time", entry_t))

                    mk.range_box(
                        f"NY open range ({self.params.range_window_start}-{self.params.range_window_end})",
                        state["range_high"], state["range_low"], range_t,
                        end_time=entry_t, role=ROLE_CONFLUENCE,
                        color="rgba(148,163,184,0.14)",
                        range_high=round(float(state["range_high"]), 6),
                        range_low=round(float(state["range_low"]), 6),
                        range_size=round(abs(state["range_high"] - state["range_low"]), 6),
                    )
                    mk.level(
                        "Range mid (retest / entry)", state["range_mid"], range_t,
                        role=ROLE_TRIGGER, color="rgba(59,130,246,0.9)",
                        rule="entry on retest of range_mid after a body close beyond the range",
                    )
                    if state.get("break_time") is not None:
                        mk.structure(
                            f"Body close beyond range ({state['bias']})",
                            ts(state["break_time"]), price=state.get("break_close"),
                            role=ROLE_TRIGGER,
                            broke=("range_high" if state["bias"] == "BUY" else "range_low"),
                            broke_level=round(float(
                                state["range_high"] if state["bias"] == "BUY" else state["range_low"]
                            ), 6),
                        )
                    mk.level(
                        "Breakout swing extreme (stop reference)", float(breakout_extreme), entry_t,
                        role=ROLE_CONTEXT, color="rgba(148,163,184,0.8)",
                    )
                    mk.level(
                        "Stop loss", stop_loss, entry_t, role=ROLE_INVALIDATION,
                        color="rgba(239,68,68,0.9)",
                        buffer_points=getattr(self.params, "stop_buffer_points", None),
                        atr_mult=getattr(self.params, "sl_buffer_atr_mult", 0.0),
                        sl_pips=round(abs(entry - stop_loss) / pip_size, 2) if pip_size else None,
                    )
                    mk.level(
                        "Take profit", take_profit, entry_t, role=ROLE_CONTEXT,
                        color="rgba(16,185,129,0.9)",
                        target_mode=target_mode, target_source=target_source,
                        rr=(round(abs(take_profit - entry) / abs(entry - stop_loss), 3)
                            if abs(entry - stop_loss) > 0 else None),
                    )

                    state["status"] = "DONE"
                    return TradeSignal(
                        strategy_id="NYOpenRetest_v1",
                        symbol=symbol,
                        direction=state["bias"],
                        timeframe=timeframe,
                        entry_price=entry,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        # confluence_score is DELIBERATELY constant here. Unlike the
                        # IFVG strategies, this setup has exactly one confluence chain
                        # (08:00-08:15 range marked -> body close beyond it after 09:30
                        # -> retest of range_mid) and every element is a hard gate: there
                        # is no optional confluence to count, so a computed score would
                        # be invented rather than measured. See audit §10.6.
                        confluence_score=92,
                        timestamp=float(latest["time"]) if "time" in latest else candles.index[-1].timestamp(),
                        metadata={
                            "setup": "NY_OPEN_RETEST",
                            "target_mode": target_mode,
                            "target_source": target_source,
                            "sl_pips": round(abs(entry - stop_loss) / pip_size, 2) if pip_size else None,
                            "realised_rr": (
                                round(abs(take_profit - entry) / abs(entry - stop_loss), 3)
                                if abs(entry - stop_loss) > 0 else None
                            ),
                            # [V1] Chart geometry — see strategies/core/markings.py.
                            **mk.as_metadata(),
                        }
                    )

        return None
