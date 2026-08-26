"""
backend/strategies/strategy_apa/engine.py

Advanced Price Action (APA) Strategy Engine
============================================
Implements a fully rule-based Head & Shoulders / ABC structural reversal
with Invalidation Zone retest entry.

State Machine (per candidate — see [6.2] below):
  (scan) → AWAIT_BOS → AWAIT_RETEST → AWAIT_CONFIRMATION → (signal fired, candidate removed)

[6.1/6.2] Up to `max_concurrent_patterns` candidates are tracked per symbol
simultaneously, each with its own status and age. This replaces the old
design (one `state[symbol]["pattern"]` slot, wiped on UTC-midnight calendar
change): a single slow-to-resolve setup no longer blocks every other
opportunity on the same symbol, and staleness is now an explicit per-state
bar-count budget (`pattern_max_age_bars`/`bos_max_age_bars`/
`retest_max_age_bars`) rather than an arbitrary calendar boundary that wiped
fresh AWAIT_BOS patterns while leaving genuinely stuck AWAIT_RETEST/
AWAIT_CONFIRMATION setups untouched.

Source: docs/apa_strategy_implementation_plan.md (Michael FX / A.P.A. framework)
"""

import pandas as pd
import pytz

from backend.core.config_schema import UserConfigV2
from backend.risk.position_sizer import get_pip_size
from backend.strategies.base_strategy import BaseStrategy, TradeSignal
from backend.strategies.core.markings import (
    ROLE_CONFLUENCE,
    ROLE_CONTEXT,
    ROLE_INVALIDATION,
    ROLE_TRIGGER,
    MarkingCollector,
    ts,
)
from backend.strategies.core.swing_structure import (
    calculate_atr,
    detect_swings,
    detect_hs_pattern,
)
from backend.strategies.registry import register_strategy
from backend.utils.logger import get_logger

logger = get_logger(__name__)


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
                # [6.2] Bounded list of independently-progressing candidates —
                # was a single dict slot. Each candidate carries exactly the
                # fields the old single-slot state dict used to.
                "candidates": [],
                # [6.1] Bar counters driving the staleness budgets — replace
                # the old UTC-calendar-date reset entirely.
                "structure_bar_index": 0,
                "entry_bar_index": 0,
            }

    def _sl_floor_distance(self, symbol: str, atr: float, pip_size: float) -> float:
        """
        Absolute minimum stop distance in PRICE units (audit §10.8).

            floor = max(min_sl_pips  * pip_size,          # absolute backstop
                        min_sl_atr_mult * atr,            # volatility-relative backstop
                        min_sl_spread_mult * spread * pip_size)   # spread-relative (opt-in)

        [6.5/S7] Then capped at `max_sl_floor_atr_mult * atr` (0 = uncapped) —
        the floor's job is rescuing a too-tight stop, not silently re-widening
        one that was already comfortably wide.

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

        max_mult = getattr(self.params, "max_sl_floor_atr_mult", 0.0) or 0.0
        if max_mult > 0 and atr > 0:
            floor = min(floor, max_mult * atr)

        return floor

    def _confluence_score(self, candidate: dict) -> int:
        """
        Genuine 0–100 confluence score (audit §10.6 — this was a hard-coded 90 on
        every signal, which made `confluence_stats` in every report meaningless).

        Components (sum to 100):

          43  MANDATORY CHAIN — awarded whenever a signal fires at all, because
              every one of these was verified by the state machine before we got
              here: H&S pattern with shoulders inside `shoulder_symmetry_tolerance_atr`
              → body-close BOS through the neckline → neckline within
              `neckline_major_atr_tolerance` of a major fractal swing → Head
              level never violated → SL verified on the correct side of entry.

              REDUCED 55 → 43 (2026-08-24). The retest into the Invalidation
              Zone used to be part of this guaranteed chain; with
              `require_retest` defaulting False it no longer is, so the 12
              points it represented moved to the RETEST REJECTION component
              below. A signal that did retest AND was rejected scores the same
              55 baseline it always did; one that entered straight off the BOS
              scores 43, which is the honest difference between them.

          12  RETEST REJECTION — the retest candle wicked into the Invalidation
              Zone and closed back out on the trade's side, i.e. the zone
              actually held. Awarded only when a retest occurred AND rejected;
              0 both when no retest happened and when one happened without
              rejecting. Never a gate — requiring it would reintroduce exactly
              the selection problem `require_retest=False` removes.

          15  SHOULDER SYMMETRY — how far inside the tolerance band the two shoulders
              actually sat. ≤1/3 of tolerance → 15, ≤2/3 → 8, else 0. A pattern that
              only just squeaked past the symmetry test is a weaker H&S than one whose
              shoulders are near-identical.

          15  NECKLINE PRECISION — distance from the neckline to the nearest major
              fractal swing, as a fraction of ATR. [6.4] The admission gate is now
              `neckline_major_atr_tolerance` (default 1.0, was a hardcoded 0.5), but
              this component still only awards points for a TIGHT match
              (≤0.15×ATR → 15, ≤0.30×ATR → 8, else 0) — a pattern admitted only
              because the gate was widened scores 0 here, deploying less size
              (via confluence-scaled risk) rather than being silently discarded.

          15  STOP QUALITY — 15 when the structural stop was already wider than the
              cost floor, 0 when the floor had to widen it. A floored stop means the
              shoulder wick sat inside the spread: the geometry is real but the
              economics were not, and the trade is now running on a synthetic stop.

        Range in practice: 43 (bare minimum, no retest, floored stop) → 100.
        """
        score = 43

        # Retest rejection — evidence the zone held. Contributor, not a gate.
        if candidate.get("retest_rejected"):
            score += int(getattr(self.params, "rejection_candle_confluence_points", 12) or 0)

        tol = getattr(self.params, "shoulder_symmetry_tolerance_atr", 0.0) or 0.0
        gap = candidate.get("shoulder_symmetry_gap_atr")
        if gap is not None and tol > 0:
            ratio = gap / tol
            if ratio <= 1.0 / 3.0:
                score += 15
            elif ratio <= 2.0 / 3.0:
                score += 8

        precision = candidate.get("neckline_precision_atr")
        if precision is not None:
            if precision <= 0.15:
                score += 15
            elif precision <= 0.30:
                score += 8

        if not candidate.get("sl_floored"):
            score += 15

        return max(0, min(100, int(score)))

    def _session_filter_applies(self, symbol: str) -> bool:
        """
        Whether the equity-session window is meaningful for this instrument.

        Measured across 691 saved trades on 8 symbols, expectancy by session:

            NY         +0.358   LONDON     +0.075
            LONDON/NY  -0.128   ASIAN      -0.112

        — but that is an aggregate over instruments that do not share a clock.
        Per symbol the sign reverses for the 24/7 ones: XRPUSD is +0.56 in the
        Asian window and -0.20 in NY, while XAUUSD is +0.73 in NY and -0.30 in
        Asian. Equity sessions describe when equity-linked liquidity arrives;
        crypto and synthetics have no such rhythm, so applying the window to
        them removes trades for no reason.

        `trades_24_7` already marks exactly these instruments on the profile, so
        the exemption reads off existing data rather than a new symbol list that
        would need maintaining.
        """
        if not getattr(self.params, "session_filter_skip_24_7", True):
            return True
        try:
            from backend.risk.compounding import get_instrument_profile
            profile = get_instrument_profile(symbol)
            if profile is not None and getattr(profile, "trades_24_7", False):
                return False
        except Exception:
            # Unknown instrument — apply the filter rather than silently
            # widening when the session window is what the user configured.
            pass
        return True

    def _is_within_session(self, current_time: pd.Timestamp, symbol: str | None = None) -> bool:
        if not self.params.session_filter_enabled:
            return True
        if symbol is not None and not self._session_filter_applies(symbol):
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

    @staticmethod
    def _pattern_identity(pattern: dict) -> tuple:
        """
        [6.2] Fingerprint used to avoid re-adding the same H&S pattern as a
        duplicate candidate on every bar while it's still sitting in
        AWAIT_BOS (detect_hs_pattern re-detects the same formation every
        call until price moves past it).
        """
        return (
            pattern.get("type"),
            round(pattern.get("neckline_price", 0.0), 6),
            round(pattern.get("head", {}).get("price", 0.0), 6),
        )

    async def on_bar(self, symbol: str, timeframe: str, candles: pd.DataFrame) -> TradeSignal | None:
        self._init_state(symbol)
        state = self.state[symbol]
        candidates: list = state["candidates"]

        if len(candles) < (self.params.major_fractal_m * 2 + 5):
            return None

        current_time = candles.index[-1]
        latest = candles.iloc[-1]
        atr = calculate_atr(candles, self.params.atr_lookback)

        # ── STRUCTURE TIMEFRAME PROCESSING ──────────────────────────────
        if timeframe == self.params.structure_timeframe:
            state["structure_bar_index"] += 1
            sbi = state["structure_bar_index"]

            # [6.1] Age out AWAIT_BOS candidates that never broke structure.
            pattern_max_age = getattr(self.params, "pattern_max_age_bars", 0) or 0
            if pattern_max_age > 0:
                still_fresh = []
                for c in candidates:
                    if c["status"] == "AWAIT_BOS" and (sbi - c["created_at_structure_bar"]) > pattern_max_age:
                        self.log_event(
                            f"[{symbol}] setup_expired_await_bos — pattern stale after "
                            f"{sbi - c['created_at_structure_bar']} structure bars "
                            f"(budget {pattern_max_age}).",
                            category="APA",
                        )
                        continue
                    still_fresh.append(c)
                candidates[:] = still_fresh

            # Detect minor swings (for H&S detection)
            minor_swings = detect_swings(candles, self.params.minor_fractal_m)
            # Detect major swings (for BOS filter)
            major_swings = detect_swings(candles, self.params.major_fractal_m)
            major_prices = [s["price"] for s in major_swings]

            # Step 1: scan for a NEW H&S pattern, if there's room for one.
            max_concurrent = max(1, getattr(self.params, "max_concurrent_patterns", 1) or 1)
            await_bos_count = sum(1 for c in candidates if c["status"] == "AWAIT_BOS")
            if await_bos_count < max_concurrent:
                pattern = detect_hs_pattern(
                    minor_swings, atr, self.params.shoulder_symmetry_tolerance_atr
                )
                if pattern:
                    identity = self._pattern_identity(pattern)

                    # Track a pattern once, in ANY state — not just AWAIT_BOS.
                    #
                    # The old check only matched candidates still awaiting BOS, so
                    # a pattern that had progressed past that stage counted as
                    # untracked and was re-added on the next structure bar. That
                    # was survivable while a retest was mandatory (the duplicate
                    # sat in AWAIT_RETEST and usually expired), but with
                    # `require_retest=False` a re-added candidate BOSes instantly
                    # — price is already beyond the neckline — and fires on the
                    # same bar. The result was the same H&S emitting a signal
                    # every few milliseconds: tens of thousands of duplicate
                    # signals, a log growing without bound, and a backtest that
                    # never finished.
                    already_tracked = any(
                        self._pattern_identity(c["pattern"]) == identity
                        for c in candidates
                    )

                    # A pattern that has already produced a signal must not
                    # re-arm. Removing the candidate on fire is what made it
                    # invisible to the check above; this remembers it instead.
                    fired = state.setdefault("fired_identities", {})
                    if identity in fired:
                        already_tracked = True

                    if not already_tracked:
                        direction = "SELL" if pattern["type"] == "BEARISH" else "BUY"
                        candidates.append({
                            "status": "AWAIT_BOS",
                            "pattern": pattern,
                            "direction": direction,
                            "created_at_structure_bar": sbi,
                        })
                        self.log_event(
                            f"[{symbol}] {pattern['type']} H&S pattern detected "
                            f"({len(candidates)}/{max_concurrent} candidates). "
                            f"Neckline: {pattern['neckline_price']:.5f}. Awaiting BOS.",
                            category="APA",
                        )

            # Step 2: BOS — candle CLOSE beyond neckline on a (near-)major swing level.
            # [6.3] Was min(open,close)/max(open,close) — a wick-inclusive test on
            # one side. A body close through the neckline is not the same as a
            # close beyond it; the spec calls for the latter.
            for c in list(candidates):
                if c["status"] != "AWAIT_BOS":
                    continue
                pattern = c["pattern"]
                neckline = pattern["neckline_price"]

                bos_bearish = pattern["type"] == "BEARISH" and latest["close"] < neckline
                bos_bullish = pattern["type"] == "BULLISH" and latest["close"] > neckline
                if not (bos_bearish or bos_bullish):
                    continue

                # [6.4] Neckline must sit within neckline_major_atr_tolerance of a
                # major swing — was a hardcoded 0.5x. A pattern between 0.5x and
                # this tolerance is now admitted at reduced confluence rather
                # than discarded outright (see _confluence_score's NECKLINE
                # PRECISION component).
                major_tol = getattr(self.params, "neckline_major_atr_tolerance", 0.5) or 0.5
                nearest_major_dist = min((abs(mp - neckline) for mp in major_prices), default=None)
                is_major = nearest_major_dist is not None and nearest_major_dist <= atr * major_tol
                if not is_major:
                    self.log_event(
                        f"[{symbol}] Body closed beyond neckline but NOT within "
                        f"{major_tol:g}xATR of a major level — liquidity sweep, dropping candidate.",
                        category="APA",
                    )
                    candidates.remove(c)
                    continue

                # Draw Invalidation Zone from Right Shoulder candle body, or from
                # both shoulders' bodies when invalidation_zone_source == "both".
                # [E8 / conformance §1.2] `left_shoulder` used to fall through to
                # right-shoulder behaviour: the list was seeded with `rs`
                # unconditionally and only "both" ever added the left one, so
                # selecting "left_shoulder" silently did nothing. A config value
                # that is accepted and then ignored is worse than one that is
                # rejected — you cannot tell it had no effect from the result.
                rs = pattern["right_shoulder"]
                ls = pattern["left_shoulder"]
                _iz_source = getattr(self.params, "invalidation_zone_source", "right_shoulder")
                if _iz_source == "both":
                    iz_shoulders = [rs, ls]
                elif _iz_source == "left_shoulder":
                    iz_shoulders = [ls]
                else:
                    iz_shoulders = [rs]

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

                c["invalidation_zone_top"] = iz_top
                c["invalidation_zone_bottom"] = iz_bottom

                # SL = wick extreme of right shoulder + buffer. sl_buffer_atr_mult
                # widens this further, additive on top of sl_buffer_atr (both are
                # ATR multiples applied to the same wick reference — see params.py
                # docstrings, sl_buffer_atr_mult is explicitly "added ON TOP of the
                # structural SL distance").
                buffer = (self.params.sl_buffer_atr + self.params.sl_buffer_atr_mult) * atr
                head = pattern["head"]
                tight = abs(head["price"] - rs["price"]) < self.params.tight_level_threshold_atr * atr

                if pattern["type"] == "BEARISH":
                    sl_ref_wick = head["price"] if tight else rs["price"]
                    c["sl_level"] = sl_ref_wick + buffer
                    c["invalidation_head"] = head["price"]
                else:
                    sl_ref_wick = head["price"] if tight else rs["price"]
                    c["sl_level"] = sl_ref_wick - buffer
                    c["invalidation_head"] = head["price"]

                # Capture the structure-timeframe ATR for the cost floor applied
                # at signal-emission time (see _sl_floor_distance / audit §10.8).
                c["structure_atr"] = atr

                # ── Confluence measurements (audit §10.6) ────────────────
                ls = pattern["left_shoulder"]
                if atr > 0:
                    c["shoulder_symmetry_gap_atr"] = abs(ls["price"] - rs["price"]) / atr
                    c["neckline_precision_atr"] = nearest_major_dist / atr
                else:
                    c["shoulder_symmetry_gap_atr"] = None
                    c["neckline_precision_atr"] = None
                c["tight_levels"] = tight
                # Retest is now OPTIONAL. When it is not required the candidate
                # goes straight to confirmation on the BOS bar; the retest state
                # is skipped entirely rather than waited out and timed out.
                # See APAParams.require_retest for the measurement behind this.
                c["retest_occurred"] = False
                c["retest_rejected"] = False
                _needs_retest = bool(getattr(self.params, "require_retest", False))
                c["status"] = "AWAIT_RETEST" if _needs_retest else "AWAIT_CONFIRMATION"
                # [6.1] Retest-timeout clock starts from the current entry-bar
                # count (aged in entry-timeframe bars, not structure bars).
                c["bos_at_entry_bar"] = state["entry_bar_index"]
                # The AWAIT_CONFIRMATION expiry check reads `retest_at_entry_bar`.
                # When the retest is skipped nothing else ever sets it, so seed it
                # here from the BOS bar — the confirmation clock should start when
                # the candidate ENTERS confirmation, whichever route it took.
                if not _needs_retest:
                    c["retest_at_entry_bar"] = state["entry_bar_index"]
                self.log_event(
                    f"[{symbol}] BOS confirmed on major level "
                    f"({nearest_major_dist / atr if atr > 0 else float('nan'):.2f}xATR). "
                    f"IZ: [{iz_bottom:.5f} – {iz_top:.5f}]. Awaiting retest.",
                    category="APA",
                )
            return None

        # ── ENTRY TIMEFRAME PROCESSING ───────────────────────────────────
        elif timeframe == self.params.entry_timeframe:
            state["entry_bar_index"] += 1
            ebi = state["entry_bar_index"]

            # [6.1] Age out AWAIT_RETEST candidates whose retest never came —
            # this is the timeout the old single-slot design was entirely
            # missing ("the only invalidation is checked inside
            # AWAIT_CONFIRMATION, reached only after a retest touch").
            bos_max_age = getattr(self.params, "bos_max_age_bars", 0) or 0
            retest_max_age = getattr(self.params, "retest_max_age_bars", 0) or 0
            if bos_max_age > 0 or retest_max_age > 0:
                still_fresh = []
                for c in candidates:
                    if (
                        c["status"] == "AWAIT_RETEST"
                        and bos_max_age > 0
                        and (ebi - c["bos_at_entry_bar"]) > bos_max_age
                    ):
                        self.log_event(
                            f"[{symbol}] setup_expired_await_retest — no retest after "
                            f"{ebi - c['bos_at_entry_bar']} entry bars (budget {bos_max_age}).",
                            category="APA",
                        )
                        continue
                    if (
                        c["status"] == "AWAIT_CONFIRMATION"
                        and retest_max_age > 0
                        # `.get` with the BOS bar as fallback: a candidate that
                        # skipped the retest has no retest bar, and an unhandled
                        # KeyError here would abort the whole backtest.
                        and (ebi - c.get("retest_at_entry_bar", c.get("bos_at_entry_bar", ebi))) > retest_max_age
                    ):
                        self.log_event(
                            f"[{symbol}] setup_expired_await_confirmation — unconfirmed after "
                            f"{ebi - c['retest_at_entry_bar']} entry bars (budget {retest_max_age}).",
                            category="APA",
                        )
                        continue
                    still_fresh.append(c)
                candidates[:] = still_fresh

            if not candidates:
                return None

            if not self._is_within_session(current_time, symbol):
                return None

            # Step 3: Wait for candle BODY to enter the Invalidation Zone.
            for c in candidates:
                if c["status"] != "AWAIT_RETEST":
                    continue
                iz_top, iz_bottom = c["invalidation_zone_top"], c["invalidation_zone_bottom"]
                body_top = max(latest["open"], latest["close"])
                body_bottom = min(latest["open"], latest["close"])
                if body_bottom <= iz_top and body_top >= iz_bottom:
                    c["status"] = "AWAIT_CONFIRMATION"
                    c["retest_at_entry_bar"] = ebi
                    c["retest_occurred"] = True

                    # Did the zone actually REJECT price, or is the retrace still
                    # running? A rejection wicks into the zone and closes back out
                    # on the trade's side. This scores confluence points; it never
                    # gates the entry — see APAParams.rejection_candle_confluence_points.
                    if c["direction"] == "SELL":
                        # Bearish: wick pushed up into the zone, body closed below it.
                        c["retest_rejected"] = (
                            latest["high"] >= iz_bottom and latest["close"] < iz_bottom
                        )
                    else:
                        c["retest_rejected"] = (
                            latest["low"] <= iz_top and latest["close"] > iz_top
                        )

                    self.log_event(
                        f"[{symbol}] Retest into Invalidation Zone confirmed"
                        f"{' with rejection' if c['retest_rejected'] else ' (no rejection yet)'}. "
                        f"Checking confirmation rules.",
                        category="APA",
                    )

                    # [Phase 14 B3.4] Rejection-candle gate.
                    # When require_rejection_candle=True AND the retest candle
                    # already closed back out (retest_rejected is True on this
                    # same bar), we stay in AWAIT_CONFIRMATION and the signal
                    # fires below on this same bar iteration. If the candle only
                    # touched but did NOT close back out, we stay in
                    # AWAIT_CONFIRMATION and wait for the close-back-out to
                    # happen on a subsequent bar before firing.
                    # Either way, no `continue` here — the confirmation loop below
                    # will honour the gate on every iteration.

            # Step 4: Confirmation — all 5 rules from §4 (run on same bar),
            # tried oldest-candidate-first. [6.2] A candidate that fails
            # validation is dropped and the NEXT candidate is tried on the
            # same bar, instead of the whole symbol giving up for this tick.
            for c in list(candidates):
                if c["status"] != "AWAIT_CONFIRMATION":
                    continue

                # [Phase 14 B3.4] Rejection-candle gate: if the param is on and
                # the zone has not yet been rejected (close-back-out not yet
                # seen), skip this candidate for now. It stays AWAIT_CONFIRMATION
                # and will be re-evaluated on every subsequent bar until it
                # either rejects or the candidate expires via retest_max_age_bars.
                _require_reject = getattr(self.params, "require_rejection_candle", False)
                if _require_reject and not c.get("retest_rejected", False):
                    # Check if this bar itself provides the rejection (same bar
                    # as the IZ touch): if so, c["retest_rejected"] is already
                    # True from the AWAIT_RETEST block above and we fall through.
                    # Otherwise we skip until the rejection comes.
                    continue

                pattern = c["pattern"]
                direction = c["direction"]
                sl = c["sl_level"]
                head_price = c["invalidation_head"]

                # Hard invalidation: body closes back beyond the Head level
                if direction == "SELL" and min(latest["open"], latest["close"]) > head_price:
                    candidates.remove(c)
                    self.log_event(f"[{symbol}] Pattern invalidated — body closed beyond Head.", category="APA")
                    continue
                if direction == "BUY" and max(latest["open"], latest["close"]) < head_price:
                    candidates.remove(c)
                    self.log_event(f"[{symbol}] Pattern invalidated — body closed beyond Head.", category="APA")
                    continue

                # Rule 5: No conflicting open position (handled upstream by risk engine)
                # Rules 1–4 have been validated through the state machine

                entry = latest["close"]
                if sl is None:
                    candidates.remove(c)
                    continue

                # ── Validate SL is on the correct side of entry ──
                if direction == "SELL" and sl <= entry:
                    candidates.remove(c)
                    self.log_event(
                        f"[{symbol}] SELL SL ({sl:.5f}) <= entry ({entry:.5f}) — "
                        f"SL on wrong side, dropping candidate.",
                        category="APA",
                    )
                    continue
                if direction == "BUY" and sl >= entry:
                    candidates.remove(c)
                    self.log_event(
                        f"[{symbol}] BUY SL ({sl:.5f}) >= entry ({entry:.5f}) — "
                        f"SL on wrong side, dropping candidate.",
                        category="APA",
                    )
                    continue

                # ── Cost floors (audit §10.8) ────────────────────────────────
                pip_size = get_pip_size(symbol) or 0.0001
                floor_atr = c.get("structure_atr") or atr
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
                    c["sl_floored"] = True
                else:
                    c["sl_floored"] = False

                # TP: use risk engine's RR grid (SL distance × RR)
                if direction == "SELL":
                    tp = entry - sl_distance  # TP1 = 1R
                else:
                    tp = entry + sl_distance  # TP1 = 1R

                # Final sanity: SL and TP must not be equal
                if abs(tp - sl) < 1e-10:
                    candidates.remove(c)
                    self.log_event(
                        f"[{symbol}] SL ({sl:.5f}) == TP ({tp:.5f}) — degenerate, dropping candidate.",
                        category="APA",
                    )
                    continue

                confluence_score = self._confluence_score(c)

                # This candidate resolved into a signal — remove it, and remember
                # its identity so the detector cannot immediately re-arm the same
                # H&S on the next structure bar. Keyed to the entry-bar index so
                # the record can be aged out rather than growing for the life of
                # the run.
                _fired = state.setdefault("fired_identities", {})
                _fired[self._pattern_identity(pattern)] = state.get("entry_bar_index", 0)

                # Age out anything older than the pattern-staleness budget: past
                # that point the structure is gone, so a same-shaped pattern is a
                # genuinely new one rather than the same one re-detected.
                _age_budget = max(
                    int(getattr(self.params, "pattern_max_age_bars", 0) or 0) * 4, 500
                )
                _now_bar = state.get("entry_bar_index", 0)
                if len(_fired) > 64:
                    for _k in [k for k, v in _fired.items() if _now_bar - v > _age_budget]:
                        _fired.pop(_k, None)

                candidates.remove(c)

                self.log_event(
                    f"[{symbol}] APA SIGNAL FIRED: {direction} @ {entry:.5f} | SL: {sl:.5f} | "
                    f"TP1: {tp:.5f} | confluence: {confluence_score} | "
                    f"{len(candidates)} candidate(s) still active",
                    category="APA",
                )

                # [V1 section C.6] Chart markings. APA is a four-stage state
                # machine (pattern -> BOS -> retest -> confirmation) and until
                # now none of those stages left a trace on the chart. Each
                # marking below carries the bar the stage actually resolved on.
                mk = MarkingCollector(timeframe)
                entry_t = ts(latest.get("time", current_time))
                head = pattern.get("head") or {}
                ls = pattern.get("left_shoulder") or {}
                rs = pattern.get("right_shoulder") or {}

                for tag, pivot, role in (
                    ("Left shoulder", ls, ROLE_CONTEXT),
                    ("Head", head, ROLE_CONFLUENCE),
                    ("Right shoulder", rs, ROLE_CONTEXT),
                ):
                    if pivot.get("price") is not None:
                        mk.structure(
                            tag, ts(pivot.get("index", entry_t)), price=pivot["price"],
                            role=role, pattern_type=pattern["type"],
                            price_level=round(float(pivot["price"]), 6),
                        )

                mk.level(
                    "Neckline", pattern["neckline_price"], entry_t,
                    role=ROLE_TRIGGER, color="rgba(168,85,247,0.9)",
                    pattern_type=pattern["type"],
                    bos_direction="close below" if direction == "SELL" else "close above",
                    precision_atr=c.get("neckline_precision_atr"),
                    tight_levels=c.get("tight_levels"),
                )
                # The zone's ROLE now depends on whether a retest was required.
                # When it is not, the zone is context the setup was measured
                # against, not a condition the entry waited for — labelling it a
                # trigger would misdescribe what actually gated this trade.
                _retested = bool(c.get("retest_occurred"))
                _rejected = bool(c.get("retest_rejected"))
                mk.zone(
                    "Retest zone (invalidation band)"
                    + ("" if _retested else " — not required"),
                    c["invalidation_zone_top"], c["invalidation_zone_bottom"], entry_t,
                    role=ROLE_TRIGGER if _retested else ROLE_CONTEXT,
                    color="rgba(234,179,8,0.16)" if _retested else "rgba(148,163,184,0.10)",
                    zone_top=round(float(c["invalidation_zone_top"]), 6),
                    zone_bottom=round(float(c["invalidation_zone_bottom"]), 6),
                    symmetry_gap_atr=c.get("shoulder_symmetry_gap_atr"),
                    retest_required=bool(getattr(self.params, "require_retest", False)),
                    retest_occurred=_retested,
                    retest_rejected=_rejected,
                )
                if _rejected:
                    mk.structure(
                        "Retest rejection", entry_t, price=entry, role=ROLE_CONFLUENCE,
                        detail_note="wicked into the zone and closed back out",
                        confluence_points=int(
                            getattr(self.params, "rejection_candle_confluence_points", 12) or 0
                        ),
                    )
                if head.get("price") is not None:
                    mk.level(
                        "Head (invalidation)", head["price"], entry_t,
                        role=ROLE_INVALIDATION, color="rgba(239,68,68,0.75)",
                        rule="body close beyond head kills the pattern",
                    )
                mk.level(
                    "Stop loss", sl, entry_t, role=ROLE_INVALIDATION,
                    color="rgba(239,68,68,0.9)",
                    sl_distance=round(sl_distance, 6),
                    sl_pips=round(sl_distance / pip_size, 2) if pip_size else None,
                    floored=bool(c.get("sl_floored")),
                    floor_distance=round(sl_floor, 6) if sl_floor else None,
                    structure_atr=c.get("structure_atr"),
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
                        "invalidation_zone": [c["invalidation_zone_bottom"], c["invalidation_zone_top"]],
                        "sl_floored": bool(c.get("sl_floored")),
                        "sl_pips": round(sl_distance / pip_size, 2),
                        # Entry provenance: whether a retest was required, whether
                        # one happened, and whether the zone rejected. Recorded
                        # per-signal so a saved run states which entry rule
                        # produced it — results under require_retest True and
                        # False are not comparable.
                        "require_retest": bool(getattr(self.params, "require_retest", False)),
                        "retest_occurred": bool(c.get("retest_occurred")),
                        "retest_rejected": bool(c.get("retest_rejected")),
                        # [Phase 14 B2.3] Direct access key for on_position_bar's
                        # hard-invalidation exit. Also derivable from the markings
                        # list, but having it at the top level avoids O(n) search
                        # on every bar for every open position.
                        "invalidation_head": round(float(head["price"]), 6) if head.get("price") is not None else None,
                        # [V1] Chart geometry — see strategies/core/markings.py.
                        **mk.as_metadata(),
                    },
                )

        return None

    # ── Phase 14 B2.3: Hard invalidation exit ────────────────────────────────

    def on_position_bar(
        self,
        symbol: str,
        timeframe: str,
        candles: "pd.DataFrame",
        position: dict,
    ):
        """
        [Phase 14 B2.3] Close an open APA position when the current bar's BODY
        (open-close range, excluding wicks) closes beyond the Head level that
        defined the originating H&S pattern.

        A body-close beyond the Head means the market has completely reversed the
        move that produced the BOS, making the H&S thesis structurally invalid.
        Running the position to SL at that point books additional adverse excursion
        with no rational basis.

        The Head level is stored in the position's metadata under
        `invalidation_head` at signal-fire time (see the mk.level call in on_bar).
        If the metadata is absent (position from an older engine version), the hook
        silently no-ops so existing trades are not affected.
        """
        from backend.strategies.base_strategy import TradeAction

        if not getattr(self.params, "hard_invalidation_exit", True):
            return None

        meta = position.get("metadata") or position.get("original_signal", {}).get("metadata") or {}
        head_level = meta.get("invalidation_head")
        if head_level is None:
            # Try the markings list — the level is stored as a marking with
            # label "Head (invalidation)" and a price_level attribute.
            for marking in meta.get("markings", []):
                if marking.get("label") == "Head (invalidation)":
                    head_level = marking.get("price_level")
                    break
        if head_level is None:
            return None

        direction = position.get("direction", "")
        if not candles.empty:
            latest = candles.iloc[-1]
            body_top = max(float(latest["open"]), float(latest["close"]))
            body_bottom = min(float(latest["open"]), float(latest["close"]))
        else:
            return None

        if direction == "SELL" and body_bottom > float(head_level):
            # Body closed above the Head of a bearish H&S — thesis invalid.
            self.log_event(
                f"[{symbol}] APA hard-invalidation exit: SELL position, body closed "
                f"above Head {head_level:.5f} (body bottom {body_bottom:.5f})",
                category="APA",
            )
            return TradeAction(
                ticket=position.get("ticket", 0),
                action="CLOSE",
                close_reason="APA_HEAD_INVALIDATION",
            )

        if direction == "BUY" and body_top < float(head_level):
            # Body closed below the Head of a bullish H&S — thesis invalid.
            self.log_event(
                f"[{symbol}] APA hard-invalidation exit: BUY position, body closed "
                f"below Head {head_level:.5f} (body top {body_top:.5f})",
                category="APA",
            )
            return TradeAction(
                ticket=position.get("ticket", 0),
                action="CLOSE",
                close_reason="APA_HEAD_INVALIDATION",
            )

        return None
