#!/usr/bin/env python
"""
scripts/run_crt_addendum.py

Three specific claims the main study does not answer.

1. "THIS IS HOPEFULLY GOING TO GIVE YOU THE LOWEST POINT OF THE DAY"
   A falsifiable sentence. How often is the sweep candle's low actually the
   low of its day? Measured against the base rate — any 4-hour candle has a
   1-in-6 chance of holding the day's extreme by luck alone, so the filter
   has to beat 16.7% to mean anything.

2. THE "PURGE DURATION (MINUTES): 90" SETTING
   The settings screenshot sets it to 90. The video never says what it does,
   but the indicator paints a purge window after the CRT candle, so the
   natural reading is that a sweep only counts inside it. Tested as a time
   filter at several durations against no filter at all.

3. WHERE A 82% WIN RATE COULD LIVE
   Claimed for the CRT model. A win rate is not a free parameter — it is set
   by the target. So the same rule is run at targets from 1:0.25 upwards, and
   the report says what target 82% needs and what the expectancy is there.
   A high win rate bought with a tiny target is not an edge, and this is the
   arithmetic that shows it.

    python scripts/run_crt_addendum.py --feed deriv
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import zone_money_engine as eng  # noqa: E402
from scripts.run_crt_study import (DERIV, FUNDEDNEXT, MIN_STOP_SPREADS,  # noqa: E402
                                   SLIP_POINTS, aggregate, buckets,
                                   crt_ltf_trades, crt_windows, drop_overlaps,
                                   stats, sweep_trades, walk)
from scripts.run_published_strategies import FEEDS, load  # noqa: E402


def lowest_point_of_day(b: dict, engulf: bool) -> dict:
    """How often the sweep candle holds the day's extreme, vs. the base rate."""
    h4 = aggregate(b, buckets(b["time"], 240))
    o, hi, lo, c, t = h4["open"], h4["high"], h4["low"], h4["close"], h4["t"]
    day = (t // 86400).astype(np.int64)
    day_lo: dict[int, float] = {}
    day_hi: dict[int, float] = {}
    for d in np.unique(day):
        m = day == d
        day_lo[int(d)] = float(lo[m].min())
        day_hi[int(d)] = float(hi[m].max())

    hits = total = 0
    for i in range(1, len(c)):
        body_hi, body_lo = max(o[i - 1], c[i - 1]), min(o[i - 1], c[i - 1])
        bull = lo[i] < lo[i - 1] and c[i] > o[i] and (not engulf or c[i] > body_hi)
        bear = hi[i] > hi[i - 1] and c[i] < o[i] and (not engulf or c[i] < body_lo)
        if bull == bear:
            continue
        total += 1
        d = int(day[i])
        if bull and lo[i] <= day_lo[d] + 1e-12:
            hits += 1
        elif bear and hi[i] >= day_hi[d] - 1e-12:
            hits += 1
    # the base rate: a 4-hour candle picked at random from its own day
    per_day = np.bincount(day - day.min())
    per_day = per_day[per_day > 0]
    base = float(np.mean(1.0 / per_day))
    return {"signals": total, "held_extreme": hits,
            "rate": hits / total if total else 0.0, "base_rate": base}


def purge_filtered(b: dict, sym: str, anchor_hour: int, purge_min: int | None) -> list[dict]:
    """Model 2, but the sweep must happen within `purge_min` of the CRT close."""
    spread_px = b["spread_pts"] * b["point"]
    slip = SLIP_POINTS * b["point"]
    o, h, lo, c, t = b["open"], b["high"], b["low"], b["close"], b["time"]
    out = []
    for c0, c1, day_end in crt_windows(b, anchor_hour):
        crt_hi, crt_lo = h[c0:c1 + 1].max(), lo[c0:c1 + 1].min()
        if crt_hi <= crt_lo:
            continue
        deadline = t[c1] + (purge_min or 10_000) * 60
        swept, j = None, c1 + 1
        while j <= day_end - 3:
            if swept is None:
                if t[j] > deadline:
                    break
                if lo[j] < crt_lo:
                    swept = "low"
                elif h[j] > crt_hi:
                    swept = "high"
                j += 1
                continue
            k = min(j + 2, day_end)
            bull = swept == "low"
            if not ((c[k] > crt_lo) if bull else (c[k] < crt_hi)):
                j += 3
                continue
            entry_i = k + 1
            if entry_i > day_end:
                break
            entry = o[entry_i]
            stop = lo[j:k + 1].min() if bull else h[j:k + 1].max()
            risk = abs(entry - stop)
            if risk <= 0 or risk < MIN_STOP_SPREADS * spread_px[entry_i]:
                break
            tgt = crt_hi if bull else crt_lo
            if (bull and tgt <= entry) or (not bull and tgt >= entry):
                break
            gross, exit_i = walk(b, entry_i, day_end, bull, entry, stop, tgt)
            out.append({"t": int(t[entry_i]), "symbol": sym,
                        "r": (gross - spread_px[entry_i] - slip) / risk,
                        "stop_distance": risk, "exit_t": int(t[exit_i]),
                        "dir": "long" if bull else "short"})
            break
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--feed", default="deriv", choices=list(FEEDS))
    args = ap.parse_args()
    bars_dir, specs_path = FEEDS[args.feed]
    eng.SPECS = json.loads(Path(specs_path).read_text(encoding="utf-8"))
    markets = [m for m in (DERIV if args.feed == "deriv" else FUNDEDNEXT)
               if (bars_dir / f"{m}.npz").exists() and m in eng.SPECS]
    data = {m: load(bars_dir, m) for m in markets}
    res: dict = {"feed": args.feed}

    # -- 1. the lowest point of the day -----------------------------------
    print('\n-- "the lowest point of the day" --')
    print("  (a 4H candle holds its day's extreme ~1 time in 6 by luck)")
    res["lowest_point"] = {}
    for engulf in (False, True):
        tag = "v2-engulf" if engulf else "v1-plain"
        agg = {"signals": 0, "held_extreme": 0, "base": []}
        for s, b in data.items():
            r = lowest_point_of_day(b, engulf)
            agg["signals"] += r["signals"]
            agg["held_extreme"] += r["held_extreme"]
            agg["base"].append(r["base_rate"])
        rate = agg["held_extreme"] / max(1, agg["signals"])
        base = float(np.mean(agg["base"]))
        res["lowest_point"][tag] = {"rate": rate, "base_rate": base,
                                    "signals": agg["signals"]}
        print(f"  {tag:<10s} {agg['held_extreme']:,} of {agg['signals']:,} "
              f"= {rate*100:5.1f}%   base rate {base*100:.1f}%   "
              f"lift {rate/base:.2f}x")

    # -- 2. the 90-minute purge window ------------------------------------
    print("\n-- the Purge Duration setting, as a time filter --")
    res["purge"] = {}
    for pm in (30, 60, 90, 120, 240, None):
        name = "no limit" if pm is None else f"{pm} min"
        per = {s: purge_filtered(b, s, 5, pm) for s, b in data.items()}
        st = stats(drop_overlaps([t for v in per.values() for t in v]),
                   f"purge={name}")
        res["purge"][name] = st
        if st.get("thin"):
            print(f"  purge {name:<9s} only {st['n']} trades")
        else:
            print(f"  purge {name:<9s} n={st['n']:>5d} win={st['win_rate']:5.1f}% "
                  f"E={st['expectancy_r']:+.4f}R t={st['t_stat']:+5.2f} "
                  f"PF={st['profit_factor']:.2f}")

    # -- 3. what target an 82% win rate would need -------------------------
    print("\n-- the 82% win rate, against the target that would buy it --")
    res["win_rate_curve"] = {}
    for tr in (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0):
        per = {s: crt_ltf_trades(b, s, anchor_hour=5, target_r=tr,
                                 confirm_tf_min=15) for s, b in data.items()}
        st = stats(drop_overlaps([t for v in per.values() for t in v]),
                   f"crt-ltf 1:{tr:g}")
        res["win_rate_curve"][f"1:{tr:g}"] = st
        if st.get("thin"):
            print(f"  target 1:{tr:<5g} only {st['n']} trades")
        else:
            mark = "  <-- 82%" if abs(st["win_rate"] - 82) < 4 else ""
            print(f"  target 1:{tr:<5g} n={st['n']:>5d} win={st['win_rate']:5.1f}% "
                  f"E={st['expectancy_r']:+.4f}R PF={st['profit_factor']:.2f} "
                  f"ret={st['return_pct']:+8.1f}%{mark}")

    out = ROOT / "debug" / f"crt_addendum_{args.feed}.json"
    out.write_text(json.dumps(res, indent=1, default=float), encoding="utf-8")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
