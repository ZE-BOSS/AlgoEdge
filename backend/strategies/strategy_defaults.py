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
    "TrendBreakout_v1", "OvernightSession_v1",
    # fib pullback / leg entry, leaves on a chandelier trail (2026-10-25)
    "FibTrend_v1",
    # roboquant templates that exit on time / a regime flip, never on target
    "RQ_TMeanReversion_v1", "RQ_CharlesMonger_v1",
    # ...and the 2026-10-20 ports whose exits are a band flip / an EMA cross
    "RQ_SigmaBreakout_v1", "RQ_DailyVWAP_v1", })


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
    # Kronos' confident direction call (strategies/strategy_kronos). Measured
    # with ONE target at 6R that rarely fills - trades end on the 10-bar time
    # exit - so the account's multi-TP grid must not apply: blended with TP1 at
    # 1R it came out below min_rr 3.0 and the risk engine refused EVERY signal
    # in the first app backtest (2026-10-19).
    "KRONOS_v1": {
        "tp_count": 1,
        "tp1_rr": 6.0,
        "be_mode": "NONE",
        "trail_method_tp1": "NONE",
        "trail_mode": "NONE",
        "evidence": (
            "research/kronos/FINDINGS-2026-10-19.md 6b: SPX500 H1 FundedNext Feb 2025 -> Oct 2026, "
            "4-of-5 paths agree, 10-bar hold, 2xATR stop, app engine with swap: 440 trades, "
            "+0.152R, t 2.77, PF 1.39; positive in both halves; beat 30/30 random-direction runs. "
            "Best of 12 symbols tested and NDX100 was negative: a forward-test candidate."
        ),
    },
    # ── roboquant.dev templates, replicated 2026-10-19 ───────────────────────
    # Each template's own exit, ONE target at its own R:R, no break-even or
    # trail - the account TP grid would re-shape every one of them. Three use
    # a target far below the account's min_rr (NQ Precision 0.14R, NY ORB
    # 0.4R, Crazy ORB 0.35R): `min_rr` here is that strategy's own R:R, or the
    # risk engine refuses every signal (it did for KRONOS_v1 at first).
    "RQ_NQPrecision_v1": {
        "tp_count": 1, "tp1_rr": round(0.3 / 2.2, 4), "min_rr": round(0.3 / 2.2, 4),
        "be_mode": "NONE", "trail_method_tp1": "NONE", "trail_mode": "NONE",
        "evidence": "roboquant.dev template NQ Precision Bot, NQ 1m preset: SL 2.2x / TP 0.3x ATR(14), "
                    "8-min time close. Claimed +1,377%, 94% win, DD 32.8%, PF 2.48 (fixed 6 NQ).",
    },
    "RQ_NYORB_v1": {
        "tp_count": 1, "tp1_rr": 0.4, "min_rr": 0.4,
        "be_mode": "NONE", "trail_method_tp1": "NONE", "trail_mode": "NONE",
        "evidence": "roboquant.dev template NY Open Range Breakout, MNQ 15m preset: stop 0.4x ATR(14), "
                    "target 0.4R, RVOL>=1.0, ATR 0.10-0.60% of price. Claimed +1,909%, 74% win, DD 42.1%, PF 1.22.",
    },
    "RQ_CrazyORB_v1": {
        "tp_count": 1, "tp1_rr": 0.35, "min_rr": 0.35,
        "be_mode": "NONE", "trail_method_tp1": "NONE", "trail_mode": "NONE",
        "evidence": "roboquant.dev template Crazy ORB, NQ 15m preset: 09:15-09:30 range, stop 1x ATR(14), "
                    "target 0.35R, 2 trades/day. Claimed +594%, 81.3% win, DD 19.0%, PF 1.57.",
    },
    "RQ_TMeanReversion_v1": {
        "tp_count": 1, "tp1_rr": 40.0,
        "be_mode": "NONE", "trail_method_tp1": "NONE", "trail_mode": "NONE",
        "evidence": "roboquant.dev template T-Mean Reversion, MNQ 1h preset: buy Fri 18:00 UTC after a red "
                    "Friday, exit Wed 10:00 UTC, no stop/target. Claimed +708%, 63.4% win, DD 27.7%, PF 2.58.",
    },
    "RQ_CharlesMonger_v1": {
        "tp_count": 1, "tp1_rr": 50.0,
        "be_mode": "NONE", "trail_method_tp1": "NONE", "trail_mode": "NONE",
        "evidence": "roboquant.dev template Charles Monger, ES 1h preset: EMA(200) high/close band flips, "
                    "long only, 1% stop. Claimed +1,116%, 24.9% win, DD 55.5%, PF 1.32.",
    },
    # ── roboquant.dev templates, ported 2026-10-20 (strategy_roboquant/engine_more.py) ──
    "RQ_FlashTriggerORB_v1": {
        "tp_count": 1, "tp1_rr": 3.0, "min_rr": 3.0,
        "be_mode": "NONE", "trail_method_tp1": "NONE", "trail_mode": "NONE",
        "evidence": "roboquant.dev template The Flash Trigger ORB, MNQ 15m preset: 14:30 UTC candle, close beyond it, stop at the far side, 3R, trail 25 pts after 70. Claimed +195%, 61.6% win, DD 11.1%, PF 1.36.",
    },
    "RQ_ScarfaceORB_v1": {
        "tp_count": 1, "tp1_rr": 2.0, "min_rr": 2.0,
        "be_mode": "NONE", "trail_method_tp1": "NONE", "trail_mode": "NONE",
        "evidence": "roboquant.dev template Scarface ORB, MNQ 1m preset: 5-min range, breakout/retest/momentum candle, 1.4x ATR(14) stop, 2R. Claimed +48.4%, 41.5% win, DD 7.7%, PF 1.35.",
    },
    "RQ_IVB_v1": {
        "tp_count": 1, "tp1_rr": 1.3333, "min_rr": 1.3333,
        "be_mode": "NONE", "trail_method_tp1": "NONE", "trail_mode": "NONE",
        "evidence": "roboquant.dev template IVB, MNQ 5m preset: 09:30-10:00 IB below 0.9x its 10-day average, 1.1x volume surge, stop 3x / target 4x ATR(14). Claimed +309.8%, 52.0% win, DD 20.1%, PF 1.37.",
    },
    "RQ_GigaORB_v1": {
        "tp_count": 1, "tp1_rr": 1.0, "min_rr": 1.0,
        "be_mode": "NONE", "trail_method_tp1": "NONE", "trail_mode": "NONE",
        "evidence": "roboquant.dev template Giga ORB, MNQ 15m preset: 09:30-09:45 range, stop at the far side, 1R, wick filter 13%, prior-day bias. Claimed +477.1%, 55.9% win, DD 21.5%, PF 1.22.",
    },
    "RQ_SigmaBreakout_v1": {
        "tp_count": 1, "tp1_rr": 40.0,
        "be_mode": "NONE", "trail_method_tp1": "NONE", "trail_mode": "NONE",
        "evidence": "roboquant.dev template Sigma Breakout, MNQ 30m preset: 2-day sigma bands at 1.5x around the RTH open, flip on the opposite band, flat at 16:00. Claimed +440.9%, 55.3% win, DD 19.7%, PF 1.39.",
    },
    "RQ_DailyVWAP_v1": {
        "tp_count": 1, "tp1_rr": 40.0,
        "be_mode": "NONE", "trail_method_tp1": "NONE", "trail_mode": "NONE",
        "evidence": "roboquant.dev template Daily Anchored VWAP Signal Strategy, MES 3m preset: EMA(11) vs daily VWAP, exit on a close back across the EMA. Claimed +299.9%, 56.6% win, DD 22.0%, PF 1.84.",
    },
    "RQ_FridayRevisit_v1": {
        "tp_count": 1, "tp1_rr": 10.0, "min_rr": 10.0,
        "be_mode": "NONE", "trail_method_tp1": "NONE", "trail_mode": "NONE",
        "evidence": "roboquant.dev template Friday Range Revisit - Monday Short, NQ 5m preset: short Monday's RTH open inside Friday's range, stop 0.1x / target 1x daily ATR. Claimed +573.3%, 22.6% win, DD 15.2%, PF 2.96.",
    },
    "RQ_M30ORB_v1": {
        "tp_count": 1, "tp1_rr": 1.5, "min_rr": 1.5,
        "be_mode": "NONE", "trail_method_tp1": "NONE", "trail_mode": "NONE",
        "evidence": "roboquant.dev template M30 ORB + M5 Breakout, MNQ 5m preset: 09:30-10:00 range, RVOL 1.4x, stop 3x ATR(14), 1.5R, flat 15:00. Claimed +345.7%, 47.6% win, DD 33.7%, PF 1.22.",
    },
    "RQ_SRSweep_v1": {
        "tp_count": 1, "tp1_rr": 1.0, "min_rr": 1.0,
        "be_mode": "NONE", "trail_method_tp1": "NONE", "trail_mode": "NONE",
        "evidence": "roboquant.dev template S&R Sweep, NQ 5m preset: 3/3 pivot levels, sweep and close back with EMA(30) trend, stop at the candle, 1R. Claimed +57.0%, 52.1% win, DD 11.0%, PF 1.25.",
    },
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

    # ── FibTrend — Fibonacci as the ENTRY of a trend system (2026-10-25) ─
    #
    # research/fib/FIB-USES-2026-10-25.md. No target: the chandelier trail is
    # the exit, so tp1_rr is a placeholder (NO_MEASURED_TARGET). Real CFDs run
    # the "immediate" form through SYNTH_SLOT_PARAMS below.
    "FibTrend_v1": {
        "tp_count": 1,
        "tp1_rr": 20.0,
        "be_mode": "NONE",
        "trail_method_tp1": "NONE",
        "trail_mode": "NONE",
        "evidence": (
            "research/fib 2026-10-25, H4 structure, 2023-10 -> 2026-10, 20 sign-flip surrogates per "
            "market. Synthetics (12): fib 0.5 pullback with the 50-day trend, K=5 legs, 3x H4 ATR "
            "trail: +0.183R, +0.202R above surrogate, p<0.05, positive 7 of 8 years. Real CFDs (8): "
            "same trend + trail entered at once (no fib): +0.189R, p 0.05, carried by XAUUSD, XAGUSD, "
            "BTCUSD; Wall Street 30 negative. APP BACKTEST (scripts/run_app_backtest.py, costs + swap, "
            "$10k, 1% compounded, 2023-10 -> 2026-10): all 20 markets 904 trades +0.15R, $26,898, DD 27.6%; "
            "last 9 months +21.3%. XAUUSD +0.73R, XAGUSD +0.57R, US Tech 100 +0.23R, Vol 10/25 and Multi "
            "Step 2 ~+0.4R; Wall Street 30 -0.32R, Step Index -0.43R. Step 300/400/500 mostly refused at "
            "$10k (0.1-lot minimum). Not significant after correcting for 9 variants (t +1.75)."
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

    # FibTrend_v1 on real CFDs: the trend + trail without waiting for the fib
    # pullback measured better on all 8 together (research/fib, variant X), and
    # that form used 3-bar swings - with the synthetic form's 5 every leg is
    # confirmed 8 hours later. Applied to every real CFD that was tested, not
    # only the winners.
    **{f"{s}|FibTrend_v1": {"entry_mode": "immediate", "stop_level": 0.5, "swing_bars": 3}
       for s in ("XAUUSD", "XAGUSD", "US TECH 100", "US SP 500", "WALL STREET 30",
                 "GBPJPY", "EURUSD", "BTCUSD")},

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
