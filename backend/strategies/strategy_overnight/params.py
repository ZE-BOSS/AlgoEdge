"""
backend/strategies/strategy_overnight/params.py

OvernightSession_v1 — hold an equity index from the cash close to the next cash
open, and nothing else.

This is the overnight (close-to-open) risk premium: on equity indices the return
earned while the cash market is SHUT has historically been most of the total
return, and the return earned while it is open has been close to zero. It is one
of the most replicated anomalies in the equity literature, and unlike most of
them it survives on a retail CFD feed, because one trade a night is one spread a
night.

Measured as the app runs it (scripts/run_app_form_check.py), Deriv M5
2021-10 -> 2026-09, US Tech 100 / US SP 500 / Germany 40, 2,006 nights:
+0.065R, 52.8% win, t +3.15, Sharpe 1.28, +80.1% on $10,000 at 0.5% risk with a
21.7% maximum drawdown. It is also the ONLY one of the three strategies shipped
on 2026-09-25 that stayed positive on FundedNext's own bars (654 nights,
+0.056R, t +1.73, +13.8%).
"""

from dataclasses import dataclass


@dataclass
class OvernightSessionParams:
    session: str = "ny"
    """Which cash session's close to buy and open to sell into: "ny" (09:30-16:00
    America/New_York) or "london" (08:00-16:30 Europe/London). Both are
    DST-aware. Measured on "ny", including for Germany 40 — the premium is paid
    over the US night."""

    stop_atr_multiple: float = 0.5
    """Stop, in daily ATR, below the entry. The research had NO stop and used
    0.5 x ATR only to normalise its reporting; the app always places a real one,
    so it was re-measured with the stop live: 0.5 was the best of 0.5 / 1 / 1.5 /
    2 / 3 (+0.065R, falling to +0.011R at 3x). It is tight, and roughly 10% of
    nights are stopped out — that is the strategy, not a flaw. Widening it lowers
    the drawdown AND the return, because a wider stop is a bigger R for the same
    move."""

    atr_days: int = 14
    """Days in the daily ATR the stop is a multiple of."""
