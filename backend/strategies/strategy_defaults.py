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
        # [18.3] Measured best fixed R:R — research/16.
        # 1:2 best ($-46,699; degrades to $-64,059 at 1:5), n=4,978.
        "tp1_rr": 2.0,
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
        # [18.3] Measured best fixed R:R — research/16.
        # 1:5 $+78,849 vs $+28,881 at 1:2 — monotonic in RR, n=1,595.
        "tp1_rr": 5.0,
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
        # [18.3] Measured best fixed R:R — research/16.
        # 1:4 best ($-45,994 vs $-54,794 at 1:3), n=7,604.
        "tp1_rr": 4.0,
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
        # [18.3] Measured best fixed R:R — research/16.
        # 1:3 best ($-7,480; 1:5 is $-18,943), n=2,757.
        "tp1_rr": 3.0,
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
        # [18.3] Measured best fixed R:R — research/16.
        # 1:5 least-bad ($-46,302 vs $-56,569 at 1:3), n=3,372.
        "tp1_rr": 5.0,
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
        # [18.3] Measured best fixed R:R — research/16.
        # 1:4 the only profitable setting ($+540), n=458.
        "tp1_rr": 4.0,
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
        # [18.3] Measured best fixed R:R — research/16.
        # 1:3 best of four ($-3,689 vs $-12,116 at 1:2), n=3,225.
        "tp1_rr": 3.0,
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



# ── Per-SLOT measured defaults (symbol x strategy) ───────────────────────
#
# [18.3] The strategy-level tp1_rr above is the best single value across every
# symbol that strategy trades. Research/16 showed the optimum is materially
# symbol-specific on top of that: DriftJumpAlpha wants 1:5 on Crash 1000 but
# 1:3 on Crash 300, and NYOpenRetest wants 1:5 on the two Nasdaq feeds while
# 1:2 is right for it everywhere else.
#
# Only cells with n >= 30 trades AND a profitable best setting are listed —
# a cell that loses at every R:R has no "best" worth shipping, and a cell on
# 12 trades cannot support one. Everything absent falls back to the strategy
# default, then to RiskParams.
#
# Keyed "SYMBOL|strategy_id", matching MultiTPManager.slot_key(). Symbol names
# differ per broker (US Tech 100 on Deriv is NDX100 on FundedNext), so both
# appear where both were measured.
#
# Source: research/16-full-window-backtest.md — 285 cells, 23,989 trades,
# uniform 238-242 day window per asset.
SLOT_TP1_RR: dict[str, float] = {
    # DriftJumpAlpha — the only strategy profitable in aggregate
    "CRASH 1000 INDEX|DriftJumpAlpha_v1": 5.0,   # +$80,262  n=514  DD 37.0%
    "CRASH 300 INDEX|DriftJumpAlpha_v1": 3.0,    #  +$4,517  n=554  DD 36.1%

    # VWAP
    "VOLATILITY 75 INDEX|VWAP_v1": 5.0,          # +$15,328  n=240  DD 16.4%
    "GERMANY 40|VWAP_v1": 4.0,                   #  +$5,766  n=167  DD 16.9%
    "GER30|VWAP_v1": 4.0,                        #  FundedNext name for the above
    "XAGUSD|VWAP_v1": 4.0,                       #  +$4,088  n=169  DD 30.0%
    "XAUUSD|VWAP_v1": 5.0,                       #  +$4,077  n=169  DD 13.3%

    # CRT
    "CRASH 500 INDEX|CRT_v1": 5.0,               # +$11,869  n=220  DD 16.7%

    # NYOpenRetest — 1:5 on the Nasdaq feeds, against its 1:2 strategy default
    "US TECH 100|NYOpenRetest_v1": 5.0,          #  +$8,944  n=133  DD 10.6%
    "NDX100|NYOpenRetest_v1": 5.0,               #  +$7,862  n=109  DD 10.8%

    # BiasIFVG — best risk-adjusted cell in the study is USOUSD (Sharpe 6.58)
    "USOUSD|BiasIFVG_v1": 5.0,                   #  +$8,673  n=57   DD 7.7%
    "ETHUSD|BiasIFVG_v1": 5.0,                   #  +$6,904  n=75   DD 11.1%
    "UKOUSD|BiasIFVG_v1": 5.0,                   #  +$5,093  n=53   DD 6.0%

    # APA
    "VOLATILITY 75 INDEX|APA_v1": 4.0,           #  +$5,016  n=113  DD 18.6%
    "XRPUSD|APA_v1": 5.0,                        #  +$4,988  n=116  DD 22.5%
    "BTCUSD|APA_v1": 5.0,                        #  +$4,370  n=85   DD 18.2%
    "CRASH 500 INDEX|APA_v1": 3.0,               #  +$4,310  n=104  DD 6.4%
}


def get_slot_tp1_rr_defaults() -> dict[str, float]:
    """Measured per-symbol R:R defaults, ready to merge into a risk_config.

    Consumed by both the live path (bot_service) and the backtest routes, so a
    measured target is the target actually traded. A user override always wins:
    these are seeded first and anything explicit is applied on top.
    """
    return dict(SLOT_TP1_RR)

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
