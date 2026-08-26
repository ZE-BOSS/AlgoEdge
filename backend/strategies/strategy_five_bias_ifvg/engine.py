import pandas as pd
import pytz

from backend.core.config_schema import UserConfigV2
from backend.risk.position_sizer import get_pip_size
from backend.strategies.base_strategy import BaseStrategy, TradeSignal
from backend.strategies.core.fvg import FVGDetector
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
from backend.utils.logger import get_logger

logger = get_logger(__name__)

@register_strategy("BiasIFVG_v1")
class BiasIFVGEngine(BaseStrategy):
    """
    Strategy 2: 4-Step Bias -> Key Level -> IFVG
    """
    def __init__(self, config: UserConfigV2):
        super().__init__(config)
        self.params = config.bias_ifvg
        
        self.state = {}
        self.htf_detectors = {}
        self.m15_detectors = {}
        self.m5_detectors = {}
        self.last_trade_date = {}
        # [6.1b/S2] M5 bar counter per symbol, driving setup_max_age_bars —
        # independent of the calendar-day reset (see on_bar).
        self.m5_bar_index: dict = {}

    def _init_state(self, symbol: str):
        if symbol not in self.state:
            self.state[symbol] = {
                "bias": None,
                "key_level": None,
                "status": "AWAIT_BIAS",
                "m5_fvg_to_invert": None,
                "m5_swing_point": None,
                "manipulation_leg_start": None,
                "approach_leg_start": None,
                "setup_started_bar": None,
                "trades_today": 0,
                "wins_today": 0,
                "losses_today": 0,
                "extra_levels": [],
                # Confluence inputs (audit §10.6) — recorded as the state machine
                # advances, scored at signal-emission time by _confluence_score().
                "confluent_level_count": 0,
                "sl_floored": False,
            }
            self.m5_bar_index[symbol] = 0
            self.htf_detectors[symbol] = FVGDetector(fvg_min_gap_atr_mult=0.1)
            self.m15_detectors[symbol] = FVGDetector(fvg_min_gap_atr_mult=0.1)
            self.m5_detectors[symbol] = FVGDetector(fvg_min_gap_atr_mult=0.05)

    def notify_outcome(self, symbol: str, group_id: str, is_win: bool, pnl: float) -> None:
        """
        Called by the backtester/live engine after a full trade group closes.
        Tracks wins_today/losses_today so the spec's mandatory day-stop rule
        (stop after 1 win; after 1 loss only take a 2nd trade if it's A+; stop
        after 2 losses regardless) can be enforced by _can_trade_today().
        """
        self._init_state(symbol)
        state = self.state[symbol]
        if is_win:
            state["wins_today"] = state.get("wins_today", 0) + 1
            self.log_event(
                f"[{symbol}] BiasIFVG win recorded. Day-stop rule: no more trades today.",
                category="BIAS_IFVG",
            )
        else:
            state["losses_today"] = state.get("losses_today", 0) + 1
            self.log_event(
                f"[{symbol}] BiasIFVG loss recorded. losses_today={state['losses_today']}",
                category="BIAS_IFVG",
            )

    def _can_trade_today(self, state: dict) -> bool:
        """
        Spec HARD day-stop conditions only: stop for the day after 1 win; stop
        entirely after 2 losses. Both are pure trade-count rules, unrelated to
        confluence — see `_second_trade_size_modifier` for the confluence-gated
        "2nd trade after 1 loss" rule, which is a CLAMP, not a BLOCK [6.12/S15].
        """
        wins = state.get("wins_today", 0)
        losses = state.get("losses_today", 0)
        if wins >= 1:
            return False
        if losses >= 2:
            return False
        return True

    def _second_trade_size_modifier(self, state: dict, candidate_confluence: int | None) -> float:
        """
        [6.12/S15] After exactly 1 loss, the spec allows a 2nd trade only if
        it's "a clearly A+ setup" — previously enforced as an outright BLOCK
        below `a_plus_confluence_threshold`, discarding a real setup entirely
        for falling short of a bar that's inherently somewhat arbitrary.
        CLAMPED instead: a sub-threshold 2nd-after-a-loss setup still fires,
        at reduced size, rather than being skipped for the rest of the day.
        Returns 1.0 (no reduction) for every other trade.
        """
        if state.get("losses_today", 0) != 1:
            return 1.0
        threshold = getattr(self.params, "a_plus_confluence_threshold", 85)
        reduced = getattr(self.params, "second_trade_size_modifier", 0.5)
        if candidate_confluence is None or candidate_confluence < threshold:
            return reduced
        return 1.0

    def _detect_cisd_and_rejections(self, candles: pd.DataFrame, bias: str) -> list:
        if len(candles) < 5 or bias is None:
            return []

        levels = []
        # `candles` passed into on_bar already has the unclosed bar stripped
        # upstream, so iloc[-1] IS the most recently completed candle — using
        # iloc[-2] here evaluated the pattern one bar late.
        latest = candles.iloc[-1]
        body_size = abs(latest["close"] - latest["open"])
        total_size = latest["high"] - latest["low"]

        min_body = 0.0
        min_body_atr_mult = getattr(self.params, "rejection_min_body_atr_mult", 0.0)
        if min_body_atr_mult > 0:
            atr = calculate_atr(candles)
            min_body = min_body_atr_mult * atr

        if total_size > 0 and body_size >= min_body:
            if bias == "BUY":
                lower_wick = min(latest["close"], latest["open"]) - latest["low"]
                if lower_wick > body_size * 2:
                    levels.append({
                        "type": "BULLISH_REJECTION",
                        "top": min(latest["close"], latest["open"]),
                        "bottom": latest["low"],
                        "time": candles.index[-1]
                    })
            elif bias == "SELL":
                upper_wick = latest["high"] - max(latest["close"], latest["open"])
                if upper_wick > body_size * 2:
                    levels.append({
                        "type": "BEARISH_REJECTION",
                        "top": latest["high"],
                        "bottom": max(latest["close"], latest["open"]),
                        "time": candles.index[-1]
                    })

        # CISD: a variable-length run (>= 2, uncapped) of same-direction-close
        # candles immediately preceding the reversal candle, per spec's "run of
        # down/up-close candles" language — not hardcoded to exactly 2 candles.
        # Scan backward from the candle before the reversal candle (`latest`)
        # while candles keep closing in the run direction.
        if len(candles) >= 4:
            reversal = latest
            run_is_down_close = bias == "BUY"
            run = []
            i = len(candles) - 2  # candle immediately before the reversal candle
            while i >= 0:
                c = candles.iloc[i]
                is_run_candle = (c["close"] < c["open"]) if run_is_down_close else (c["close"] > c["open"])
                if not is_run_candle:
                    break
                run.append(c)
                i -= 1

            if len(run) >= 2:
                # run[-1] is the oldest candle in the run — its open is the run's
                # "opening price" per spec, i.e. the CISD line.
                cisd_line = run[-1]["open"]
                if bias == "BUY" and reversal["close"] > cisd_line:
                    buffer = (reversal["close"] - cisd_line) * 0.1
                    levels.append({
                        "type": "BULLISH_CISD",
                        "top": cisd_line + buffer,
                        "bottom": cisd_line - buffer,
                        "time": candles.index[-1]
                    })
                elif bias == "SELL" and reversal["close"] < cisd_line:
                    buffer = (cisd_line - reversal["close"]) * 0.1
                    levels.append({
                        "type": "BEARISH_CISD",
                        "top": cisd_line + buffer,
                        "bottom": cisd_line - buffer,
                        "time": candles.index[-1]
                    })
        return levels

    def _sl_floor_distance(self, symbol: str, atr: float, pip_size: float) -> float:
        """
        Absolute minimum stop distance in PRICE units (audit §10.4 / §10.8).

            floor = max(min_sl_pips  * pip_size,          # absolute backstop
                        min_sl_atr_mult * atr,            # volatility-relative backstop
                        min_sl_spread_mult * spread * pip_size)   # spread-relative (opt-in)

        The spread term is only active if a `min_sl_spread_mult` field exists on
        BiasIFVGParams (it currently does not — the term is inert by default).
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

        Previously the SL was set FLUSH at `m5_swing_point` (engine.py:345). The spec
        nominates the swing high/low as the stop reference, but a stop placed exactly
        ON a visible swing is the single most-hunted price on the chart. The floor is
        especially load-bearing for this strategy: entry is an M5 body-close THROUGH
        an IFVG, so entry and the swing that formed that IFVG are only a few pips
        apart by construction, not by accident.

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
                category="BIAS_IFVG",
            )
            sl_dist = floor

        return sl_dist, floored

    def _approach_leg_start(self, candles: "pd.DataFrame", bias: str, lookback: int = 40):
        """
        [D-5] When the swing that reached the key level began.

        The spec's "manipulation leg" is the swing that ACTUALLY TOUCHED the
        key level, so its start is the last extreme before the tap on the
        opposite side of the move:

          BUY  — price fell into a bullish level, so the leg started at the
                 highest high in the recent window.
          SELL — mirror: the lowest low.

        A window rather than a full swing-structure walk on purpose: the tap has
        just happened, the leg is by definition recent, and `lookback` M5 bars
        (~3h) comfortably covers it. Returning the window's own start when the
        extreme sits at its edge is the safe failure — it widens the search
        rather than silently excluding the gap the setup depends on.
        """
        try:
            window = candles.iloc[-lookback:]
            if window.empty:
                return candles.index[-1]
            idx = window["high"].idxmax() if bias == "BUY" else window["low"].idxmin()
            return idx
        except Exception:
            # Never let leg-start resolution break a signal; the caller falls
            # back to the tap time, which reproduces REACTION behaviour.
            return None

    def _count_confluent_levels(self, symbol: str, state: dict, chosen: dict) -> int:
        """
        How many OTHER recorded key levels overlap the tapped level's price band.

        This is the literal meaning of "confluence": two or more independently-derived
        methods (an M15 FVG, a CISD line, a rejection block) marking the same price.
        A key level that three methods agree on is a materially better level than one
        found by a single method, and it is the only quality dimension this strategy
        genuinely measures more than once.
        """
        try:
            top = float(chosen["top"])
            bottom = float(chosen["bottom"])
        except Exception:
            return 0

        candidates: list = []
        try:
            candidates.extend(self.m15_detectors[symbol].active_fvgs or [])
        except Exception:
            pass
        candidates.extend(state.get("extra_levels", []) or [])

        count = 0
        for lvl in candidates:
            if lvl is chosen:
                continue
            try:
                l_top = float(lvl["top"])
                l_bottom = float(lvl["bottom"])
            except Exception:
                continue
            if l_bottom <= top and l_top >= bottom:
                count += 1
        return count

    def _confluence_score(self, state: dict, displacement_atr: float | None) -> int:
        """
        Genuine 0–100 confluence score (audit §10.6 — this was a hard-coded 85 on
        every signal, which made the spec's "only a clearly A+ setup may be the 2nd
        trade after a loss" day-stop rule degenerate: with
        `a_plus_confluence_threshold` also at 85 the gate read `85 >= 85` and every
        setup qualified as A+).

        Components (sum to 100):

          50  MANDATORY CHAIN — awarded whenever a signal fires, because the state
              machine verified all of it to get here: an H4 bias established from an
              HTF FVG → an M15 key level (FVG, CISD line, or rejection block) tapped
              by an M5 bar → an opposing M5 FVG formed during the reaction → an M5
              body closed THROUGH it (the inversion) → the swing extreme was never
              breached → the bar is inside the 09:30–11:00 ET window → the daily
              trade cap is not exhausted.

          20  LEVEL CONFLUENCE — 10 points per ADDITIONAL independently-derived key
              level whose price band overlaps the tapped one, capped at 2 (see
              _count_confluent_levels). One method marking a price is a level; three
              marking the same price is a confluence.

          15  DISPLACEMENT — how decisively the inversion candle closed beyond the
              IFVG boundary, in ATR terms. ≥0.50×ATR → 15, ≥0.25×ATR → 8, else 0.

          15  STOP QUALITY — 15 when the structural stop (swing + ATR buffer) was
              already wider than the cost floor, 0 when the floor had to widen it.
              A floored stop means the IFVG swing sat inside the spread.

        Range in practice: 50 → 100. Note that `a_plus_confluence_threshold` is
        currently 90 — a params-level workaround for this defect that made the A+
        gate deliberately unsatisfiable. With a real score it is now reachable
        (requires the mandatory chain plus at least two of the three quality
        components), so that default is worth revisiting per audit §6.
        """
        score = 50

        score += 10 * min(2, int(state.get("confluent_level_count", 0) or 0))

        if displacement_atr is not None:
            if displacement_atr >= 0.50:
                score += 15
            elif displacement_atr >= 0.25:
                score += 8

        if not state.get("sl_floored"):
            score += 15

        return max(0, min(100, int(score)))

    def _is_within_session(self, current_time: pd.Timestamp) -> bool:
        if current_time.tzinfo is None:
            current_time = current_time.tz_localize('UTC')
        ny_tz = pytz.timezone('America/New_York')
        ny_time = current_time.astimezone(ny_tz)
        time_str = ny_time.strftime("%H:%M")
        
        start = self.params.session_start
        cutoff = self.params.session_cutoff
        
        if start <= cutoff:
            return start <= time_str <= cutoff
        else:
            return time_str >= start or time_str <= cutoff

    def get_required_timeframes(self) -> list[str]:
        return ["H4", "M15", "M5"]

    async def on_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame) -> TradeSignal | None:
        self._init_state(symbol)
        state = self.state[symbol]
        
        current_time = candles.index[-1]
        latest = candles.iloc[-1]
        
        # [6.1b/S2] Scoped to the day-stop TRADE COUNTERS only — these are
        # genuinely calendar-day rules by spec design ("stop after 1 win
        # TODAY", "2 losses TODAY"). The in-progress setup (bias/key_level/
        # status/m5_fvg_to_invert/etc) used to be wiped here too, at the exact
        # UTC-midnight boundary regardless of freshness — that's now handled
        # below by an explicit bar-age budget instead (setup_max_age_bars).
        current_date = current_time.date()
        if symbol not in self.last_trade_date or self.last_trade_date[symbol] != current_date:
            state["trades_today"] = 0
            state["wins_today"] = 0
            state["losses_today"] = 0
            self.last_trade_date[symbol] = current_date
            self.log_event(f"[{symbol}] Day-stop counters reset for new trading day.", category="BIAS_IFVG")

        if timeframe == "M5":
            self.m5_bar_index[symbol] = self.m5_bar_index.get(symbol, 0) + 1
            setup_max_age = getattr(self.params, "setup_max_age_bars", 0) or 0
            if (
                setup_max_age > 0
                and state["status"] not in ("AWAIT_BIAS", "AWAIT_KEY_LEVEL")
                and state.get("setup_started_bar") is not None
                and (self.m5_bar_index[symbol] - state["setup_started_bar"]) > setup_max_age
            ):
                self.log_event(
                    f"[{symbol}] setup_expired_{state['status'].lower()} — no signal after "
                    f"{self.m5_bar_index[symbol] - state['setup_started_bar']} M5 bars "
                    f"(budget {setup_max_age}). Reverting to key-level search (bias preserved).",
                    category="BIAS_IFVG",
                )
                state["status"] = "AWAIT_KEY_LEVEL"
                state["key_level"] = None
                state["m5_fvg_to_invert"] = None
                state["m5_swing_point"] = None
                state["manipulation_leg_start"] = None
                state["approach_leg_start"] = None
                state["setup_started_bar"] = None

        # 1. Determine Bias (HTF: H4) — always update on each H4 bar regardless of status.
        # Spec: "15m secondary check can flip bias intraday" — bias must be re-evaluated continuously.
        if timeframe == "H4":
            htf_fvgs = self.htf_detectors[symbol].update(candles)
            if htf_fvgs:
                last_fvg = htf_fvgs[-1]
                new_bias = "BUY" if last_fvg["type"] == "BULLISH" else "SELL"
                if new_bias != state["bias"]:
                    self.log_event(f"[{symbol}] HTF Bias flipped: {state['bias']} → {new_bias}", category="BIAS_IFVG")
                    # Bias flip invalidates any in-progress setup — restart from key-level search.
                    state["bias"] = new_bias
                    state["key_level"] = None
                    state["m5_fvg_to_invert"] = None
                    state["m5_swing_point"] = None
                    state["manipulation_leg_start"] = None
                    state["approach_leg_start"] = None
                    state["setup_started_bar"] = None
                    state["status"] = "AWAIT_KEY_LEVEL"
                elif state["status"] == "AWAIT_BIAS":
                    # First time bias is established
                    state["bias"] = new_bias
                    state["status"] = "AWAIT_KEY_LEVEL"
                    self.log_event(f"[{symbol}] Bias established: {state['bias']} based on HTF FVG", category="BIAS_IFVG")
        
        # 2. Key Levels (M15 tracker update)
        elif timeframe == "M15":
            self.m15_detectors[symbol].update(candles)
            if state["bias"] is not None:
                new_extra = self._detect_cisd_and_rejections(candles, state["bias"])
                if new_extra:
                    if "extra_levels" not in state:
                        state["extra_levels"] = []
                    state["extra_levels"].extend(new_extra)
                    # Keep only the latest 10 to avoid bloat
                    state["extra_levels"] = state["extra_levels"][-10:]

        # 3. IFVG Confirmation and Entry (M5)
        elif timeframe == "M5":
            m5_fvgs = []
            
            # Update M5 FVGs
            if state["status"] in ["AWAIT_IFVG_SETUP", "AWAIT_IFVG_CLOSE"]:
                m5_fvgs = self.m5_detectors[symbol].update(candles)
                
            # Check for M15 Tap (Real-time detection using M5 candle).
            # Only run while still searching for the key level itself — running this
            # while already AWAIT_IFVG_SETUP kept re-firing on every bar price dwells
            # near the zone, repeatedly pushing manipulation_leg_start forward and
            # starving the subsequent IFVG search of ever finding a qualifying gap.
            if state["status"] == "AWAIT_KEY_LEVEL":
                # Check FVGs
                m15_fvgs = self.m15_detectors[symbol].active_fvgs
                for fvg in reversed(m15_fvgs):
                    if state["bias"] == "BUY" and fvg["type"] == "BULLISH" and fvg["bottom"] <= latest["low"] <= fvg["top"]:
                        state["key_level"] = fvg
                        state["status"] = "AWAIT_IFVG_SETUP"
                        state["manipulation_leg_start"] = current_time
                        state["approach_leg_start"] = self._approach_leg_start(candles, state["bias"])
                        state["setup_started_bar"] = self.m5_bar_index.get(symbol, 0)  # [6.1b/S2]
                        state["confluent_level_count"] = self._count_confluent_levels(symbol, state, fvg)
                        self.log_event(f"[{symbol}] M15 Bullish FVG tapped by M5. Awaiting M5 IFVG.", category="BIAS_IFVG")
                        break
                    elif state["bias"] == "SELL" and fvg["type"] == "BEARISH" and fvg["bottom"] <= latest["high"] <= fvg["top"]:
                        state["key_level"] = fvg
                        state["status"] = "AWAIT_IFVG_SETUP"
                        state["manipulation_leg_start"] = current_time
                        state["approach_leg_start"] = self._approach_leg_start(candles, state["bias"])
                        state["setup_started_bar"] = self.m5_bar_index.get(symbol, 0)  # [6.1b/S2]
                        state["confluent_level_count"] = self._count_confluent_levels(symbol, state, fvg)
                        self.log_event(f"[{symbol}] M15 Bearish FVG tapped by M5. Awaiting M5 IFVG.", category="BIAS_IFVG")
                        break

                # Check Extra Levels (CISD / Rejections)
                if state["status"] == "AWAIT_KEY_LEVEL":
                    for lvl in reversed(state.get("extra_levels", [])):
                        if state["bias"] == "BUY" and lvl["type"] in ["BULLISH_CISD", "BULLISH_REJECTION"] and lvl["bottom"] <= latest["low"] <= lvl["top"]:
                            state["key_level"] = lvl
                            state["status"] = "AWAIT_IFVG_SETUP"
                            state["manipulation_leg_start"] = current_time
                            state["approach_leg_start"] = self._approach_leg_start(candles, state["bias"])
                            state["setup_started_bar"] = self.m5_bar_index.get(symbol, 0)  # [6.1b/S2]
                            state["confluent_level_count"] = self._count_confluent_levels(symbol, state, lvl)
                            self.log_event(f"[{symbol}] M15 {lvl['type']} tapped by M5. Awaiting M5 IFVG.", category="BIAS_IFVG")
                            break
                        elif state["bias"] == "SELL" and lvl["type"] in ["BEARISH_CISD", "BEARISH_REJECTION"] and lvl["bottom"] <= latest["high"] <= lvl["top"]:
                            state["key_level"] = lvl
                            state["status"] = "AWAIT_IFVG_SETUP"
                            state["manipulation_leg_start"] = current_time
                            state["approach_leg_start"] = self._approach_leg_start(candles, state["bias"])
                            state["setup_started_bar"] = self.m5_bar_index.get(symbol, 0)  # [6.1b/S2]
                            state["confluent_level_count"] = self._count_confluent_levels(symbol, state, lvl)
                            self.log_event(f"[{symbol}] M15 {lvl['type']} tapped by M5. Awaiting M5 IFVG.", category="BIAS_IFVG")
                            break
            
            # [D-5 — RESOLVED 2026-08-23] Look for the opposing M5 FVG to invert.
            #
            # Which leg it may come from is now a setting rather than an
            # unresolved ambiguity. The spec (Step 3) defines the manipulation
            # leg as "the swing that ACTUALLY TOUCHED the key level" and says to
            # scan for gaps "within that specific leg" — that is the APPROACH,
            # so APPROACH is the default. The engine's previous behaviour
            # (post-tap gaps only) is retained as REACTION because the existing
            # backtest corpus was produced under it. See BiasIFVGParams.
            if state["status"] == "AWAIT_IFVG_SETUP":
                leg_mode = str(getattr(self.params, "ifvg_leg_mode", "APPROACH") or "APPROACH").upper()
                tap_time = state.get("manipulation_leg_start") or pd.Timestamp.min
                leg_start = state.get("approach_leg_start") or tap_time

                if leg_mode == "REACTION":
                    window_start, window_end = tap_time, pd.Timestamp.max
                elif leg_mode == "BOTH":
                    window_start, window_end = leg_start, pd.Timestamp.max
                else:  # APPROACH — spec-conformant default
                    window_start, window_end = leg_start, tap_time

                for fvg in reversed(m5_fvgs):
                    fvg_time = fvg.get("index", pd.Timestamp.min)
                    if window_start <= fvg_time <= window_end:
                        if state["bias"] == "BUY" and fvg["type"] == "BEARISH":
                            state["m5_fvg_to_invert"] = fvg
                            state["status"] = "AWAIT_IFVG_CLOSE"
                            lookback = candles.iloc[-20:]
                            state["m5_swing_point"] = lookback["low"].min()
                            break
                        elif state["bias"] == "SELL" and fvg["type"] == "BULLISH":
                            state["m5_fvg_to_invert"] = fvg
                            state["status"] = "AWAIT_IFVG_CLOSE"
                            lookback = candles.iloc[-20:]
                            state["m5_swing_point"] = lookback["high"].max()
                            break

            if state["status"] == "AWAIT_IFVG_CLOSE":
                # Session filter blocks entries, but doesn't delete active setup
                if not self._is_within_session(current_time):
                    return None
                    
                if state["trades_today"] >= self.params.max_trades_per_day:
                    return None

                # Spec's mandatory day-stop rule (stop after 1 win; after 1 loss only
                # take a 2nd trade if it's A+; stop after 2 losses regardless).
                # The unconditional halves of that rule are cheap and setup-independent,
                # so they short-circuit here. The A+ gate itself needs a REAL confluence
                # score, which is only computable once the trigger has fired and the stop
                # is known — so it is re-checked, in full, just before the signal is
                # emitted (audit §10.6).
                if state.get("wins_today", 0) >= 1 or state.get("losses_today", 0) >= 2:
                    return None

                fvg = state["m5_fvg_to_invert"]
                triggered = False
                
                # Check for body close through the opposing FVG
                if state["bias"] == "BUY" and latest["close"] > fvg["top"]:
                    triggered = True
                elif state["bias"] == "SELL" and latest["close"] < fvg["bottom"]:
                    triggered = True
                    
                # Invalidate if price breaks the swing extreme before inversion.
                # `m5_swing_point` is initialised to None, so `.get(key, default)` does
                # NOT protect these comparisons — the key exists. Resolve it explicitly
                # so a None/NaN reference can never raise here (audit §10.5).
                swing_ref = state.get("m5_swing_point")
                swing_valid = swing_ref is not None and not pd.isna(swing_ref)
                if swing_valid and state["bias"] == "BUY" and latest["close"] < swing_ref:
                    state["status"] = "AWAIT_KEY_LEVEL"
                    self.log_event(f"[{symbol}] Bias IFVG setup failed (Swing low broken).", category="BIAS_IFVG")
                    return None
                elif swing_valid and state["bias"] == "SELL" and latest["close"] > swing_ref:
                    state["status"] = "AWAIT_KEY_LEVEL"
                    self.log_event(f"[{symbol}] Bias IFVG setup failed (Swing high broken).", category="BIAS_IFVG")
                    return None

                if triggered:
                    entry = latest["close"]

                    # ── Missing structural reference = no setup (audit §10.5) ──
                    # This used to fall back to `entry * 0.99` — a 1%-of-price stop,
                    # i.e. ~80 pips on USDCHF or ~200 points on NAS100. A missing dict
                    # key silently swung the stop distance (and therefore the position
                    # size) by ~80x with no log line. A missing swing reference means
                    # there is no setup, not a setup with an arbitrary stop.
                    if not swing_valid:
                        self.log_event(
                            f"[{symbol}] IFVG inversion confirmed but m5_swing_point is missing — "
                            f"no structural stop reference, discarding setup (no signal).",
                            level="WARN",
                            category="BIAS_IFVG",
                        )
                        state["status"] = "AWAIT_KEY_LEVEL"
                        return None

                    atr = calculate_atr(candles, 14)
                    pip_size = get_pip_size(symbol) or 0.0001

                    # Structural stop + sl_buffer_atr_mult cushion + cost floors
                    # (audit §10.3 / §10.4 — sl_buffer_atr_mult was previously dead
                    # in this engine: the identifier appeared nowhere in the file).
                    sl_dist, floored = self._resolve_sl_distance(
                        symbol, entry, float(swing_ref), atr, pip_size
                    )
                    state["sl_floored"] = floored

                    # Use configurable RR target (default 2.0)
                    target_rr = getattr(self.params, 'target_rr', 2.0)
                    if state["bias"] == "BUY":
                        sl = entry - sl_dist
                        tp = entry + sl_dist * target_rr
                    else:
                        sl = entry + sl_dist
                        tp = entry - sl_dist * target_rr

                    # Displacement of the inversion close beyond the IFVG boundary,
                    # in ATR terms — a confluence input, see _confluence_score().
                    boundary = fvg["top"] if state["bias"] == "BUY" else fvg["bottom"]
                    displacement_atr = (abs(entry - boundary) / atr) if atr > 0 else None
                    confluence_score = self._confluence_score(state, displacement_atr)

                    # Hard day-stop rule (unconditional halves only — see
                    # _can_trade_today's docstring).
                    if not self._can_trade_today(state):
                        self.log_event(
                            f"[{symbol}] Setup rejected by hard day-stop rule "
                            f"(wins={state.get('wins_today', 0)}, losses={state.get('losses_today', 0)}).",
                            category="BIAS_IFVG",
                        )
                        state["status"] = "AWAIT_KEY_LEVEL"
                        return None

                    # [6.12/S15] 2nd-trade-after-1-loss A+ rule — CLAMP, not BLOCK.
                    size_modifier = self._second_trade_size_modifier(state, candidate_confluence=confluence_score)
                    if size_modifier < 1.0:
                        self.log_event(
                            f"[{symbol}] 2nd trade after 1 loss: confluence {confluence_score} < "
                            f"a_plus_confluence_threshold {getattr(self.params, 'a_plus_confluence_threshold', 85)} "
                            f"— sizing clamped to {size_modifier:.0%} instead of skipped.",
                            category="BIAS_IFVG",
                        )

                    state["status"] = "AWAIT_KEY_LEVEL"
                    state["trades_today"] += 1

                    # [V1 section C.6] Chart markings. The chain here is
                    # bias -> key level tapped -> opposing M5 FVG -> inversion
                    # close. `confluent_level_count` was already measured for
                    # the confluence score; this is what puts the levels it
                    # counted on the chart instead of just their count.
                    mk = MarkingCollector("M5")
                    entry_t = ts(latest.get("time", current_time))
                    key_level = state.get("key_level") or {}

                    mk.structure(
                        f"Daily bias: {state['bias']}", entry_t, role=ROLE_CONFLUENCE,
                        bias=state["bias"],
                        confluent_levels=int(state.get("confluent_level_count", 0) or 0),
                    )
                    if key_level.get("top") is not None:
                        mk.box(
                            "OB" if "CISD" in str(key_level.get("type", "")) else "FVG",
                            f"Key level ({key_level.get('type', '')})",
                            key_level["top"], key_level["bottom"],
                            ts(key_level.get("index", entry_t)),
                            end_time=entry_t, role=ROLE_CONFLUENCE,
                            color="rgba(168,85,247,0.16)",
                            level_type=key_level.get("type"),
                            consequent_encroachment=key_level.get("ce"),
                            confluent_level_count=int(state.get("confluent_level_count", 0) or 0),
                        )
                    if fvg.get("top") is not None:
                        mk.fvg(
                            f"IFVG ({fvg.get('type', '')} -> inverted)",
                            fvg["top"], fvg["bottom"], ts(fvg.get("index", entry_t)),
                            end_time=entry_t, role=ROLE_TRIGGER,
                            color="rgba(59,130,246,0.18)",
                            fvg_type=fvg.get("type"),
                            inversion_boundary=round(float(boundary), 6),
                        )
                    mk.structure(
                        "Inversion close (displacement)", entry_t, price=entry,
                        role=ROLE_TRIGGER,
                        displacement_atr=round(displacement_atr, 3) if displacement_atr is not None else None,
                        atr=round(float(atr), 6) if atr else None,
                        size_modifier=size_modifier,
                    )
                    mk.level(
                        "M5 structural swing (stop reference)", float(swing_ref), entry_t,
                        role=ROLE_CONTEXT, color="rgba(148,163,184,0.8)",
                    )
                    mk.level(
                        "Stop loss", sl, entry_t, role=ROLE_INVALIDATION,
                        color="rgba(239,68,68,0.9)",
                        sl_distance=round(sl_dist, 6),
                        sl_pips=round(sl_dist / pip_size, 2) if pip_size else None,
                        floored=bool(floored),
                    )
                    mk.level(
                        f"Take profit ({target_rr}R)", tp, entry_t, role=ROLE_CONTEXT,
                        color="rgba(16,185,129,0.9)", target_rr=target_rr,
                    )

                    return TradeSignal(
                        strategy_id="BiasIFVG_v1",
                        symbol=symbol,
                        direction=state["bias"],
                        timeframe="M5",
                        entry_price=entry,
                        stop_loss=sl,
                        take_profit=tp,
                        confluence_score=confluence_score,
                        timestamp=float(latest.get("time", current_time.timestamp())),
                        metadata={
                            "setup": "BIAS_IFVG",
                            "sl_pips": round(sl_dist / pip_size, 2),
                            "sl_floored": floored,
                            "swing_point": float(swing_ref),
                            "confluent_levels": int(state.get("confluent_level_count", 0) or 0),
                            "displacement_atr": round(displacement_atr, 3) if displacement_atr is not None else None,
                            "size_modifier": size_modifier,
                            # [V1] Chart geometry — see strategies/core/markings.py.
                            **mk.as_metadata(),
                        }
                    )

        return None
