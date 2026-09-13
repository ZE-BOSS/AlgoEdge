#!/usr/bin/env python
"""
scripts/run_confluence_pool.py

The cross-market verdict on each confluence, for both strategies.

A per-market pick with 40-140 trades is mostly noise — the per-market tables in
run_vwap_research.py / run_apa_research.py show picks chosen on t = +0.3. What
carries signal is whether a confluence pays in the SAME DIRECTION across many
markets, and whether it still does in the window nobody selected on.

Reads every data/vwap_research/*.pkl and data/apa_research/*.pkl, then reports:

  1. Per confluence, pooled across all markets, per window: expectancy with the
     gate on, with it off, the trades it blocks, and the edge it adds.
  2. Per confluence, the MARKET COUNT — in how many markets it helped, so a
     result driven by one outlier market is visible as one.
  3. The R:R ladder pooled across markets (APA), and the exit-variant ladder
     (VWAP), so "does 1:10 work" is answered on thousands of trades.

    python scripts/run_confluence_pool.py
"""

from __future__ import annotations

import pickle
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.analytics import apa_research as apa  # noqa: E402
from backend.analytics import vwap_research as vw  # noqa: E402

VWAP_DIR = ROOT / "data" / "vwap_research"
APA_DIR = ROOT / "data" / "apa_research"


def _load(directory: Path) -> tuple[dict[str, list], dict]:
    per_market: dict[str, list] = {}
    windows: dict = {}
    for f in sorted(directory.glob("*.pkl")):
        with open(f, "rb") as fh:
            blob = pickle.load(fh)
        windows = blob.get("windows", windows)
        for k, v in blob.get("candidates", {}).items():
            if k.endswith("__pick") or not isinstance(v, list):
                continue
            per_market.setdefault(k, []).extend(v)
    return per_market, windows


def _fmt(s: dict) -> str:
    return f"{'-':>22}" if not s["n"] else f"{s['n']:>6} {s['avg_r']:+.3f}R {s['pf'] or 0:>5.2f} {s['t']:>+5.1f}"


def pool_report(name: str, per_market: dict[str, list], windows: dict, mod, key: str,
                setups: tuple[str, ...] | None = None) -> None:
    if not per_market:
        print(f"\n{name}: no saved research yet")
        return
    wins = {k: v for k, v in windows.items() if k != "pick"}
    print(f"\n{'=' * 100}\n{name}: {sum(len(v) for v in per_market.values())} candidates "
          f"across {len(per_market)} markets\n{'=' * 100}")

    for setup in (setups or (None,)):
        label = f" [{setup}]" if setup else ""
        pooled = []
        for cands in per_market.values():
            pooled.extend([c for c in cands if setup is None or getattr(c, "setup", None) == setup])
        if not pooled:
            continue
        print(f"\n-- pooled ablation{label}, exit {key} --")
        print(f"  {'confluence':<20}{'window':<10}{'with gate':>22}{'without':>22}{'blocked':>22}{'edge':>8}")
        for f in mod.CONFLUENCES:
            if not any(f in c.features for c in pooled):
                continue
            for wname, (lo, hi) in wins.items():
                sub = mod.in_window(pooled, lo, hi)
                on = mod.apply_combination(sub, use=(f,))
                off = mod.apply_combination(sub)
                blocked = [c for c in off if f in c.features and not c.features[f]]
                s_on, s_off, s_blk = mod.stats(on, key), mod.stats(off, key), mod.stats(blocked, key)
                edge = (None if s_on["avg_r"] is None or s_off["avg_r"] is None
                        else s_on["avg_r"] - s_off["avg_r"])
                print(f"  {f:<20}{wname:<10}{_fmt(s_on)}{_fmt(s_off)}{_fmt(s_blk)}"
                      f"{(f'{edge:+.3f}R' if edge is not None else '-'):>8}")

        # how many markets each confluence helped, in the unselected window
        last = list(wins)[-1]
        lo, hi = wins[last]
        print(f"\n-- markets helped{label} ({last}, exit {key}) --")
        for f in mod.CONFLUENCES:
            helped = tested = 0
            for cands in per_market.values():
                sub = [c for c in mod.in_window(cands, lo, hi)
                       if (setup is None or getattr(c, "setup", None) == setup) and f in c.features]
                if len(sub) < 20:
                    continue
                on = mod.stats(mod.apply_combination(sub, use=(f,)), key)
                off = mod.stats(mod.apply_combination(sub), key)
                if on["avg_r"] is None or off["avg_r"] is None:
                    continue
                tested += 1
                helped += int(on["avg_r"] > off["avg_r"])
            if tested:
                print(f"  {f:<20}{helped:>3} of {tested:<3} markets")


def ladder(name: str, per_market: dict[str, list], windows: dict, mod, keys: list[str],
           use: tuple[str, ...]) -> None:
    if not per_market:
        return
    wins = {k: v for k, v in windows.items() if k != "pick"}
    pooled = [c for cands in per_market.values() for c in cands]
    print(f"\n-- {name}: pooled exit ladder (gates: {', '.join(use) or 'none'}) --")
    print(f"  {'exit':<18}" + "".join(f"{w:>23}" for w in wins))
    for k in keys:
        row = f"  {k:<18}"
        for wname, (lo, hi) in wins.items():
            sub = mod.apply_combination(mod.in_window(pooled, lo, hi), use=use)
            row += f"{_fmt(mod.stats(sub, k)):>23}"
        print(row)


def main() -> int:
    vw_markets, vw_windows = _load(VWAP_DIR)
    apa_markets, apa_windows = _load(APA_DIR)

    pool_report("VWAP", vw_markets, vw_windows, vw, vw.variant_key(2.0, True, False),
                setups=("pullback", "reversion"))
    if vw_markets:
        ladder("VWAP", vw_markets, vw_windows, vw,
               [vw.variant_key(t, hc, be) for t, hc, be in vw.EXIT_VARIANTS],
               use=("in_session_window",))

    pool_report("APA", apa_markets, apa_windows, apa, apa.variant_key(3.0, True))
    if apa_markets:
        ladder("APA", apa_markets, apa_windows, apa,
               [apa.variant_key(t, he) for t, he in apa.EXIT_VARIANTS if he],
               use=("head_not_breached", "session_ok"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
