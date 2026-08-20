import pandas as pd
import pytz

from backend.core.config_schema import UserConfigV2
from backend.risk.position_sizer import get_pip_size
from backend.strategies.base_strategy import BaseStrategy, TradeSignal
from backend.strategies.core.fvg import FVGDetector
from backend.strategies.core.market_structure import MarketStructureDetector
from backend.strategies.core.swing_structure import calculate_atr
from backend.strategies.registry import register_strategy
from backend.utils.logger import get_logger

logger = get_logger(__name__)

@register_strategy("HTFFVGFlip_v1")
class HTFFVGFlipEngine(BaseStrategy):
    """
    Strategy 1: HTF Key Level -> 5M FVG -> Inversion Flip
    """
    def __init__(self, config: UserConfigV2):
        super().__init__(config)
        self.params = config.htf_fvg_flip
        
        # State tracking per symbol
        self.state = {}
        self.htf_detectors = {}
        self.m5_detectors = {}
        self.ms_detectors = {}
        self.last_trade_date = {}

    def _init_state(self, symbol: str):
        if symbol not in self.state:
            self.state[symbol] = {
                "status": "AWAIT_HTF_TAP",
                "bias": None,
                "tap_time": None,
                "m5_fvg": None,
                "m5_swing_point": None,
                # Confluence inputs (audit §10.6) — recorded as the state machine
                # advances, scored at signal-emission time by _confluence_score().
                "htf_fvg_first_tap": False,
                "sl_floored": False,
            }
            self.htf_detectors[symbol] = FVGDetector(fvg_min_gap_atr_mult=0.2)
            self.m5_detectors[symbol] = FVGDetector(fvg_min_gap_atr_mult=0.1)
            # Used to derive HTF trend context so we only act on FVG taps that are
            # counter to the recent trend (spec requirement — see on_bar step 2).
            self.ms_detectors[symbol] = MarketStructureDetector(swing_length=5, min_bos_count=1)

    def _sl_floor_distance(self, symbol: str, atr: float, pip_size: float) -> float:
        """
        Absolute minimum stop distance in PRICE units (audit §10.4 / §10.8).

            floor = max(min_sl_pips  * pip_size,          # absolute backstop
                        min_sl_atr_mult * atr,            # volatility-relative backstop
                        min_sl_spread_mult * spread * pip_size)   # spread-relative (opt-in)

        The spread term is only active if a `min_sl_spread_mult` field exists on
        HTFFVGFlipParams (it currently does not — the term is inert by default).
        Every lookup uses getattr so a missing field can never crash the engine.
        Mirrors strategy_vwap/engine.py::_resolve_sl_distance.
        """
        floor = 0.0

        min_pips = getattr(self.params, "min_sl_pips", 0.0) or 0.0
        if min_pips > 0 and pip_size > 0:
            floor = max(floor, min_pips * pip_size)

        atr_mult = getattr(self.params, "min_sl_atr_mult", 0.0) or 0.0
        if atr_mult > 0 and atr > 0:
            floor = max(floor, atr_mult * atr)

        spread_mult = getattr(self.params, "min_sl_spread_mult", 0.0) or 0.0
        if spread_mult > 0 and pip_size > 0:
            try:
                from backend.risk.broker_costs import get_broker_costs
                spread_pips = get_broker_costs(symbol).get("spread_pips", 0.0) or 0.0
                if spread_pips > 0:
                    floor = max(floor, spread_mult * spread_pips * pip_size)
            except Exception:
                # Broker costs unavailable (no MT5, module missing) — the absolute
                # and ATR floors above still apply.
                pass

        return floor

    def _resolve_sl_distance(
        self, symbol: str, entry: float, swing_point: float, atr: float, pip_size: float
    ) -> tuple[float, bool]:
        """
        Stop distance in PRICE units, per the audit's §10.4 formula:

            sl_dist = max(|entry - m5_swing_point| + sl_buffer_atr_mult * atr, floor)
            floor   = max(min_sl_pips * pip_size, min_sl_atr_mult * atr)

        Previously the SL was set FLUSH at `m5_swing_point` (engine.py:199) with no
        cushion at all — sitting exactly on the swing wick every other participant
        can see, before spread, slippage, or the ordinary overshoot that follows a
        swing test is even accounted for.

        Returns (sl_dist, floored) — `floored` feeds the confluence score.
        """
        structural = abs(entry - swing_point)

        buffer_mult = getattr(self.params, "sl_buffer_atr_mult", 0.0) or 0.0
        sl_dist = structural + (buffer_mult * atr if atr > 0 else 0.0)

        floor = self._sl_floor_distance(symbol, atr, pip_size)
        floored = floor > 0 and sl_dist < floor
        if floored:
            self.log_event(
                f"[{symbol}] SL floored: {sl_dist / pip_size:.1f} → {floor / pip_size:.1f} pips "
                f"(structural swing distance {structural / pip_size:.1f} pips + "
                f"{buffer_mult:.2f}xATR sat inside the cost floor).",
                category="FVG_FLIP",
            )
            sl_dist = floor

        return sl_dist, floored

    def _confluence_score(self, state: dict, displacement_atr: float | None) -> int:
        """
        Genuine 0–100 confluence score (audit §10.6 — this was a hard-coded 88 on
        every signal).

        Components (sum to 100):

          55  MANDATORY CHAIN — awarded whenever a signal fires, because the state
              machine verified all of it to get here: an HTF FVG tapped by an M5 bar
              → the tap was COUNTER to the confirmed HTF market-structure trend
              (BULLISH FVG only counts against a BEARISH trend, and vice versa)
              → an opposing M5 FVG formed after the tap → price retested that FVG
              → an M5 body closed THROUGH it (the inversion) → the swing extreme was
              never breached in the meantime → the bar is inside the session window.

          15  FIRST TAP — the HTF FVG had not been tapped before. This is the
              `require_unfilled_htf_fvg` quality rule; when that flag is left ON the
              component is always awarded (it is a hard gate), but when a user turns
              it off, re-taps of an already-consumed gap now score lower instead of
              being indistinguishable from a virgin gap.

          15  DISPLACEMENT — how decisively the inversion candle closed beyond the
              FVG boundary, in ATR terms. ≥0.50×ATR → 15, ≥0.25×ATR → 8, else 0.
              A body that closes one tick past the gap is a far weaker inversion than
              one that closes half an ATR past it.

          15  STOP QUALITY — 15 when the structural stop (swing + ATR buffer) was
              already wider than the cost floor, 0 when the floor had to widen it.
              A floored stop means the M5 swing sat inside the spread.

        Range in practice: 55 → 100.
        """
        score = 55

        if state.get("htf_fvg_first_tap"):
            score += 15

        if displacement_atr is not None:
            if displacement_atr >= 0.50:
                score += 15
            elif displacement_atr >= 0.25:
                score += 8

        if not state.get("sl_floored"):
            score += 15

        return max(0, min(100, int(score)))

    def _is_within_session(self, current_time: pd.Timestamp) -> bool:
        if not self.params.session_filter_enabled:
            return True
        
        ny_tz = pytz.timezone('America/New_York')
        if current_time.tzinfo is None:
            current_time = current_time.tz_localize('UTC')
        ny_time = current_time.astimezone(ny_tz)
        time_str = ny_time.strftime("%H:%M")
        start = self.params.session_start
        cutoff = self.params.session_cutoff
        
        if start <= cutoff:
            return start <= time_str <= cutoff
        else:
            return time_str >= start or time_str <= cutoff

    def get_required_timeframes(self) -> list[str]:
        # Dynamically request timeframes configured by the user
        return [self.params.htf_timeframe, self.params.entry_confirmation_tf]

    async def on_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame) -> TradeSignal | None:
        self._init_state(symbol)
        state = self.state[symbol]
        
        current_time = candles.index[-1]
        latest = candles.iloc[-1]
        
        current_date = current_time.date()
        if symbol not in self.last_trade_date or self.last_trade_date[symbol] != current_date:
            # Reset state for the new trading day
            self.state[symbol] = {
                "status": "AWAIT_HTF_TAP",
                "bias": None,
                "tap_time": None,
                "m5_fvg": None,
                "m5_swing_point": None,
                # Confluence inputs (audit §10.6) — recorded as the state machine
                # advances, scored at signal-emission time by _confluence_score().
                "htf_fvg_first_tap": False,
                "sl_floored": False,
            }
            state = self.state[symbol]
            self.last_trade_date[symbol] = current_date
            self.log_event(f"[{symbol}] State reset for new trading day.", category="FVG_FLIP")
        
        # Process HTF (Keep trackers updated)
        if timeframe == self.params.htf_timeframe:
            self.htf_detectors[symbol].update(candles)
            self.ms_detectors[symbol].update(candles)

        # Process LTF Confirmation
        elif timeframe == self.params.entry_confirmation_tf:
            ltf_fvgs = []
            
            # 1. Update LTF FVGs
            if state["status"] in ["AWAIT_INVERSION_FVG", "AWAIT_INVERSION_CLOSE"]:
                ltf_fvgs = self.m5_detectors[symbol].update(candles)
            
            # 2. Check for HTF Tap (Real-time detection using M5 candle)
            if state["status"] == "AWAIT_HTF_TAP":
                htf_fvgs = self.htf_detectors[symbol].active_fvgs
                # Spec requires the tapped FVG be counter to the recent HTF trend:
                # a BULLISH FVG (expected to bounce price up) only qualifies as
                # counter-trend support when the HTF trend is BEARISH, and a BEARISH
                # FVG only qualifies as counter-trend resistance when the HTF trend is
                # BULLISH. With no confirmed trend (NEUTRAL) there's nothing to be
                # "counter" to, so taps are held back until a trend resolves.
                htf_trend = self.ms_detectors[symbol].get_bias()
                require_unfilled = getattr(self.params, "require_unfilled_htf_fvg", True)
                for fvg in htf_fvgs:
                    if require_unfilled and fvg.get("tapped"):
                        continue
                    # Bullish FVG tap -> expect bounce up (BUY bias); counter-trend only if HTF trend is BEARISH
                    if fvg["type"] == "BULLISH" and fvg["bottom"] <= latest["low"] <= fvg["top"]:
                        if htf_trend != "BEARISH":
                            continue
                        # Record BEFORE marking: with require_unfilled_htf_fvg=False a
                        # previously-consumed gap can reach here, and a re-tap is a
                        # genuinely weaker setup than a virgin one (see _confluence_score).
                        state["htf_fvg_first_tap"] = not bool(fvg.get("tapped"))
                        fvg["tapped"] = True
                        state["status"] = "AWAIT_INVERSION_FVG"
                        state["bias"] = "BUY"
                        state["tap_time"] = current_time
                        self.log_event(f"[{symbol}] HTF Bullish FVG tapped by M5 (counter-trend vs BEARISH). Bias: BUY", category="FVG_FLIP")
                        break
                    # Bearish FVG tap -> expect bounce down (SELL bias); counter-trend only if HTF trend is BULLISH
                    elif fvg["type"] == "BEARISH" and fvg["bottom"] <= latest["high"] <= fvg["top"]:
                        if htf_trend != "BULLISH":
                            continue
                        state["htf_fvg_first_tap"] = not bool(fvg.get("tapped"))
                        fvg["tapped"] = True
                        state["status"] = "AWAIT_INVERSION_FVG"
                        state["bias"] = "SELL"
                        state["tap_time"] = current_time
                        self.log_event(f"[{symbol}] HTF Bearish FVG tapped by M5 (counter-trend vs BULLISH). Bias: SELL", category="FVG_FLIP")
                        break

            # 3. Look for a new LTF FVG in the OPPOSING direction (to be inverted)
            if state["status"] == "AWAIT_INVERSION_FVG":
                for fvg in reversed(ltf_fvgs):
                    fvg_time = fvg.get("index", pd.Timestamp.min)
                    if fvg_time >= state.get("tap_time", pd.Timestamp.min):
                        if state["bias"] == "BUY" and fvg["type"] == "BEARISH":
                            state["m5_fvg"] = fvg
                            state["status"] = "AWAIT_RETEST"
                            tap_time = state.get("tap_time")
                            lookback = candles.loc[tap_time:] if tap_time is not None and tap_time in candles.index else candles.iloc[-20:]
                            state["m5_swing_point"] = lookback["low"].min()
                            self.log_event(f"[{symbol}] {timeframe} Bearish FVG formed. Awaiting retest.", category="FVG_FLIP")
                            break
                        elif state["bias"] == "SELL" and fvg["type"] == "BULLISH":
                            state["m5_fvg"] = fvg
                            state["status"] = "AWAIT_RETEST"
                            tap_time = state.get("tap_time")
                            lookback = candles.loc[tap_time:] if tap_time is not None and tap_time in candles.index else candles.iloc[-20:]
                            state["m5_swing_point"] = lookback["high"].max()
                            self.log_event(f"[{symbol}] {timeframe} Bullish FVG formed. Awaiting retest.", category="FVG_FLIP")
                            break

            # 4. Wait for price to retest the FVG
            if state["status"] == "AWAIT_RETEST":
                fvg = state["m5_fvg"]
                if state["bias"] == "BUY" and latest["high"] >= fvg["bottom"]:
                    state["status"] = "AWAIT_INVERSION_CLOSE"
                    self.log_event(f"[{symbol}] Bearish FVG retested. Awaiting inversion close.", category="FVG_FLIP")
                elif state["bias"] == "SELL" and latest["low"] <= fvg["top"]:
                    state["status"] = "AWAIT_INVERSION_CLOSE"
                    self.log_event(f"[{symbol}] Bullish FVG retested. Awaiting inversion close.", category="FVG_FLIP")

            # 5. Wait for inversion close
            if state["status"] == "AWAIT_INVERSION_CLOSE":
                # Only block new trades outside session; do not reset state mid-setup
                if not self._is_within_session(current_time):
                    return None
                    
                fvg = state["m5_fvg"]
                triggered = False
                
                # Check for body close THROUGH the opposing FVG
                if state["bias"] == "BUY" and latest["close"] > fvg["top"]:
                    triggered = True
                elif state["bias"] == "SELL" and latest["close"] < fvg["bottom"]:
                    triggered = True

                # Invalidate if price breaks the swing extreme before inversion.
                # Uses the candle WICK (low/high), not the close — a wick-only pierce
                # through the recorded swing point already invalidates the level even
                # without a full-body close beyond it, since the eventual SL sits on
                # that level and a wick breach means it's already been run through.
                # `m5_swing_point` is initialised to None, so `.get(key, default)` does
                # NOT protect this comparison — the key exists. Resolve it explicitly
                # so a None/NaN reference can never raise here (audit §10.5).
                swing_ref = state.get("m5_swing_point")
                swing_valid = swing_ref is not None and not pd.isna(swing_ref)
                if swing_valid and state["bias"] == "BUY" and latest["low"] < swing_ref:
                    state["status"] = "AWAIT_HTF_TAP"
                    self.log_event(f"[{symbol}] Inversion setup failed (Swing low broken).", category="FVG_FLIP")
                    return None
                elif swing_valid and state["bias"] == "SELL" and latest["high"] > swing_ref:
                    state["status"] = "AWAIT_HTF_TAP"
                    self.log_event(f"[{symbol}] Inversion setup failed (Swing high broken).", category="FVG_FLIP")
                    return None

                if triggered:
                    entry = latest["close"]
                    swing_point = state.get("m5_swing_point")

                    # ── Missing structural reference = no setup (audit §10.5) ──
                    # This used to fall back to `entry * 0.99` — a 1%-of-price stop,
                    # i.e. ~80 pips on USDCHF or ~200 points on NAS100. A missing dict
                    # key silently swung the stop distance (and therefore the position
                    # size) by ~80x with no log line. A missing swing reference means
                    # there is no setup, not a setup with an arbitrary stop.
                    if swing_point is None or pd.isna(swing_point):
                        self.log_event(
                            f"[{symbol}] Inversion confirmed but m5_swing_point is missing — "
                            f"no structural stop reference, discarding setup (no signal).",
                            level="WARN",
                            category="FVG_FLIP",
                        )
                        state["status"] = "AWAIT_HTF_TAP"
                        return None

                    self.log_event(f"[{symbol}] Inversion confirmed. Entering trade.", category="FVG_FLIP")

                    atr = calculate_atr(candles, 14)
                    pip_size = get_pip_size(symbol) or 0.0001

                    # Structural stop + sl_buffer_atr_mult cushion + cost floors
                    # (audit §10.3 / §10.4 — sl_buffer_atr_mult was previously dead
                    # in this engine: the identifier appeared nowhere in the file).
                    sl_dist, floored = self._resolve_sl_distance(
                        symbol, entry, float(swing_point), atr, pip_size
                    )
                    state["sl_floored"] = floored

                    rr = self.params.target_rr
                    if state["bias"] == "BUY":
                        sl = entry - sl_dist
                        tp = entry + sl_dist * rr
                    else:
                        sl = entry + sl_dist
                        tp = entry - sl_dist * rr

                    # Displacement of the inversion close beyond the FVG boundary,
                    # in ATR terms — a confluence input, see _confluence_score().
                    boundary = fvg["top"] if state["bias"] == "BUY" else fvg["bottom"]
                    displacement_atr = (abs(entry - boundary) / atr) if atr > 0 else None
                    confluence_score = self._confluence_score(state, displacement_atr)

                    # Reset state for next setup
                    state["status"] = "AWAIT_HTF_TAP"

                    return TradeSignal(
                        strategy_id="HTFFVGFlip_v1",
                        symbol=symbol,
                        direction=state["bias"],
                        timeframe=timeframe,
                        entry_price=entry,
                        stop_loss=sl,
                        take_profit=tp,
                        confluence_score=confluence_score,
                        timestamp=float(latest.get("time", current_time.timestamp())),
                        metadata={
                            "setup": "HTF_FVG_FLIP",
                            "sl_pips": round(sl_dist / pip_size, 2),
                            "sl_floored": floored,
                            "swing_point": float(swing_point),
                            "displacement_atr": round(displacement_atr, 3) if displacement_atr is not None else None,
                        }
                    )

        return None
