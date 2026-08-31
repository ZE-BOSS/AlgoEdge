"""
backend/strategies/strategy_defaults.py

Per-strategy exit-management and session defaults, derived from measurement.

WHY THIS EXISTS
---------------
Trailing, break-even and session gating were **global** RiskParams applied
identically to all seven strategies. The Phase 3 study showed that is wrong in
both directions:

  * The 15-cell trailing sweep improved 10 cells and made 5 WORSE. Almost the
    entire book-level gain came from ONE strategy (NYOpenRetest, +1,765 PnL);
    DriftJumpAlpha, VWAP and CRT netted slightly negative. A single global
    trailing setting cannot be right for both.
  * The 43-cell session ablation ranged from **-0.170** (HTFFVGFlip — the gate
    is actively harmful) to **+0.126** (BiasIFVG — the best gate in the study).
    Mean contribution across the book was -0.010, i.e. nothing.

So the correct unit is the strategy, not the account. This module holds the
recommended defaults per strategy; RiskParams keeps the genuinely global
concerns (position sizing, drawdown caps, concurrency).

RESOLUTION ORDER
----------------
    RiskParams defaults  ->  STRATEGY_DEFAULTS[strategy_id]  ->  user overrides

A user override always wins. These are defaults, not constraints — the point is
that the shipped configuration is the measured-best one, so the parameters do
not have to be touched to get the recommended behaviour.

EVIDENCE
--------
Every value below traces to a measured result; `evidence` records which.
Sources: debug/trailing/trailing_sweep.csv (15 cells x 9 configs),
debug/ablation_session/ (43 cells), debug/ablation/ (46-cell recording sweep).
See implementation/PHASE-3-CONFLUENCE-RESEARCH.md.
"""

from __future__ import annotations

from typing import Any

# Fields a strategy is allowed to override. Anything outside this set stays
# global — position sizing and drawdown caps are account-level concerns and a
# strategy has no business changing them.
OVERRIDABLE: frozenset[str] = frozenset({
    # Trailing
    "trail_method_tp1", "trail_method_tp2", "trail_method_tp3",
    "trail_method_tp4", "trail_method_tp5",
    "trail_mode", "trail_trigger_rr", "trail_activation_rr",
    "trail_trigger_tp_level", "atr_trail_multiplier", "atr_trail_multiplier_tp1",
    "trail_pips", "trail_require_be_first",
    # Break-even
    "be_mode", "be_trigger_rr", "be_trigger_tp_level",
    "be_buffer_pips", "be_buffer_atr_mult", "be_spread_multiple",
    # Targets
    "tp_count", "tp1_rr", "tp2_rr", "tp3_rr", "min_rr",
    # Stop floor (per asset class is resolved separately in position_sizer)
    "min_sl_pips",
})


STRATEGY_DEFAULTS: dict[str, dict[str, Any]] = {

    # ── NYOpenRetest — the one strategy trailing clearly helps ────────────
    #
    # Best setup quality in the study (P(2R) 34.7%, median MFE 2.92R) and the
    # worst realised result in the saved book (-11,695). That gap was always
    # exit management, and the sweep confirms it: +1,765 PnL and +15.5pp win
    # rate from trailing alone — more than the whole book's improvement.
    # GBPJPY alone went 2.6% -> 25.6% win rate, +1,353 PnL.
    "NYOpenRetest_v1": {
        "trail_method_tp1": "ATR_TRAIL",
        "trail_mode": "RR",
        "trail_trigger_rr": 1.5,
        "trail_activation_rr": 1.5,
        "atr_trail_multiplier": 2.5,
        "atr_trail_multiplier_tp1": 2.5,
        "trail_require_be_first": False,
        "be_mode": "RR",
        "be_trigger_rr": 1.0,
        "evidence": (
            "trailing_sweep: +1,765 PnL / +15.5pp WR across 4 cells (best in book). "
            "session ablation -0.009 (noise) so the session gate is left as-is."
        ),
    },

    # ── DriftJumpAlpha — trailing HURTS; leave exits alone ────────────────
    #
    # Net +4 PnL from trailing across 3 cells, but that hides Crash 1000 Index
    # at -1,299 with full-TP exits collapsing 9 -> 0. Win rate rose 16.6pp
    # while money went nowhere: trailing converts its large winners into small
    # ones. This is the clearest "do not evaluate on win rate" case in the study.
    #
    # Its binding constraint is daily_trade_cap (blocks 77.9%), a risk control
    # rather than a market filter — deliberately left in place.
    "DriftJumpAlpha_v1": {
        "trail_method_tp1": "NONE",
        "trail_mode": "NONE",
        "be_mode": "TP_HIT",
        "evidence": (
            "trailing_sweep: +4 PnL over 3 cells, but Crash 1000 -1,299 with TP exits "
            "9->0. Trailing truncates its winners. Only profitable strategy in the book "
            "(+4,886) — do not touch its exits."
        ),
    },

    # ── VWAP — keep the session gate, skip trailing ───────────────────────
    #
    # Session ablation +0.064 across 9 cells (6/9 positive): removing the gate
    # yields ~94% more signals that are materially worse. Trailing was -4 PnL
    # net across 6 cells, with Hong Kong 50 losing 542 while its win rate rose.
    "VWAP_v1": {
        "trail_method_tp1": "NONE",
        "trail_mode": "NONE",
        "be_mode": "EITHER",
        "be_trigger_rr": 1.5,
        "evidence": (
            "session ablation +0.064 (keep gate). trailing_sweep -4 PnL over 6 cells; "
            "Hong Kong 50 -542 on a HIGHER win rate."
        ),
    },

    # ── BiasIFVG — best session gate in the study ─────────────────────────
    #
    # +0.126 contribution, the highest measured. Removing it would give 5x the
    # signals (87 -> 435) and worse expectancy — so the low trade count is the
    # price of the edge, not a defect to tune away.
    "BiasIFVG_v1": {
        "trail_method_tp1": "NONE",
        "trail_mode": "NONE",
        "be_mode": "EITHER",
        "be_trigger_rr": 1.5,
        "evidence": (
            "session ablation +0.126 — highest in the study. Keep the gate and accept "
            "the low frequency. Trailing untested on this strategy (43 signals)."
        ),
    },

    # ── CRT — loosen the session gate ─────────────────────────────────────
    #
    # session_filter discards 155,455 of 176,155 candidates (88.2%) for a
    # measured contribution of +0.009 — nothing. Removing it triples the sample
    # (186 -> 571 signals) at essentially unchanged expectancy, and CRT cannot
    # be evaluated at 19 trades per symbol.
    "CRT_v1": {
        "session_filter_enabled": False,
        "trail_method_tp1": "NONE",
        "trail_mode": "NONE",
        "be_mode": "TP_HIT",
        "evidence": (
            "session ablation +0.009 while blocking 88.2% of candidates — the most "
            "expensive no-op in the codebase. Disabled to restore sample size. "
            "trailing_sweep -11 PnL over 2 cells."
        ),
    },

    # ── HTFFVGFlip — session gate is actively harmful ─────────────────────
    #
    # -0.170 contribution, positive in only 2 of 9 cells. Removing it gives
    # 121% MORE signals AND better expectancy — it costs sample size and
    # quality simultaneously. The clearest single verdict in the study, and the
    # opposite of what its 89.5% block rate suggests.
    "HTFFVGFlip_v1": {
        "session_filter_enabled": False,
        "trail_method_tp1": "NONE",
        "trail_mode": "NONE",
        "be_mode": "EITHER",
        "evidence": (
            "session ablation -0.170 (2/9 cells positive) — removing the gate improves "
            "BOTH signal count (+121%) and expectancy. Actively harmful."
        ),
    },

    # ── APA — was structurally unable to trade ────────────────────────────
    #
    # rejection_candle passed 0 of 1,388 evaluations because require_retest
    # defaults False (so the AWAIT_RETEST branch that sets retest_rejected never
    # runs) while require_rejection_candle defaults True. Fixed in the engine by
    # evaluating rejection independently of the retest state.
    "APA_v1": {
        "trail_method_tp1": "NONE",
        "trail_mode": "NONE",
        "be_mode": "EITHER",
        "evidence": (
            "0 signals from 78,800 candidates until the require_retest / "
            "require_rejection_candle interlock was fixed. No exit data yet — "
            "re-measure before changing exits."
        ),
    },
}


def get_strategy_defaults(strategy_id: str) -> dict[str, Any]:
    """
    Recommended risk/exit overrides for a strategy, minus the `evidence` note.

    Returns {} for an unknown strategy, so callers always get the plain global
    defaults rather than an error.
    """
    raw = STRATEGY_DEFAULTS.get(strategy_id) or {}
    return {k: v for k, v in raw.items() if k != "evidence"}


def get_strategy_evidence(strategy_id: str) -> str:
    """The measured justification, for display in the UI and in run logs."""
    return (STRATEGY_DEFAULTS.get(strategy_id) or {}).get("evidence", "")


def merge_strategy_defaults(
    strategy_id: str,
    risk_config: dict[str, Any],
    user_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Apply strategy defaults on top of the global risk config.

    Order: global -> strategy default -> explicit user override.

    `user_overrides` should contain only fields the user actually set. A caller
    that cannot distinguish "user set this to the default value" from "user did
    not set it" should pass None and let the strategy default apply — that is
    the whole point of shipping measured defaults.
    """
    merged = dict(risk_config or {})
    for key, value in get_strategy_defaults(strategy_id).items():
        # Session enablement lives on the strategy params object, not RiskParams;
        # it is carried here so one call site can resolve everything.
        merged[key] = value
    for key, value in (user_overrides or {}).items():
        if value is not None:
            merged[key] = value
    return merged
