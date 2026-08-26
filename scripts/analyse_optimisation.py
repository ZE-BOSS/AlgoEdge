"""
scripts/analyse_optimisation.py

Follow-up analysis on the signal corpus captured by `optimise_strategy.py`.

Reads `debug/apa_optimisation.json` — no MT5, no re-run — so combination
hypotheses are instant to test.

The discipline that matters here: with 327 signals and a dozen possible splits,
SOME split will look profitable by chance. So every candidate is checked three
ways before it is believed:

  1. **Effect size** — is the gap large, or within noise of zero?
  2. **Monotonicity across targets** — a real edge shows up at most targets, not
     one. A single standout column is a fluke.
  3. **Per-symbol consistency** — does it hold on each symbol independently?
     This is the strongest test available without a hold-out period, because a
     pattern that only exists on one instrument is that instrument, not the rule.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TARGETS = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]


def exp_at(rows, t):
    if not rows:
        return float("nan")
    w = sum(1 for r in rows if r["first_to"] >= t)
    return (w * t - (len(rows) - w)) / len(rows)


def row(label, rows, targets=TARGETS, width=34):
    if not rows:
        return f"{label:<{width}}{0:>5}" + "      —" * len(targets)
    cells = "".join(f"{exp_at(rows, t):>7.2f}" for t in targets)
    return f"{label:<{width}}{len(rows):>5}{cells}"


def header(title, width=34):
    print(f"\n{title}")
    h = f"{'':<{width}}{'n':>5}" + "".join(f"{t:>7.1f}" for t in TARGETS)
    print(h)
    print("-" * len(h))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="debug/apa_optimisation.json")
    args = ap.parse_args()

    rows = json.loads(Path(args.file).read_text(encoding="utf-8"))
    print(f"{len(rows)} signals from {len({r['symbol'] for r in rows})} symbols")

    symbols = sorted({r["symbol"] for r in rows})
    LO = lambda r: r["confluence"] <= 73          # noqa: E731
    AM = lambda r: r["hour"] < 12                 # noqa: E731

    # ── 1. Is the confluence inversion real, or one symbol's quirk? ──
    header("CONFLUENCE EFFECT, PER SYMBOL (low score = <=73)")
    for s in symbols:
        ss = [r for r in rows if r["symbol"] == s]
        print(row(f"  {s} low", [r for r in ss if LO(r)]))
        print(row(f"  {s} high", [r for r in ss if not LO(r)]))
    consistent = sum(
        1 for s in symbols
        if exp_at([r for r in rows if r["symbol"] == s and LO(r)], 2.0)
        > exp_at([r for r in rows if r["symbol"] == s and not LO(r)], 2.0)
    )
    print(f"\n  low beats high at 2R on {consistent}/{len(symbols)} symbols")

    # ── 2. Same test for the session effect ──
    header("SESSION EFFECT, PER SYMBOL (early = before 12:00 UTC)")
    for s in symbols:
        ss = [r for r in rows if r["symbol"] == s]
        print(row(f"  {s} 07-12", [r for r in ss if AM(r)]))
        print(row(f"  {s} 12-17", [r for r in ss if not AM(r)]))
    consistent_s = sum(
        1 for s in symbols
        if exp_at([r for r in rows if r["symbol"] == s and AM(r)], 2.0)
        > exp_at([r for r in rows if r["symbol"] == s and not AM(r)], 2.0)
    )
    print(f"\n  early beats late at 2R on {consistent_s}/{len(symbols)} symbols")

    # ── 3. Do the two filters compound, or are they the same effect? ──
    header("COMBINED FILTERS")
    print(row("all signals", rows))
    print(row("low confluence only", [r for r in rows if LO(r)]))
    print(row("early session only", [r for r in rows if AM(r)]))
    print(row("low conf AND early", [r for r in rows if LO(r) and AM(r)]))
    print(row("high conf AND late", [r for r in rows if not LO(r) and not AM(r)]))

    # Are they independent? If low-confluence signals are mostly early anyway,
    # the two "effects" are one effect counted twice.
    lo = [r for r in rows if LO(r)]
    overlap = sum(1 for r in lo if AM(r)) / len(lo) if lo else 0
    base = sum(1 for r in rows if AM(r)) / len(rows)
    print(f"\n  early share: {base:.0%} of all signals, {overlap:.0%} of low-confluence")
    print("  (similar percentages = independent filters; very different = one effect)")

    # ── 4. Retest rejection — the confluence component just added ──
    header("RETEST REJECTION (the new confluence contributor)")
    print(row("rejected the zone", [r for r in rows if r.get("retest_rejected")]))
    print(row("no rejection", [r for r in rows if not r.get("retest_rejected")]))

    # ── 5. Direction within the best filter ──
    best = [r for r in rows if LO(r) and AM(r)]
    header("DIRECTION, WITHIN low-conf + early")
    print(row("BUY", [r for r in best if r["direction"] == "BUY"]))
    print(row("SELL", [r for r in best if r["direction"] == "SELL"]))

    # ── 6. Reach curve for the surviving subset ──
    if best:
        print(f"\nREACH CURVE — low confluence + early session (n={len(best)})")
        for t in TARGETS:
            k = sum(1 for r in best if r["first_to"] >= t)
            need = 1.0 / (1.0 + t)
            mark = "  <-- clears" if (k / len(best)) > need else ""
            print(f"  {t:>5.1f}R : {k:>4}/{len(best)}  {k / len(best) * 100:>5.1f}%   "
                  f"(needs {need * 100:>4.1f}%){mark}")

    print("\nCaution: these splits were chosen AFTER seeing the data. Consistency")
    print("across symbols is the only guard here against fitting noise — treat a")
    print("filter that works on 5/5 differently from one that works on 3/5.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
