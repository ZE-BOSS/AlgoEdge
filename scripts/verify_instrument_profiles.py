"""
scripts/verify_instrument_profiles.py

[1.16/C3] Check every hardcoded InstrumentProfile against what the broker
actually reports, and say plainly where they disagree.

This is task 1.16 — described in TASKS.md as "the correct/principled way to
close 1.15 without guessing". The alternative that was rejected there was
replacing one unverified constant with another guessed one; this replaces them
with measured ones, or reports that a symbol cannot be measured at all.

Run it whenever you change brokers, or after any profile edit:

    venv_win/Scripts/python.exe scripts/verify_instrument_profiles.py
    venv_win/Scripts/python.exe scripts/verify_instrument_profiles.py --fix-preview
    venv_win/Scripts/python.exe scripts/verify_instrument_profiles.py --symbols XRPUSD,BTCUSD

Exit code is non-zero when a MISMATCH is found, so this can gate a deploy.
An UNAVAILABLE symbol is not a failure — brokers legitimately differ in what
they list — but it is reported, because an unavailable symbol's profile is
unverifiable and should not be trusted.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import MetaTrader5 as mt5
except ImportError:
    print("MetaTrader5 package not installed — this script needs a Windows MT5 terminal.")
    sys.exit(2)

from backend.risk.compounding import INSTRUMENT_PROFILES  # noqa: E402

# Profile field -> symbol_info attribute. Only fields the broker actually
# publishes are compared; `point_value_per_lot` is a derived house concept with
# no direct symbol_info twin, so it is reported but never auto-flagged.
COMPARISONS = [
    ("lot_min", "volume_min"),
    ("lot_max", "volume_max"),
    ("lot_step", "volume_step"),
    ("point_size", "point"),
    ("contract_size", "trade_contract_size"),
]

# Floating-point profiles vs. broker doubles: compare relatively, not exactly.
REL_TOL = 1e-6


def close_enough(a: float, b: float) -> bool:
    if a == b:
        return True
    if a is None or b is None:
        return False
    scale = max(abs(a), abs(b), 1e-12)
    return abs(a - b) / scale < REL_TOL


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", help="Comma-separated subset to check")
    ap.add_argument("--fix-preview", action="store_true",
                    help="Print corrected InstrumentProfile lines for every mismatch")
    args = ap.parse_args()

    if not mt5.initialize():
        print(f"MT5 initialize() failed: {mt5.last_error()}")
        print("Start the terminal and log in, then re-run.")
        return 2

    wanted = (
        [s.strip().upper() for s in args.symbols.split(",")]
        if args.symbols else sorted(INSTRUMENT_PROFILES)
    )

    mismatches: list[tuple[str, list[str]]] = []
    unavailable: list[str] = []
    matched = 0

    for symbol in wanted:
        profile = INSTRUMENT_PROFILES.get(symbol)
        if profile is None:
            print(f"[SKIP]  {symbol}: no profile defined")
            continue

        if not mt5.symbol_select(symbol, True):
            unavailable.append(symbol)
            continue
        info = mt5.symbol_info(symbol)
        if info is None:
            unavailable.append(symbol)
            continue

        diffs = []
        for pfield, bfield in COMPARISONS:
            ours = getattr(profile, pfield, None)
            theirs = getattr(info, bfield, None)
            if ours is None or theirs is None:
                continue
            if not close_enough(float(ours), float(theirs)):
                ratio = (float(theirs) / float(ours)) if ours else float("inf")
                diffs.append(
                    f"    {pfield:<18} profile={ours!r:<14} broker={theirs!r:<14} "
                    f"(broker is {ratio:.4g}x)"
                )

        if diffs:
            mismatches.append((symbol, diffs))
            print(f"[MISMATCH] {symbol}")
            for d in diffs:
                print(d)
            if args.fix_preview:
                print(f"    -> lot_min={info.volume_min}, lot_max={info.volume_max}, "
                      f"lot_step={info.volume_step}, point_size={info.point}, "
                      f"contract_size={info.trade_contract_size}")
        else:
            matched += 1

    print()
    print("=" * 64)
    print(f"  matched      : {matched}")
    print(f"  MISMATCHED   : {len(mismatches)}")
    print(f"  unavailable  : {len(unavailable)}")
    if unavailable:
        print(f"    {', '.join(unavailable)}")
        print("    (not listed by this broker — their profiles are UNVERIFIABLE here,")
        print("     and any run naming them fails at data fetch rather than at sizing)")
    if mismatches:
        print()
        print("  A mismatch on lot_min/lot_step is not cosmetic: the sizer can emit a")
        print("  volume the broker rejects outright, so the backtest simulates trades")
        print("  that could never have been placed.")
    print("=" * 64)

    mt5.shutdown()
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
