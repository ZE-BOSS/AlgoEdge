"""
Regression tests for the Stage 15 cost gates.

Run directly — this repo has no pytest installed in either venv:

    python tests/test_stage15_cost_gates.py

Both mechanisms exist because of the January-August 2026 corpus
(implementation/RESEARCH-2026-08-30-CORPUS.md): three strategies were
profitable measured on price and lost money in cash, and the whole gap was
transaction cost. The numbers asserted below are taken from that corpus so a
future change that breaks the relationship fails here rather than quietly in a
live run.
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.risk import position_sizer as ps  # noqa: E402
from backend.risk.engine import RiskEngine  # noqa: E402

_FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}  {detail}")
        _FAILURES.append(label)


# XAGUSD exactly as the corpus recorded it: spread 96 pips, slippage 10 pips,
# pip size 0.001. Round trip = (96 + 2x10) x 0.001 = 0.116 in price.
_XAG_COSTS = {
    "spread_pips": 96.0, "slippage_pips": 10.0, "stops_level_pips": 1.0,
    "commission_per_lot": 0.0, "swap_long_per_lot_per_day": 0.0,
    "swap_short_per_lot_per_day": 0.0, "source": "TEST",
}
_XAG_INFO = {
    "point": 0.001, "stops_level_points": 1, "spread_points": 96,
    "tick_value": 5.0, "tick_size": 0.001, "source": "TEST",
}
_XAG_ROUND_TRIP = (96.0 + 2 * 10.0) * 0.001
_XAG_CORPUS_MEDIAN_STOP = 0.9225  # VWAP x XAGUSD, 203 groups


def _min_stop(multiple: float) -> tuple[float, str]:
    with patch.object(ps, "get_pip_size", lambda s: 0.001), \
         patch("backend.risk.broker_costs.get_broker_costs",
               lambda s, use_live_mt5=True: _XAG_COSTS):
        return ps.minimum_stop_distance(
            "XAGUSD", _XAG_INFO, use_live_mt5=False,
            min_stop_spread_multiple=2.0, min_stop_cost_multiple=multiple,
        )


def test_economic_stop_floor() -> None:
    print("\n[15.2] economic stop floor — stop >= N x (spread + 2 x slippage)")

    # Disabled by default: the shipped behaviour must not change until the
    # setting is turned on deliberately.
    off, why_off = _min_stop(0.0)
    check("disabled (0.0) does not use the round-trip term",
          "round_trip" not in why_off, f"got {why_off!r}")
    check("disabled floor still comes from the physical checks",
          off < _XAG_CORPUS_MEDIAN_STOP,
          f"floor {off} would reject the corpus median stop")

    # Slippage counts twice — this is what distinguishes the economic floor
    # from min_stop_spread_multiple, which sees the spread only.
    ten, why_ten = _min_stop(10.0)
    check("10x floor equals 10 round trips",
          abs(ten - 10 * _XAG_ROUND_TRIP) < 1e-9,
          f"{ten} vs {10 * _XAG_ROUND_TRIP}")
    check("10x names itself in the reason", "round_trip_cost x10" in why_ten,
          f"got {why_ten!r}")

    # The corpus relationship: XAGUSD's stop covers 8x its round trip, so 6x
    # accepts it and 10x does not. If this flips, the measurement in §2.1 of
    # the research doc no longer describes the code.
    six, _ = _min_stop(6.0)
    check("6x accepts the corpus median XAGUSD stop (8x cover)",
          _XAG_CORPUS_MEDIAN_STOP >= six, f"floor {six}")
    check("10x rejects it", _XAG_CORPUS_MEDIAN_STOP < ten, f"floor {ten}")

    # Monotonic in N — a larger multiple can never demand a smaller stop.
    floors = [_min_stop(n)[0] for n in (0.0, 6.0, 10.0, 15.0, 20.0)]
    check("floor is monotonic in the multiple",
          all(a <= b for a, b in zip(floors, floors[1:])), str(floors))


def test_min_rr_feasibility_warning() -> None:
    print("\n[15.4] min_rr feasibility — the ladder that rejects every signal")

    seen: list[str] = []

    def capture(msg, *a, **k):
        seen.append(str(msg))

    # Your saved config. TP 1.5/3/5 at 50/30/20 blends to 2.65, so min_rr=3
    # refuses every signal before sizing.
    with patch("backend.risk.engine.logger.error", capture):
        RiskEngine({"tp_count": 3, "tp1_rr": 1.5, "tp2_rr": 3, "tp3_rr": 5,
                    "tp_splits": "50,30,20", "min_rr": 3})
    check("warns on the impossible ladder", len(seen) == 1, f"{len(seen)} messages")
    check("names the blended value", seen and "2.65" in seen[0],
          seen[0] if seen else "no message")
    check("names the configured min_rr", seen and "3.00" in seen[0],
          seen[0] if seen else "no message")

    # Same ladder, reachable threshold — must be silent.
    seen.clear()
    with patch("backend.risk.engine.logger.error", capture):
        RiskEngine({"tp_count": 3, "tp1_rr": 1.5, "tp2_rr": 3, "tp3_rr": 5,
                    "tp_splits": "50,30,20", "min_rr": 2.0})
    check("silent when the ladder can meet min_rr", not seen, str(seen))

    # The Stage 15.3 proposal: one TP, trailing does the rest.
    seen.clear()
    with patch("backend.risk.engine.logger.error", capture):
        RiskEngine({"tp_count": 1, "tp1_rr": 1.5, "tp_splits": "100", "min_rr": 1.0})
    check("silent for the single-TP proposal", not seen, str(seen))

    # tp_volume_pcts takes priority over tp_splits, matching MultiTPManager.
    seen.clear()
    with patch("backend.risk.engine.logger.error", capture):
        RiskEngine({"tp_count": 2, "tp1_rr": 1.0, "tp2_rr": 4.0,
                    "tp_splits": "90,10", "tp_volume_pcts": [10, 90],
                    "min_rr": 3.0})
    check("uses tp_volume_pcts, not tp_splits, when both are set",
          not seen,
          "warned, so it blended 1.3 (tp_splits) instead of 3.7 (tp_volume_pcts)")

    # A malformed config must not raise out of __init__.
    try:
        RiskEngine({"tp_count": 3, "tp_splits": "not,a,number", "min_rr": 3})
        check("survives a malformed ladder", True)
    except Exception as e:  # noqa: BLE001
        check("survives a malformed ladder", False, f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    test_economic_stop_floor()
    test_min_rr_feasibility_warning()
    print()
    if _FAILURES:
        print(f"{len(_FAILURES)} FAILED: {', '.join(_FAILURES)}")
        sys.exit(1)
    print("all Stage 15 cost-gate checks passed")
