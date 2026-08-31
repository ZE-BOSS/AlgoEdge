"""[17.5] Locks the backtest and live trailing implementations together.

There are two trailing implementations:
  backtest : backend/risk/trailing_manager.py  TrailingManager.calculate_trailing_sl
  live     : backend/services/position_manager.py  _calculate_trailing_sl

They are NOT shared code. They agree today, but nothing enforced that, so an
edit to one could silently diverge the other — and a divergence means a
backtested result stops describing what live will do.

Consolidating them into one implementation would be the ideal fix, but the live
one is `async`, fetches its own ATR over the network, and sits directly in the
order-modification path. Rewriting that carries more risk than the drift it
prevents. This test is the cheaper guarantee: it re-implements the live gate
(which is pure arithmetic) and asserts both produce identical stops.

If you DO consolidate later, this test is what proves the merge was behaviour
preserving.
"""
import pytest

from backend.risk.trailing_manager import TrailingManager

PIP = 0.0001
CFG = {
    "trail_pips": 15.0,
    "atr_trail_multiplier": 1.5,
    "trail_pct": 0.5,
    "trail_step_pips": 5.0,
}


def live_gate(is_buy: bool, new_sl: float, current_sl: float, step: float):
    """Verbatim port of position_manager._calculate_trailing_sl's ratchet gate.

    Live source:
        if is_buy:
            if new_sl >= current_sl + step: return new_sl
        else:
            if current_sl == 0.0 or new_sl <= current_sl - step: return new_sl
        return None
    """
    if is_buy:
        return new_sl if new_sl >= current_sl + step else None
    return new_sl if (current_sl == 0.0 or new_sl <= current_sl - step) else None


def live_fixed_pips(is_buy, price, trail_pips=15.0):
    d = trail_pips * PIP
    return price - d if is_buy else price + d


def live_pct(is_buy, price, pct=0.5):
    d = price * (pct / 100.0)
    return price - d if is_buy else price + d


@pytest.mark.parametrize("is_buy", [True, False])
@pytest.mark.parametrize("current_sl", [0.0, 1.0990, 1.1100])
def test_fixed_pips_matches_live(is_buy, current_sl):
    tm = TrailingManager(CFG)
    price = 1.1100
    step = CFG["trail_step_pips"] * PIP
    direction = "BUY" if is_buy else "SELL"

    bt = tm.calculate_trailing_sl("FIXED_PIPS", direction, price, current_sl, PIP)
    live = live_gate(is_buy, live_fixed_pips(is_buy, price), current_sl, step)

    if bt is None or live is None:
        assert (bt is None) == (live is None), (
            f"one trailed and the other did not: backtest={bt} live={live} "
            f"(dir={direction}, current_sl={current_sl})"
        )
    else:
        assert bt == pytest.approx(live, abs=1e-9)


@pytest.mark.parametrize("is_buy", [True, False])
def test_pct_trail_matches_live(is_buy):
    tm = TrailingManager(CFG)
    price, current_sl = 1.1100, 1.0990
    step = CFG["trail_step_pips"] * PIP
    direction = "BUY" if is_buy else "SELL"

    bt = tm.calculate_trailing_sl("PCT_TRAIL", direction, price, current_sl, PIP)
    live = live_gate(is_buy, live_pct(is_buy, price), current_sl, step)

    assert (bt is None) == (live is None)
    if bt is not None:
        assert bt == pytest.approx(live, abs=1e-9)


def test_atr_trail_converges_over_a_price_path():
    """Live anchors ATR to CURRENT price, backtest to the HIGHEST since entry.

    Those look different at a single point, but the ratchet makes them converge
    when live is polled every bar — which is how it actually runs. This walks a
    run-up-then-pullback path and asserts they end identical.
    """
    tm = TrailingManager(CFG)
    atr, mult = 0.0020, CFG["atr_trail_multiplier"]
    step = CFG["trail_step_pips"] * PIP

    path = [1.1000, 1.1050, 1.1100, 1.1150, 1.1200, 1.1160, 1.1120, 1.1100]
    live_sl = bt_sl = 1.0990
    highest = 1.1000

    for p in path:
        highest = max(highest, p)
        cand = p - atr * mult                       # live: anchored to current
        if cand >= live_sl + step:
            live_sl = cand
        nb = tm.calculate_trailing_sl(              # backtest: anchored to highest
            "ATR_TRAIL", "BUY", p, bt_sl, PIP,
            highest_price=highest, lowest_price=0.0, atr_value=atr,
        )
        if nb is not None:
            bt_sl = nb

    assert bt_sl == pytest.approx(live_sl, abs=1e-9), (
        f"ATR trailing diverged over the path: backtest={bt_sl} live={live_sl}"
    )


def test_ratchet_never_moves_stop_backwards():
    """The stop must only ever improve — this is what stops a 3R giveback."""
    tm = TrailingManager(CFG)
    assert tm.calculate_trailing_sl("FIXED_PIPS", "BUY", 1.1000, 1.1100, PIP) is None
    assert tm.calculate_trailing_sl("FIXED_PIPS", "SELL", 1.1100, 1.0900, PIP) is None
