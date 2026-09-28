"""
backend/strategies/strategy_defaults.py

Per-strategy exit-management and session defaults, derived from measurement.

WHY THIS EXISTS
---------------
Trailing, break-even and session gating were **global** RiskParams applied
identically to every strategy. The Phase 3 study showed that is wrong in both
directions: the 15-cell trailing sweep improved 10 cells and made 5 WORSE, and
the 43-cell session ablation ranged from -0.170 to +0.126 depending on the
strategy. So the correct unit is the strategy, not the account. This module
holds the recommended defaults per strategy; RiskParams keeps the genuinely
global concerns (position sizing, drawdown caps, concurrency).

RESOLUTION ORDER
----------------
    RiskParams defaults  ->  STRATEGY_DEFAULTS[strategy_id]  ->  user overrides

A user override always wins. These are defaults, not constraints — the point is
that the shipped configuration is the measured-best one, so the parameters do
not have to be touched to get the recommended behaviour.

2026-09-11: CRT, HTFFVGFlip, BiasIFVG, NYOpenRetest and the five synthetic
template strategies (SpikeFade, SpikeRide, RangeRevert, RangeBreakout,
TrendDrift) were removed from the book, and their defaults with them.

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


# Strategies whose MEASURED rule has no profit target at all: they leave on a
# trailing stop, a session close or a holding period. The engine always needs a
# take-profit price, so their `tp1_rr` below is a PLACEHOLDER put far beyond
# anything observed (the best single trade in five years of TrendBreakout_v1 was
# +5.9R) so that it never fills.
#
# This set exists so the fact is machine-readable rather than folklore: the
# /strategy-factory/strategy-defaults endpoint publishes it, the slot editor warns
# when a user lowers the placeholder, and tests can hold these to a different
# range from a real measured R:R. Lowering it does not "add a target" — it
# truncates the right tail the rule lives on.
NO_MEASURED_TARGET: frozenset[str] = frozenset({
    "TrendBreakout_v1", "OvernightSession_v1", "OpeningDrive_v1",
})


STRATEGY_DEFAULTS: dict[str, dict[str, Any]] = {

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
        # 2026-09-13: one target, no break-even, no trailing — how the shipped
        # SESSION_TREND / SESSION_PULLBACK books were measured (edge_lab). The
        # old EITHER break-even at 1.5R turned 16 of 113 BTCUSD session-pullback
        # trades into BE_SL exits the research never took. The original
        # PULLBACK_TO_VALUE mode has no edge on any of 26 markets either way.
        "tp_count": 1,
        "tp1_rr": 4.0,
        "trail_method_tp1": "NONE",
        "trail_mode": "NONE",
        "be_mode": "NONE",
        "evidence": (
            "edge_lab 2026-09-13: session modes measured with one target, no BE, flat at the "
            "session close — XAUUSD SESSION_TREND +0.26R / +0.36R / +0.42R, BTCUSD SESSION_PULLBACK "
            "+0.30R / +0.37R / +0.13R (2022-23 / 2024-25 / 2026)."
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

    # ── Boom mirror of DriftJumpAlpha (research/25) ─────────────────────────
    # Measured with a SINGLE target, break-even OFF and trailing OFF. Break-even
    # is not neutral here: research/25 §4.1 measured it costing up to 0.154 R per
    # trade on Boom, and BE_SL exits giving up 2.08 R of mean favourable excursion.
    "BoomDriftJump_v1": {
        "tp_count": 1,
        "tp1_rr": 5.0,
        "be_mode": "NONE",
        "trail_method_tp1": "NONE",
        "evidence": "research/25 — Boom mirror of DJA; BE measured harmful (-0.154 R).",
    },

    # ── ORB — opening-range breakout (data/strategy_search, 2026-09-11) ─────
    # Every number was measured with ONE target, no break-even and no trailing,
    # plus the session-close exit the engine applies itself.
    "ORB_v1": {
        "tp_count": 1,
        "tp1_rr": 3.0,
        "be_mode": "NONE",
        "trail_method_tp1": "NONE",
        "trail_mode": "NONE",
        "evidence": (
            "edge_lab 2026-09-13: M5 break of the 60m range with the H1 trend, 1:3, ONE setting for "
            "US Tech 100 / XAUUSD / BTCUSD / GBPJPY — pooled 2022-23 +0.13R (t 3.3), 2024-25 +0.09R "
            "(t 3.0), 2026 +0.09R (PF 1.29), positive on all four in every window. Earlier M15 form: "
            "GBPJPY London 60m 1:3 +0.05R / +0.08R / +0.21R."
        ),
    },

    # ── IVW — implied-volatility-wall breakout (ivw study, 2026-09-19) ──────
    # Measured with ONE target one wall-width away (stop half a width: 1:2), no
    # break-even and no trailing, plus the day-end exit the engine applies itself.
    "IVW_v1": {
        "tp_count": 1,
        "tp1_rr": 2.0,
        # the RiskEngine's shipped min_rr of 3.0 would reject every 1:2 signal
        "min_rr": 2.0,
        "be_mode": "NONE",
        "trail_method_tp1": "NONE",
        "trail_mode": "NONE",
        "evidence": (
            "ivw study 2026-09-19, 16 markets M5 2013-2026: fading the walls lost almost everywhere; "
            "breakout of the 90th-percentile wall from a low cumulative-vol regime on a liquidation-"
            "bubble bar, chosen on 2013-19, was +0.152R (t 3.3, n 512) on 2020-26 and positive on "
            "10/10 markets with enough trades."
        ),
    },

    # ── The 2026-09-25 book: three rules whose exit IS the strategy ──────
    #
    # All three leave on something the STRATEGY decides (a chandelier trail, the
    # next cash open, the cash close) and none of them has a profit target in the
    # measured rule. The engine always needs one, so `tp1_rr` is a PLACEHOLDER set
    # far beyond anything observed — the best single trade in five years of the
    # trend system was +5.9R — so it never fills and the measured exit is the one
    # that books the trade. Lowering it does not "add a target", it truncates the
    # right tail these rules live on.
    #
    # `trail_mode`/`trail_method_tp1` are OFF on purpose: RiskParams' ATR_TRAIL
    # trails the STRATEGY TIMEFRAME's ATR from the extreme PRICE, while these
    # trail the DAILY ATR from the extreme CLOSE. On M15 those are two different
    # stops and only one of them was measured. See strategies/core/trail.py.
    "TrendBreakout_v1": {
        "tp_count": 1,
        "tp1_rr": 20.0,
        "be_mode": "NONE",
        "trail_method_tp1": "NONE",
        "trail_mode": "NONE",
        "evidence": (
            "app-form check 2026-09-25 (scripts/run_app_form_check.py), Deriv M15 2021-10 -> "
            "2026-09, 10 markets: 876 trades, +0.063R, 40.0% win, payoff 1.86, t +2.17, "
            "Sharpe 1.34, +32.6% on $10k at 0.5% risk, max DD 6.3%; 9/10 markets positive. "
            "Every earlier screen used a FIXED target and measured nothing — that was the "
            "error, not the market. NEGATIVE on FundedNext's own 16 months (-0.092R, t -1.75)."
        ),
    },
    "OvernightSession_v1": {
        "tp_count": 1,
        "tp1_rr": 20.0,
        "be_mode": "NONE",
        "trail_method_tp1": "NONE",
        "trail_mode": "NONE",
        "evidence": (
            "app-form check 2026-09-25, Deriv M5 2021-10 -> 2026-09, US Tech 100 / US SP 500 / "
            "Germany 40: 2,006 nights, +0.065R, 52.8% win, t +3.15, Sharpe 1.28, +80.1% at "
            "0.5% risk, max DD 21.7%. Stop swept 0.5/1/1.5/2/3 x daily ATR — 0.5 best. The "
            "ONLY strategy in this book still positive on FundedNext's bars (+0.056R, t +1.73)."
        ),
    },
    "OpeningDrive_v1": {
        "tp_count": 1,
        "tp1_rr": 20.0,
        "be_mode": "NONE",
        "trail_method_tp1": "NONE",
        "trail_mode": "NONE",
        "evidence": (
            "app-form check 2026-09-25: the marketed \"first 5-minute candle vs the 12 EMA\" "
            "rule. Its claims DO NOT reproduce — 49.8% win and 1.07 profit factor over 5,081 "
            "sessions against \"57% and 1.29\". What is left is small and index-only: pooled "
            "+0.012R (t +1.89), US Tech 100 +0.052R (t +2.49), BTCUSD -0.005R, every FX pair "
            "negative, and NEGATIVE on FundedNext (-0.008R). Run it on US Tech 100 or not at all."
        ),
    },

    # ── SpikeResumption — built to order, and it does not hold up ────────
    #
    # Measured with ONE target, no break-even and no trailing, plus the bar-count
    # exit the engine applies itself through MaxHoldExit. target="spike" (back
    # past the spike bar) and a fixed 3R measured the same, so the simpler one
    # ships.
    #
    # It is enabled on NOTHING by default and there are deliberately no entries
    # for it in SLOT_TP1_RR or SYNTH_SLOT_PARAMS: recommending a symbol would
    # mean a measurement supports one, and none does.
    "SpikeResumption_v1": {
        "tp_count": 1,
        "tp1_rr": 3.0,
        "be_mode": "NONE",
        "trail_method_tp1": "NONE",
        "trail_mode": "NONE",
        "evidence": (
            "NO RELIABLE EDGE — shipped so it can be run and judged, not because it passed. "
            "Implementation/SPIKE-RESUMPTION-2026-09-27.md: on Boom/Crash 1000 over 2021-10 -> "
            "2024-09, three years its parameters were never fitted to, it loses 0.098R a trade "
            "(-6.1%). Per instrument Jan 2026 to date at 0.5% risk: Crash 1000 +11.9% (PF 3.51) "
            "carries it while Boom 900 is the worst of eight at -5.4% (PF 0.38, 23.5% win). And "
            "the families SWAP SIGN between windows — Boom +0.335R / Crash -0.094R in-sample, "
            "Boom -0.382R / Crash +0.735R out-of-sample, on all eight parameter variants tried. "
            "Use EMPIRICAL stop fills or the numbers are fiction."
        ),
    },

    # ── Restored 2026-09-14 (removed 2026-09-11) ─────────────────────────────
    # Exit settings are the pre-removal measured ones; the 2026-09-14 confluence
    # study re-measures every one of them — see the evidence strings once updated.
    "HTFFVGFlip_v1": {
        "tp1_rr": 4.0,
        "session_filter_enabled": False,
        "trail_method_tp1": "NONE",
        "trail_mode": "NONE",
        "be_mode": "EITHER",
        "evidence": "research/16: 1:4 the only profitable fixed R:R; session gate ablation -0.170.",
    },
    "BiasIFVG_v1": {
        "tp1_rr": 3.0,
        "trail_method_tp1": "NONE",
        "trail_mode": "NONE",
        "be_mode": "EITHER",
        "be_trigger_rr": 1.5,
        "evidence": "research/16: 1:3 best fixed R:R; session gate ablation +0.126.",
    },
    "SpikeFade_v1": {
        "tp_count": 1, "tp1_rr": 5.0, "be_mode": "NONE", "trail_method_tp1": "NONE",
        "evidence": "research/26 — best on Range Break 100 (+77.7%, PF 1.25, DD 22.2%).",
    },
    "RangeRevert_v1": {
        "tp_count": 1, "tp1_rr": 5.0, "be_mode": "NONE", "trail_method_tp1": "NONE",
        "evidence": "research/26 — best on Vol 100 (+168.6%, PF 1.36) and Boom 500.",
    },
    "RangeBreakout_v1": {
        "tp_count": 1, "tp1_rr": 3.0, "be_mode": "NONE", "trail_method_tp1": "NONE",
        "evidence": "research/26 — best on Volatility 25 (+64.3%, PF 1.07, DD 27.9%).",
    },
    "TrendDrift_v1": {
        "tp_count": 1, "tp1_rr": 8.0, "be_mode": "NONE", "trail_method_tp1": "NONE",
        "evidence": "research/26 — best on Crash 1000 (+120.5%, PF 1.30, DD 20.0%).",
    },
}

SLOT_TP1_RR: dict[str, float] = {
    # DriftJumpAlpha — the only strategy profitable in aggregate
    "CRASH 1000 INDEX|DriftJumpAlpha_v1": 5.0,   # +$80,262  n=514  DD 37.0%
    "CRASH 300 INDEX|DriftJumpAlpha_v1": 3.0,    #  +$4,517  n=554  DD 36.1%

    # VWAP
    "VOLATILITY 75 INDEX|VWAP_v1": 5.0,          # +$15,328  n=240  DD 16.4%
    "GERMANY 40|VWAP_v1": 4.0,                   #  +$5,766  n=167  DD 16.9%
    "GER30|VWAP_v1": 4.0,                        #  FundedNext name for the above
    "XAGUSD|VWAP_v1": 4.0,                       #  +$4,088  n=169  DD 30.0%
    # "XAUUSD|VWAP_v1": 5.0,   REMOVED — IS +$2,991 but OOS -$144 (n=86)

    # APA
    "VOLATILITY 75 INDEX|APA_v1": 4.0,           #  +$5,016  n=113  DD 18.6%
    "XRPUSD|APA_v1": 5.0,                        #  +$4,988  n=116  DD 22.5%
    # "BTCUSD|APA_v1": 5.0,   REMOVED — IS +$5,292 but OOS -$923 (n=45)
    "CRASH 500 INDEX|APA_v1": 3.0,               #  +$4,310  n=104  DD 6.4%

    # ORB — chosen on 2024-01..2026-01, then held unchanged (avg R per trade)
    # ORB — 2026-09-13 edge lab: ONE shared setting (M5 break of the 60m range
    # WITH the H1 trend, 1:3), chosen on 2022-23 AND 2024-25, then 2026 unseen
    "GBPJPY|ORB_v1": 3.0,       # 2022-23 +0.09  2024-25 +0.12  2026 +0.23
    "BTCUSD|ORB_v1": 3.0,       # 2022-23 +0.25  2024-25 +0.11  2026 +0.01
    "XAUUSD|ORB_v1": 3.0,       # 2022-23 +0.05  2024-25 +0.07  2026 +0.11
    "US TECH 100|ORB_v1": 3.0,  #   (no data)    2024-25 +0.04  2026 +0.05

    # VWAP session modes — per-market picks positive in every window (edge lab)
    "XAUUSD|VWAP_v1": 5.0,       # SESSION_TREND day_dir+gap_dir+early: +0.26 / +0.36 / +0.42
    "US TECH 100|VWAP_v1": 10.0, # SESSION_TREND day_dir+rel_vol_open+early: 2024-25 +0.59 / 2026 +0.39
    "BTCUSD|VWAP_v1": 5.0,       # SESSION_PULLBACK gap_dir+early: +0.30 / +0.37 / +0.13

    # BiasIFVG (research/16)
    "USOUSD|BiasIFVG_v1": 5.0,
    "UKOUSD|BiasIFVG_v1": 5.0,
}


# ─────────────────────────────────────────────────────────────────────────────
# PER-SYMBOL STRATEGY PARAMETERS
# ─────────────────────────────────────────────────────────────────────────────
# Measured per-symbol values laid onto a slot's strategy params block. Both the
# live path (bot_service, at engine build) and the backtest path
# (apply_strategy_params) apply this table, and a slot's own override or an
# explicit request value always wins over it.
#
# The name is historical: it first held the research/26 synthetic template
# strategies (removed 2026-09-11, restored 2026-09-14).
#
# Format: "SYMBOL|Strategy_id": {param: value}
SYNTH_SLOT_PARAMS: dict[str, dict[str, Any]] = {
    "GBPJPY|ORB_v1": {"session": "london", "range_minutes": 60, "breakout_timeframe": "M5",
                      "require_trend": True, "min_stop_atr": 0.5},
    "BTCUSD|ORB_v1": {"session": "ny", "range_minutes": 60, "breakout_timeframe": "M5",
                      "require_trend": True, "min_stop_atr": 0.5},
    "XAUUSD|ORB_v1": {"session": "ny", "range_minutes": 60, "breakout_timeframe": "M5",
                      "require_trend": True, "min_stop_atr": 0.5},
    "US TECH 100|ORB_v1": {"session": "ny", "range_minutes": 60, "breakout_timeframe": "M5",
                           "require_trend": True, "min_stop_atr": 0.5},

    # VWAP session modes (data/edge_lab/search_robust_*.json, per market)
    "XAUUSD|VWAP_v1": {"entry_mode": "SESSION_TREND", "session_mode_session": "native",
                       "session_mode_gates": ["day_dir", "gap_dir", "early"]},
    "US TECH 100|VWAP_v1": {"entry_mode": "SESSION_TREND", "session_mode_session": "native",
                            "session_mode_gates": ["day_dir", "rel_vol_open", "early"]},
    "BTCUSD|VWAP_v1": {"entry_mode": "SESSION_PULLBACK", "session_mode_session": "native",
                       "session_mode_gates": ["gap_dir", "early"]},

    # Synthetic-index strategies (research/26) — superseded by the 2026-09-14 study
    "BOOM 1000 INDEX|RangeRevert_v1":   {"stop_atr_multiple": 5.0, "tp1_rr": 5.0, "revert_k_atr": 2.0},
    "BOOM 500 INDEX|RangeRevert_v1":    {"stop_atr_multiple": 5.0, "tp1_rr": 5.0, "revert_k_atr": 2.0},
    "CRASH 1000 INDEX|TrendDrift_v1":   {"stop_atr_multiple": 5.0, "tp1_rr": 8.0},
    "CRASH 500 INDEX|RangeRevert_v1":   {"stop_atr_multiple": 1.0, "tp1_rr": 5.0, "revert_k_atr": 2.0},
    "VOLATILITY 75 INDEX|TrendDrift_v1": {"stop_atr_multiple": 2.5, "tp1_rr": 5.0},
    "VOLATILITY 25 INDEX|RangeBreakout_v1": {"stop_atr_multiple": 2.5, "tp1_rr": 3.0, "breakout_lookback": 20},
    "VOLATILITY 100 INDEX|RangeRevert_v1": {"stop_atr_multiple": 2.5, "tp1_rr": 8.0, "revert_k_atr": 2.0},
    "JUMP 25 INDEX|RangeRevert_v1":     {"stop_atr_multiple": 2.5, "tp1_rr": 8.0, "revert_k_atr": 2.0},
    "JUMP 100 INDEX|TrendDrift_v1":     {"stop_atr_multiple": 5.0, "tp1_rr": 5.0},
    "RANGE BREAK 100 INDEX|SpikeFade_v1": {"stop_atr_multiple": 5.0, "tp1_rr": 5.0, "spike_k_atr": 3.0},
    "RANGE BREAK 200 INDEX|SpikeFade_v1": {"stop_atr_multiple": 5.0, "tp1_rr": 5.0, "spike_k_atr": 3.0},
}


def get_synth_slot_params(symbol: str, strategy_id: str) -> dict[str, Any]:
    """Measured per-symbol parameters for a synthetic-index slot, or {}.

    Symbol matching is case-insensitive, mirroring SLOT_TP1_RR, because MT5 names
    arrive in mixed case ("Crash 1000 Index") while the table is keyed upper.
    """
    return dict(SYNTH_SLOT_PARAMS.get(f"{symbol.upper()}|{strategy_id}") or {})


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


def has_measured_target(strategy_id: str) -> bool:
    """False when this strategy's `tp1_rr` is a never-filled placeholder.

    See NO_MEASURED_TARGET. Callers that present the target to a user should say
    so rather than offering it as a tunable.
    """
    return strategy_id not in NO_MEASURED_TARGET


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


def measured_min_rr_by_strategy(strategy_ids) -> dict[str, float]:
    """Each strategy's measured minimum R:R, for the strategies that declare one.

    RiskEngine lowers the account's `min_rr` to this value for that strategy's
    signals only (never raises it). A measured target below the account gate
    otherwise rejects every signal the strategy produces — IVW_v1's 1:2 under the
    shipped 3.0 — and does so on every path, since the Backtester page always
    sends its own min_rr and the portfolio has one gate for all rows."""
    out: dict[str, float] = {}
    for sid in strategy_ids:
        v = get_strategy_defaults(sid).get("min_rr") if sid else None
        if v is not None:
            out[str(sid)] = float(v)
    return out
