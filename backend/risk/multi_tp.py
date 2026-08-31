"""
backend/risk/multi_tp.py

Multi-position TP1/TP2/TP3/TP4/TP5 orchestration.
Source: RiskManagement_Spec.md Section 2

All TP positions open at entry. No deferred stacking.
When TP1 hits, all remaining positions move to break-even.
User configures tp_count (1-5) and RR per level.
"""

import math
from dataclasses import dataclass
from typing import Any

from backend.risk.position_sizer import calculate_risk_dollars, get_symbol_info
from backend.utils.logger import get_logger

logger = get_logger(__name__)

# Direction normalization: callers may use "BUY"/"SELL" or "BULLISH"/"BEARISH"
_BUY_DIRECTIONS = {"BUY", "BULLISH"}
_SELL_DIRECTIONS = {"SELL", "BEARISH"}


def _is_buy(direction: str) -> bool:
    return direction.upper() in _BUY_DIRECTIONS


def _is_sell(direction: str) -> bool:
    return direction.upper() in _SELL_DIRECTIONS


def slot_overrides_from_config(instrument_slots) -> dict[str, dict]:
    """[17.1] Build the per-slot TP override maps from InstrumentSlot entries.

    Returns the two keys that `MultiTPManager` reads, ready to merge into a
    risk_config dict:

        {"tp1_rr_overrides_by_slot": {...}, "tp_count_overrides_by_slot": {...}}

    Shared by the live path (bot_service) and the backtest routes so both
    resolve a symbol's R:R identically — the parity that matters most here is
    that a backtested target and a live target come from the same code.
    Slots with `tp1_rr=None` are skipped, preserving "None = inherit".
    """
    rr: dict[str, float] = {}
    counts: dict[str, int] = {}
    for slot in (instrument_slots or []):
        sym = getattr(slot, "symbol", "") or ""
        sid = getattr(slot, "strategy_id", "") or ""
        if not sym:
            continue
        key = f"{sym.upper()}|{sid}"
        if getattr(slot, "tp1_rr", None) is not None:
            rr[key] = float(slot.tp1_rr)
        if getattr(slot, "tp_count", None) is not None:
            counts[key] = int(slot.tp_count)
    return {"tp1_rr_overrides_by_slot": rr,
            "tp_count_overrides_by_slot": counts}


@dataclass
class TPLevel:
    level: int          # 1, 2, 3, 4, or 5
    rr_multiplier: float
    volume_pct: float   # percentage of total volume (0.0–1.0)
    tp_price: float
    volume: float
    trail_method: str | None  # None for TP1, ATR_TRAIL for TP2, etc.
    deferred: bool = False       # Always False — all TPs open at entry


class MultiTPManager:
    """Calculates TP levels and splits volume across sub-positions."""

    def __init__(self, config: dict[str, Any]):
        self.tp1_rr = config.get("tp1_rr", 1.0)
        self.tp2_rr = config.get("tp2_rr", 3.0)
        self.tp3_rr = config.get("tp3_rr", 5.0)
        self.tp4_rr = config.get("tp4_rr", 10.0)
        self.tp5_rr = config.get("tp5_rr", 15.0)
        # [5.1/5.2/Part4] tp_volume_pcts is the explicit, typed replacement for
        # tp_splits (which accepted a comma-separated string — an odd shape
        # for a numeric list, kept only for back-compat). tp_volume_pcts wins
        # when set; tp_splits (string or list) remains the fallback source so
        # nothing that already configures tp_splits breaks.
        raw_volume_pcts = config.get("tp_volume_pcts")
        if isinstance(raw_volume_pcts, list) and raw_volume_pcts:
            self.tp_splits = [float(x) for x in raw_volume_pcts]
        else:
            raw_splits = config.get("tp_splits", [40, 30, 20, 5, 5])
            if isinstance(raw_splits, str):
                try:
                    self.tp_splits = [float(x.strip()) for x in raw_splits.split(",") if x.strip()]
                except ValueError:
                    self.tp_splits = [40, 30, 20, 5, 5]
            elif isinstance(raw_splits, list):
                self.tp_splits = [float(x) for x in raw_splits]
            else:
                self.tp_splits = [40, 30, 20, 5, 5]
        self.tp_count = config.get("tp_count", 3)  # User-configurable: how many TPs (1–5)
        self.min_rr = config.get("min_rr", 3.0)
        self.multi_position_mode = config.get("multi_position_mode", True)
        self.prop_firm_config = config.get("prop_firm", {})

        # [2.15] Per-strategy TP1 RR override, resolved through normal param
        # lookup instead of a strategy_id=="DriftJumpAlpha" branch hardcoded
        # into calculate_tp_levels. MultiTPManager is a single shared instance
        # across every strategy in a portfolio run, so this must be a
        # {strategy_id: rr} dict, not one flat value — a flat override would
        # silently apply to every strategy sharing this instance, not just the
        # one that asked for it. Populated from each strategy's own
        # tp1_rr_override param field (e.g. DriftJumpAlphaParams.tp1_rr_override)
        # by the caller assembling risk_config (backtest.py routes).
        self.tp1_rr_overrides_by_strategy: dict[str, float] = config.get("tp1_rr_overrides_by_strategy", {}) or {}

        # [17.1] Per-SLOT TP1 R:R — keyed "SYMBOL|strategy_id", so the same
        # symbol running two strategies can carry two different targets, and the
        # same strategy can use a different target per symbol. Strictly more
        # specific than tp1_rr_overrides_by_strategy above and takes priority.
        # Populated from InstrumentSlot.tp1_rr (live) and from the per-symbol
        # backtest request (backtest), so both paths resolve identically.
        self.tp1_rr_overrides_by_slot: dict[str, float] = config.get("tp1_rr_overrides_by_slot", {}) or {}
        self.tp_count_overrides_by_slot: dict[str, int] = config.get("tp_count_overrides_by_slot", {}) or {}


        # [2.13] Diagnostics from the most recent calculate_tp_levels() call —
        # requested vs. actually placed TP count, so a caller can tell "the
        # strategy asked for 3 TPs but only 2 fit the risk cap" apart from a
        # signal that was simply rejected outright.
        self.last_tp_levels_requested: int = 0
        self.last_tp_levels_placed: int = 0

        # Trail methods per TP level (all configurable)
        # [5.1/5.2/F2] trail_method_tp1 was hardcoded None — with tp_count=1
        # this made trailing structurally impossible no matter what was
        # configured. Now reads the real (default "NONE") field; downstream
        # TrailingManager.calculate_trailing_sl's else-branch already treats
        # any unrecognised method string (including "NONE") as "no trail",
        # same convention trail_method_tp2-5 already rely on.
        self.trail_methods = [
            config.get("trail_method_tp1", "NONE"),            # TP1
            config.get("trail_method_tp2", "ATR_TRAIL"),       # TP2
            config.get("trail_method_tp3", "STRUCTURE_TRAIL"), # TP3
            config.get("trail_method_tp4", "ATR_TRAIL"),       # TP4
            config.get("trail_method_tp5", "STRUCTURE_TRAIL"), # TP5
        ]

        # Diagnostic set by calculate_tp_levels() when a risk-cap overshoot survives
        # scale-down purely because of lot_min flooring (not a bad SL distance).
        # Callers (engine.py) can read this immediately after calling
        # calculate_tp_levels() to produce a clearer rejection reason. Reset on every call.
        self._last_overshoot_reason: str | None = None

    @staticmethod
    def slot_key(symbol: str, strategy_id: str) -> str:
        """Canonical key for the per-slot override maps."""
        return f"{(symbol or '').upper()}|{strategy_id or ''}"

    def resolve_tp1_rr(self, symbol: str, strategy_id: str) -> float:
        """TP1 R:R for this symbol+strategy pair, most specific override first."""
        key = self.slot_key(symbol, strategy_id)
        if key in self.tp1_rr_overrides_by_slot:
            return float(self.tp1_rr_overrides_by_slot[key])
        return float(self.tp1_rr_overrides_by_strategy.get(strategy_id, self.tp1_rr))

    def resolve_tp_count(self, symbol: str, strategy_id: str) -> int:
        """TP count for this symbol+strategy pair, per-slot override first."""
        key = self.slot_key(symbol, strategy_id)
        if key in self.tp_count_overrides_by_slot:
            return int(self.tp_count_overrides_by_slot[key])
        return int(self.tp_count)

    def calculate_tp_levels(
        self,
        entry: float,
        sl: float,
        direction: str,
        total_volume: float,
        symbol: str,
        liquidity_target: float | None = None,
        strategy_id: str = "UNKNOWN",
        max_risk_cap_dollars: float = 0.0,  # 2% of balance; 0 = no cap
        use_live_mt5: bool = True,
    ) -> list[TPLevel]:
        """
        Calculates prices and volumes for up to 5 TP levels.
        Direction accepts both conventions: "BUY"/"SELL" or "BULLISH"/"BEARISH".
        If max_risk_cap_dollars > 0, enforces it by:
          1. Reducing TP count (drops last TPs first)
          2. Then scaling remaining volumes proportionally
        """
        self._last_overshoot_reason = None

        risk = abs(entry - sl)
        if risk == 0:
            logger.warning("Risk is zero (entry == SL) — cannot calculate TP levels")
            return []

        # Determine direction sign: +1 for BUY/BULLISH, -1 for SELL/BEARISH
        if _is_buy(direction):
            sign = 1
        elif _is_sell(direction):
            sign = -1
        else:
            logger.error(f"Unknown direction '{direction}' — cannot calculate TPs")
            return []

        # [2.15] Per-strategy TP1 RR override (e.g. DriftJumpAlpha's crash-spike
        # setups want a much tighter TP1 than the global default), resolved
        # through normal per-strategy param lookup rather than a hardcoded
        # strategy_id branch.
        # [17.1] Resolution is now slot -> strategy -> global, so a per-symbol
        # target wins over a per-strategy one. `symbol` was already a parameter
        # here; only the lookup changed.
        tp1_rr_used = self.resolve_tp1_rr(symbol, strategy_id)

        rr_multipliers = [tp1_rr_used, self.tp2_rr, self.tp3_rr, self.tp4_rr, self.tp5_rr]
        tp_prices = [entry + (risk * rr * sign) for rr in rr_multipliers]

        # TP5 can optionally anchor to next liquidity pool
        if liquidity_target is not None:
            tp_prices[4] = liquidity_target

        # How many TPs the user wants (clamped 1–5)
        active_count = max(1, min(self.resolve_tp_count(symbol, strategy_id), 5))
        # [2.13] default; overwritten on every success path below, left as-is
        # (0 placed) if the function returns [] for any reason.
        self.last_tp_levels_requested = active_count
        self.last_tp_levels_placed = 0

        if not self.multi_position_mode:
            # Single position mode — use only TP1
            tp = TPLevel(
                level=1,
                rr_multiplier=tp1_rr_used,
                volume_pct=1.0,
                tp_price=tp_prices[0],
                volume=total_volume,
                # [T2.3] Was hardcoded `None`, which made trailing structurally
                # impossible on this path no matter what the user configured —
                # the Phase 5 fix only reached the `active_count == 1` branch
                # below, not this one. Both single-TP paths must read the same
                # configured method or the two disagree depending on
                # multi_position_mode.
                trail_method=self.trail_methods[0] if self.trail_methods else None,
                deferred=False,
            )
            if not self._validate_tp(tp, entry, direction):
                return []
            self.last_tp_levels_placed = 1
            return [tp]

        # [2.14] tp_count == 1 has nothing to split — skip the whole
        # lot_min-flooring/remainder-sweep machinery below (which exists purely
        # to keep EVERY sub-position at or above the broker's minimum lot when
        # dividing volume across multiple legs) and just floor the full
        # requested volume to the lot step, matching the multi_position_mode
        # branch above.
        if active_count == 1:
            info = get_symbol_info(symbol, use_live_mt5=use_live_mt5)
            lot_step = info.get("volume_step", 0.01)
            vol = math.floor(total_volume / lot_step) * lot_step if lot_step > 0 else total_volume
            tp = TPLevel(
                level=1,
                rr_multiplier=tp1_rr_used,
                volume_pct=1.0,
                tp_price=tp_prices[0],
                volume=round(vol, 4),
                trail_method=self.trail_methods[0] if self.trail_methods else None,
                deferred=False,
            )
            self.last_tp_levels_requested = 1
            if not self._validate_tp(tp, entry, direction):
                self.last_tp_levels_placed = 0
                return []
            self.last_tp_levels_placed = 1
            return [tp]

        # Build TP levels with volume splits — ALL immediate
        levels = []

        # Normalize splits to match active_count
        splits = self.tp_splits[:active_count]
        total_split = sum(splits)
        if total_split == 0:
            splits = [100 // active_count] * active_count
            total_split = sum(splits)

        # Get exact lot constraints for rounding
        info = get_symbol_info(symbol, use_live_mt5=use_live_mt5)
        lot_step = info.get("volume_step", 0.01)
        lot_min = info.get("volume_min", 0.01)

        # ── Dynamic TP Sizing with Minimum Lot Enforcement ──
        # Ensure every requested sub-position has at least lot_min.
        # This overrides volume collapse, potentially slightly increasing risk for very small accounts
        # but guarantees the requested number of TP levels are entered.
        max_lot_allowed = self.prop_firm_config.get("max_lot_sizes", {}).get(symbol, float('inf'))
        volumes = []
        for i in range(active_count):
            split_pct = splits[i] / total_split
            raw_vol = total_volume * split_pct
            vol = math.floor(raw_vol / lot_step) * lot_step
            # Clamp to minimum lot to prevent collapse
            vol = max(lot_min, round(vol, 4))
            # Clamp to max lot size per position
            vol = min(vol, max_lot_allowed)
            volumes.append(vol)

        # ── Remainder Sweep ──
        # Any volume lost to rounding is swept into TP1 (if it fits the lot_step and max lot size)
        allocated_vol = round(sum(volumes), 4)
        remainder = round(total_volume - allocated_vol, 4)
        if remainder >= lot_step:
            sweep_amount = math.floor(remainder / lot_step) * lot_step
            new_tp1_vol = round(volumes[0] + sweep_amount, 4)
            volumes[0] = min(new_tp1_vol, max_lot_allowed)

        levels = []
        for i in range(active_count):
            split_pct = splits[i] / total_split
            trail = self.trail_methods[i] if i < len(self.trail_methods) else None
            levels.append(TPLevel(
                level=i + 1,
                rr_multiplier=rr_multipliers[i],
                volume_pct=split_pct,
                tp_price=tp_prices[i],
                volume=volumes[i],
                trail_method=trail,
                deferred=False,  # ALL TPs open at entry
            ))

        # Sanity validation — catch direction bugs at the source
        for tp in levels:
            if not self._validate_tp(tp, entry, direction):
                return []

        # ── 2% Risk Cap Enforcement ──────────────────────────────────────────────────
        # After lot_min enforcement, the actual total volume may exceed what the
        # position sizer intended. Enforce the hard 2% cap here.
        if max_risk_cap_dollars > 0 and levels:
            actual_total_risk = calculate_risk_dollars(
                sum(tp.volume for tp in levels), entry, sl, symbol, use_live_mt5=use_live_mt5
            )
            if actual_total_risk > max_risk_cap_dollars:
                logger.warning(
                    f"[MultiTP] {symbol}: lot_min enforcement inflated risk to "
                    f"${actual_total_risk:.2f} (cap=${max_risk_cap_dollars:.2f}). "
                    f"Reducing TP count first..."
                )

                # Step 1: Reduce TP count (drop last TPs one by one). [2.13] Before
                # accepting a surviving subset at its ORIGINAL (smaller) split
                # volumes, first try reallocating the FULL requested budget across
                # just those surviving levels — dropping TP3 shouldn't mean TP1/TP2
                # stay pinned at their original 50%/30% split-of-100% when the
                # account could deploy the whole 80% (or more) across the two of
                # them and still respect the risk cap.
                while len(levels) > 1:
                    levels = levels[:-1]  # drop last TP
                    n_remaining = len(levels)
                    remaining_weight = sum(splits[:n_remaining])
                    if remaining_weight > 0:
                        reallocated = []
                        for i, tp in enumerate(levels):
                            w = splits[i] / remaining_weight
                            rv = total_volume * w
                            rv = math.floor(rv / lot_step) * lot_step if lot_step > 0 else rv
                            rv = min(max(lot_min, round(rv, 4)), max_lot_allowed)
                            reallocated.append(rv)
                        reallocated_risk = calculate_risk_dollars(
                            sum(reallocated), entry, sl, symbol, use_live_mt5=use_live_mt5
                        )
                        if reallocated_risk <= max_risk_cap_dollars:
                            for tp, rv in zip(levels, reallocated):
                                tp.volume = rv
                            actual_total_risk = reallocated_risk
                            logger.info(
                                f"[MultiTP] {symbol}: Risk capped by reallocating the full budget "
                                f"to {n_remaining} TP(s). actual_risk=${actual_total_risk:.2f}"
                            )
                            break

                    actual_total_risk = calculate_risk_dollars(
                        sum(tp.volume for tp in levels), entry, sl, symbol, use_live_mt5=use_live_mt5
                    )
                    if actual_total_risk <= max_risk_cap_dollars:
                        logger.info(
                            f"[MultiTP] {symbol}: Risk capped by reducing to "
                            f"{len(levels)} TP(s). actual_risk=${actual_total_risk:.2f}"
                        )
                        break

                # Step 2: If still over cap after keeping only TP1, scale volumes down
                actual_total_risk = calculate_risk_dollars(
                    sum(tp.volume for tp in levels), entry, sl, symbol, use_live_mt5=use_live_mt5
                )
                if actual_total_risk > max_risk_cap_dollars:
                    scale_factor = max_risk_cap_dollars / actual_total_risk
                    # Track whether lot_min flooring is the reason we can't hit the cap:
                    # if the mathematically-scaled volume (before flooring) is already
                    # below the broker's minimum lot, there is no volume that both
                    # respects lot_min AND stays within the risk cap — this is a
                    # small-account/SL-distance limitation, not a bad SL distance.
                    floored_below_min = False
                    for tp in levels:
                        scaled = tp.volume * scale_factor
                        if scaled < lot_min:
                            floored_below_min = True
                        scaled = max(lot_min, math.floor(scaled / lot_step) * lot_step)
                        scaled = min(scaled, max_lot_allowed)
                        tp.volume = round(scaled, 4)
                    final_risk = calculate_risk_dollars(
                        sum(tp.volume for tp in levels), entry, sl, symbol, use_live_mt5=use_live_mt5
                    )
                    logger.warning(
                        f"[MultiTP] {symbol}: After TP count reduction still over cap. "
                        f"Scaled volumes by {scale_factor:.3f}. Final risk=${final_risk:.2f}"
                    )
                    # If lot_min flooring is what kept us over cap, surface a distinct,
                    # actionable reason instead of letting this fall through to the
                    # generic "post_split_risk_overshoot" rejection in engine.py, which
                    # otherwise reads as a bad-SL-distance bug rather than an
                    # account-size limitation.
                    if floored_below_min and final_risk > max_risk_cap_dollars:
                        self._last_overshoot_reason = "account_too_small_for_sl_distance"

        logger.debug(
            f"TP levels: {len(levels)} | dir={direction} | entry={entry} | "
            f"risk={abs(entry-sl):.5f} | tp_count={len(levels)}"
        )

        # [2.13] requested vs. placed — lets a caller distinguish "the risk cap
        # forced fewer TPs than the strategy/config asked for" from a signal
        # that was simply rejected outright.
        self.last_tp_levels_requested = active_count
        self.last_tp_levels_placed = len(levels)

        return levels

    def _validate_tp(self, tp: TPLevel, entry: float, direction: str) -> bool:
        """
        Post-calculation sanity check: TP must be on the correct side of entry.
        Returns False and logs ERROR if a TP is placed on the wrong side.
        """
        if _is_buy(direction) and tp.tp_price <= entry:
            logger.error(
                f"CRITICAL: BUY TP{tp.level} ({tp.tp_price:.5f}) is at or below "
                f"entry ({entry:.5f}). This would guarantee a loss."
            )
            return False
        if _is_sell(direction) and tp.tp_price >= entry:
            logger.error(
                f"CRITICAL: SELL TP{tp.level} ({tp.tp_price:.5f}) is at or above "
                f"entry ({entry:.5f}). This would guarantee a loss."
            )
            return False
        return True
