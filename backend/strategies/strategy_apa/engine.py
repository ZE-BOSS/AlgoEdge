"""
backend/strategies/strategy_apa/engine.py

Advanced Price Action (APA) Strategy Engine
============================================
Implements a fully rule-based Head & Shoulders / ABC structural reversal
with Invalidation Zone retest entry.

State Machine:
  AWAIT_PATTERN → AWAIT_BOS → AWAIT_RETEST → AWAIT_CONFIRMATION → ARMED (trade fired) → re-arms

Source: docs/apa_strategy_implementation_plan.md (Michael FX / A.P.A. framework)
"""

import pandas as pd
import pytz

from backend.core.config_schema import UserConfigV2
from backend.risk.position_sizer import get_pip_size
from backend.strategies.base_strategy import BaseStrategy, TradeSignal
from backend.strategies.core.swing_structure import (
    calculate_atr,
    detect_swings,
    detect_hs_pattern,
)
from backend.strategies.registry import register_strategy
from backend.utils.logger import get_logger

logger = get_logger(__name__)

# Deriv synthetic symbol prefixes — APA is valid on these (no VWAP restriction)
_SYNTHETIC_PREFIXES = (
    "Volatility", "Boom", "Crash", "Jump", "Step", "Range Break",
    "V10", "V25", "V50", "V75", "V100", "V150", "V250",
)


@register_strategy("APA_v1")
class APAEngine(BaseStrategy):
    """
    Advanced Price Action — Head & Shoulders Inversion Flip strategy.
    Replaces SMC_v1 as the primary price-structure strategy.
    """

    def __init__(self, config: UserConfigV2):
        super().__init__(config)
        self.params = config.apa
        self.state: dict = {}

    def _init_state(self, symbol: str):
        if symbol not in self.state:
            self.state[symbol] = {
                "status": "AWAIT_PATTERN",
                "pattern": None,          # The H&S dict from detect_hs_pattern
                "direction": None,        # "BUY" or "SELL"
                "invalidation_zone_top": None,
                "invalidation_zone_bottom": None,
                "sl_level": None,         # SL = wick extreme of right shoulder (+ buffer)
                "invalidation_head": None,# Price beyond which the thesis is void
                "bos_confirmed": False,
                "last_trade_date": None,
                # ATR on the STRUCTURE timeframe, captured at BOS-confirmation time.
                # The cost floor is applied at signal-emission time (on the entry
                # timeframe), but min_sl_atr_mult is documented against the structure
                # timeframe's ATR — see params.py. Stashing it here keeps the floor
                # measured against the ATR its default was calibrated for.
                "structure_atr": None,
            }

    def _sl_floor_distance(self, symbol: str, atr: float, pip_size: float) -> float:
        """
        Absolute minimum stop distance in PRICE units (audit §10.8).

            floor = max(min_sl_pips  * pip_size,          # absolute backstop
                        min_sl_atr_mult * atr,            # volatility-relative backstop
                        min_sl_spread_mult * spread * pip_size)   # spread-relative (opt-in)

        The spread term is only active if a `min_sl_spread_mult` field exists on
        APAParams (it currently does not — the term is inert by default and costs
        nothing). Every lookup uses getattr so a missing field can never crash the
        engine. Mirrors strategy_vwap/engine.py::_resolve_sl_distance.
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

    def _confluence_score(self, state: dict) -> int:
        """
        Genuine 0–100 confluence score (audit §10.6 — this was a hard-coded 90 on
        every signal, which made `confluence_stats` in every report meaningless).

        Components (sum to 100):

          55  MANDATORY CHAIN — awarded whenever a signal fires at all, because
              every one of these was verified by the state machine before we got
              here: H&S pattern with shoulders inside `shoulder_symmetry_tolerance_atr`
              → body-close BOS through the neckline → neckline confirmed to coincide
              with a MAJOR fractal swing (not a liquidity sweep) → candle body retest
              into the Invalidation Zone → Head level never violated → SL verified on
              the correct side of entry.

          15  SHOULDER SYMMETRY — how far inside the tolerance band the two shoulders
              actually sat. ≤1/3 of tolerance → 15, ≤2/3 → 8, else 0. A pattern that
              only just squeaked past the symmetry test is a weaker H&S than one whose
              shoulders are near-identical.

          15  NECKLINE PRECISION — distance from the neckline to the nearest major
              fractal swing, as a fraction of ATR. The BOS gate accepts anything within
              0.5×ATR; ≤0.15×ATR → 15, ≤0.30×ATR → 8, else 0.

          15  STOP QUALITY — 15 when the structural stop was already wider than the
              cost floor, 0 when the floor had to widen it. A floored stop means the
              shoulder wick sat inside the spread: the geometry is real but the
              economics were not, and the trade is now running on a synthetic stop.

        Range in practice: 55 (bare minimum setup, floored stop) → 100.
        """
        score = 55

        tol = getattr(self.params, "shoulder_symmetry_tolerance_atr", 0.0) or 0.0
        gap = state.get("shoulder_symmetry_gap_atr")
        if gap is not None and tol > 0:
            ratio = gap / tol
            if ratio <= 1.0 / 3.0:
                score += 15
            elif ratio <= 2.0 / 3.0:
                score += 8

        precision = state.get("neckline_precision_atr")
        if precision is not None:
            if precision <= 0.15:
                score += 15
            elif precision <= 0.30:
                score += 8

        if not state.get("sl_floored"):
            score += 15

        return max(0, min(100, int(score)))

    def _is_within_session(self, current_time: pd.Timestamp) -> bool:
        if not self.params.session_filter_enabled:
            return True
        if current_time.tzinfo is None:
            current_time = current_time.tz_localize("UTC")
        utc_time = current_time.astimezone(pytz.utc)
        time_str = utc_time.strftime("%H:%M")
        start, cutoff = self.params.session_start, self.params.session_cutoff
        if start <= cutoff:
            return start <= time_str <= cutoff
        return time_str >= start or time_str <= cutoff

    def get_required_timeframes(self) -> list[str]:
        return [self.params.structure_timeframe, self.params.entry_timeframe]

    async def on_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame) -> TradeSignal | None:
        self._init_state(symbol)
        state = self.state[symbol]

        # Reset daily state on calendar day change to clear stale setups.
        # Gated: don't wipe a pattern that has already reached AWAIT_RETEST/
        # AWAIT_CONFIRMATION with a confirmed BOS (a committed, in-flight setup),
        # and don't reset at all for continuously-traded synthetic symbols, where
        # a UTC-midnight boundary isn't a meaningful session break.
        current_time_dt = candles.index[-1]
        if isinstance(current_time_dt, (int, float)):
             current_time_dt = pd.to_datetime(current_time_dt, unit='s', utc=True)
        current_date = pd.Timestamp(current_time_dt, tz='UTC').date()

        if state.get("last_trade_date") != current_date:
            is_synthetic = any(prefix in symbol for prefix in _SYNTHETIC_PREFIXES)
            in_progress_committed = (
                state.get("bos_confirmed")
                and state.get("status") in ("AWAIT_RETEST", "AWAIT_CONFIRMATION")
            )
            if is_synthetic or in_progress_committed:
                # Track the date so we don't re-evaluate this branch every bar,
                # but leave the rest of the state untouched.
                state["last_trade_date"] = current_date
            else:
                state.update({
                    "status": "AWAIT_PATTERN",
                    "pattern": None,
                    "direction": None,
                    "invalidation_zone_top": None,
                    "invalidation_zone_bottom": None,
                    "sl_level": None,
                    "invalidation_head": None,
                    "bos_confirmed": False,
                    "structure_atr": None,
                    "last_trade_date": current_date
                })
                self.log_event(f"[{symbol}] State reset for new trading day.", category="APA")

        if len(candles) < (self.params.major_fractal_m * 2 + 5):
            return None

        current_time = candles.index[-1]
        latest = candles.iloc[-1]
        atr = calculate_atr(candles, self.params.atr_lookback)

        # ── STRUCTURE TIMEFRAME PROCESSING ──────────────────────────────
        if timeframe == self.params.structure_timeframe:
            # Detect minor swings (for H&S detection)
            minor_swings = detect_swings(candles, self.params.minor_fractal_m)
            # Detect major swings (for BOS filter)
            major_swings = detect_swings(candles, self.params.major_fractal_m)

            status = state["status"]

            # Step 1: scan for H&S pattern
            if status == "AWAIT_PATTERN":
                pattern = detect_hs_pattern(
                    minor_swings, atr, self.params.shoulder_symmetry_tolerance_atr
                )
                if pattern:
                    state["pattern"] = pattern
                    state["direction"] = "SELL" if pattern["type"] == "BEARISH" else "BUY"
                    state["status"] = "AWAIT_BOS"
                    self.log_event(
                        f"[{symbol}] {pattern['type']} H&S pattern detected. "
                        f"Neckline: {pattern['neckline_price']:.5f}. Awaiting BOS.",
                        category="APA",
                    )
                return None

            # Step 2: BOS — candle body close beyond neckline on a major swing level
            if status == "AWAIT_BOS":
                pattern = state["pattern"]
                neckline = pattern["neckline_price"]

                # Body close check (not wick — spec requirement)
                body_close_through_bearish = (
                    pattern["type"] == "BEARISH"
                    and min(latest["open"], latest["close"]) < neckline
                )
                body_close_through_bullish = (
                    pattern["type"] == "BULLISH"
                    and max(latest["open"], latest["close"]) > neckline
                )

                if body_close_through_bearish or body_close_through_bullish:
                    # Confirm neckline is a major swing level
                    major_prices = [s["price"] for s in major_swings]
                    is_major = any(
                        abs(mp - neckline) <= atr * 0.5 for mp in major_prices
                    )
                    if not is_major:
                        self.log_event(
                            f"[{symbol}] Body closed beyond neckline but NOT a major level — liquidity sweep, resetting.",
                            category="APA",
                        )
                        state["status"] = "AWAIT_PATTERN"
                        state["pattern"] = None
                        return None

                    # Draw Invalidation Zone from Right Shoulder candle body, or from
                    # both shoulders' bodies when invalidation_zone_source == "both".
                    rs = pattern["right_shoulder"]
                    iz_shoulders = [rs]
                    if self.params.invalidation_zone_source == "both":
                        iz_shoulders.append(pattern["left_shoulder"])

                    iz_tops, iz_bottoms = [], []
                    for shoulder in iz_shoulders:
                        s_bar = candles.loc[shoulder["index"]] if shoulder["index"] in candles.index else None
                        if s_bar is not None:
                            iz_tops.append(max(s_bar["open"], s_bar["close"]))
                            iz_bottoms.append(min(s_bar["open"], s_bar["close"]))
                        else:
                            iz_tops.append(shoulder["body_high"])
                            iz_bottoms.append(shoulder["body_low"])

                    iz_top = max(iz_tops)
                    iz_bottom = min(iz_bottoms)

                    state["invalidation_zone_top"] = iz_top
                    state["invalidation_zone_bottom"] = iz_bottom

                    # SL = wick extreme of right shoulder + buffer. sl_buffer_atr_mult
                    # widens this further, additive on top of sl_buffer_atr (both are
                    # ATR multiples applied to the same wick reference — see params.py
                    # docstrings, sl_buffer_atr_mult is explicitly "added ON TOP of the
                    # structural SL distance").
                    buffer = (self.params.sl_buffer_atr + self.params.sl_buffer_atr_mult) * atr
                    head = pattern["head"]
                    tight = abs(head["price"] - rs["price"]) < self.params.tight_level_threshold_atr * atr

                    if pattern["type"] == "BEARISH":
                        # SL above the shoulder wick (or head wick if tight levels)
                        sl_ref_wick = head["price"] if tight else rs["price"]
                        state["sl_level"] = sl_ref_wick + buffer
                        state["invalidation_head"] = head["price"]
                    else:
                        sl_ref_wick = head["price"] if tight else rs["price"]
                        state["sl_level"] = sl_ref_wick - buffer
                        state["invalidation_head"] = head["price"]

                    state["bos_confirmed"] = True
                    # Capture the structure-timeframe ATR for the cost floor applied
                    # at signal-emission time (see _sl_floor_distance / audit §10.8).
                    state["structure_atr"] = atr

                    # ── Confluence measurements (audit §10.6) ────────────────
                    # Recorded here because both quantities are only knowable on the
                    # structure timeframe at BOS time; they are scored at emission.
                    ls = pattern["left_shoulder"]
                    if atr > 0:
                        state["shoulder_symmetry_gap_atr"] = abs(ls["price"] - rs["price"]) / atr
                        state["neckline_precision_atr"] = min(
                            abs(mp - neckline) for mp in major_prices
                        ) / atr
                    else:
                        state["shoulder_symmetry_gap_atr"] = None
                        state["neckline_precision_atr"] = None
                    state["tight_levels"] = tight
                    state["status"] = "AWAIT_RETEST"
                    self.log_event(
                        f"[{symbol}] BOS confirmed on major level. "
                        f"IZ: [{iz_bottom:.5f} – {iz_top:.5f}]. Awaiting retest.",
                        category="APA",
                    )
                return None

        # ── ENTRY TIMEFRAME PROCESSING ───────────────────────────────────
        elif timeframe == self.params.entry_timeframe:
            status = state["status"]

            if status not in ("AWAIT_RETEST", "AWAIT_CONFIRMATION"):
                return None

            if not self._is_within_session(current_time):
                return None

            iz_top = state["invalidation_zone_top"]
            iz_bottom = state["invalidation_zone_bottom"]
            direction = state["direction"]

            # Step 3: Wait for candle BODY to enter the Invalidation Zone
            if status == "AWAIT_RETEST":
                body_top = max(latest["open"], latest["close"])
                body_bottom = min(latest["open"], latest["close"])
                # Body enters IZ if any overlap
                if body_bottom <= iz_top and body_top >= iz_bottom:
                    state["status"] = "AWAIT_CONFIRMATION"
                    # Re-read status so the AWAIT_CONFIRMATION block below
                    # can run on the same bar as the retest touch (no 1-bar delay).
                    status = state["status"]
                    self.log_event(
                        f"[{symbol}] Retest into Invalidation Zone confirmed. Checking confirmation rules.",
                        category="APA",
                    )

            # Step 4: Confirmation — all 5 rules from §4 (run on same bar)
            if status == "AWAIT_CONFIRMATION":
                pattern = state["pattern"]
                sl = state["sl_level"]
                head_price = state["invalidation_head"]

                # Hard invalidation: body closes back beyond the Head level
                if direction == "SELL" and min(latest["open"], latest["close"]) > head_price:
                    state["status"] = "AWAIT_PATTERN"
                    state["pattern"] = None
                    self.log_event(f"[{symbol}] Pattern invalidated — body closed beyond Head.", category="APA")
                    return None
                if direction == "BUY" and max(latest["open"], latest["close"]) < head_price:
                    state["status"] = "AWAIT_PATTERN"
                    state["pattern"] = None
                    self.log_event(f"[{symbol}] Pattern invalidated — body closed beyond Head.", category="APA")
                    return None

                # Rule 5: No conflicting open position (handled upstream by risk engine)
                # Rules 1–4 have been validated through the state machine

                entry = latest["close"]
                if sl is None:
                    return None

                # ── Validate SL is on the correct side of entry ──
                # SELL: SL must be above entry (price rises = loss)
                # BUY:  SL must be below entry (price falls = loss)
                if direction == "SELL" and sl <= entry:
                    state["status"] = "AWAIT_PATTERN"
                    state["pattern"] = None
                    self.log_event(
                        f"[{symbol}] SELL SL ({sl:.5f}) <= entry ({entry:.5f}) — "
                        f"SL on wrong side, pattern invalidated.",
                        category="APA",
                    )
                    return None
                if direction == "BUY" and sl >= entry:
                    state["status"] = "AWAIT_PATTERN"
                    state["pattern"] = None
                    self.log_event(
                        f"[{symbol}] BUY SL ({sl:.5f}) >= entry ({entry:.5f}) — "
                        f"SL on wrong side, pattern invalidated.",
                        category="APA",
                    )
                    return None

                # ── Cost floors (audit §10.8) ────────────────────────────────
                # The structural SL was computed at BOS-confirmation time, but
                # `entry` is only known now, at the retest. The floor must therefore
                # be applied HERE — measuring it against the shoulder wick instead of
                # against the actual entry systematically under-widens, because the
                # retest entry sits inside the Invalidation Zone (shoulder BODIES)
                # while the stop sits at the shoulder WICK: the two can be a fraction
                # of a pip apart. The forensic runs produced a 3.47-pip median stop
                # and a 0.45-pip minimum on a ~2-pip spread from exactly this gap.
                pip_size = get_pip_size(symbol) or 0.0001
                floor_atr = state.get("structure_atr") or atr
                sl_floor = self._sl_floor_distance(symbol, floor_atr, pip_size)
                sl_distance = abs(entry - sl)
                if sl_floor > 0 and sl_distance < sl_floor:
                    self.log_event(
                        f"[{symbol}] SL floored: {sl_distance / pip_size:.1f} → "
                        f"{sl_floor / pip_size:.1f} pips (structural SL {sl:.5f} sat inside "
                        f"the cost floor).",
                        category="APA",
                    )
                    sl = entry + sl_floor if direction == "SELL" else entry - sl_floor
                    sl_distance = sl_floor
                    state["sl_floored"] = True
                else:
                    state["sl_floored"] = False

                # TP: use risk engine's RR grid (SL distance × RR)
                if direction == "SELL":
                    tp = entry - sl_distance  # TP1 = 1R
                else:
                    tp = entry + sl_distance  # TP1 = 1R

                # Final sanity: SL and TP must not be equal
                if abs(tp - sl) < 1e-10:
                    state["status"] = "AWAIT_PATTERN"
                    state["pattern"] = None
                    self.log_event(
                        f"[{symbol}] SL ({sl:.5f}) == TP ({tp:.5f}) — degenerate, resetting.",
                        category="APA",
                    )
                    return None

                confluence_score = self._confluence_score(state)

                # Reset state machine for next setup
                state["status"] = "AWAIT_PATTERN"
                state["pattern"] = None
                state["bos_confirmed"] = False

                self.log_event(
                    f"[{symbol}] APA SIGNAL FIRED: {direction} @ {entry:.5f} | SL: {sl:.5f} | "
                    f"TP1: {tp:.5f} | confluence: {confluence_score}",
                    category="APA",
                )

                return TradeSignal(
                    strategy_id="APA_v1",
                    symbol=symbol,
                    direction=direction,
                    signal_type="HS_INVERSION",
                    timeframe=timeframe,
                    entry_price=entry,
                    stop_loss=sl,
                    take_profit=tp,
                    confluence_score=confluence_score,
                    timestamp=float(latest.get("time", current_time.timestamp())),
                    metadata={
                        "setup": "APA_HS",
                        "pattern_type": pattern["type"],
                        "neckline": pattern["neckline_price"],
                        "invalidation_zone": [iz_bottom, iz_top],
                        "sl_floored": bool(state.get("sl_floored")),
                        "sl_pips": round(sl_distance / pip_size, 2),
                    },
                )

        return None
