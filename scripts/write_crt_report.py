#!/usr/bin/env python
"""
scripts/write_crt_report.py

Turns the two `run_crt_study.py` result files into one markdown report.

Separate from the study so the report can be rewritten without re-running
twelve markets, and so the numbers in it are provably the numbers the study
produced rather than ones retyped by hand.

    python scripts/write_crt_report.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEBUG = ROOT / "debug"


def load(feed: str) -> dict | None:
    p = DEBUG / f"crt_study_{feed}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def row(name: str, s: dict) -> str:
    if s.get("thin"):
        return f"| {name} | {s['n']} | — | — | — | — | — | — | — |"
    return (f"| {name} | {s['n']:,} | {s['win_rate']:.1f}% | "
            f"{s['expectancy_r']:+.4f} | {s['t_stat']:+.2f} | "
            f"{s['profit_factor']:.2f} | {s['return_pct']:+,.1f}% | "
            f"{s['max_dd_pct']:.1f}% | {s['sharpe']:+.2f} |")


HEAD = ("| Variant | Trades | Win | Expectancy | t | PF | Return on $10k "
        "| Max DD | Sharpe |\n|---|---:|---:|---:|---:|---:|---:|---:|---:|")


def section(res: dict, prefix: str, title: str) -> str:
    runs = {k: v for k, v in res["runs"].items() if k.startswith(prefix)}
    if not runs:
        return ""
    out = [f"### {title}", "", HEAD]
    for k, v in runs.items():
        out.append(row(k.replace(prefix, "").strip(), v["pooled"]))
    return "\n".join(out) + "\n"


def best_of(res: dict, prefix: str) -> tuple[str, dict] | None:
    cands = [(k, v["pooled"]) for k, v in res["runs"].items()
             if k.startswith(prefix) and not v["pooled"].get("thin")]
    if not cands:
        return None
    return max(cands, key=lambda kv: kv[1]["expectancy_r"])


def main() -> None:
    deriv, fn = load("deriv"), load("fundednext")
    if deriv is None:
        print("run scripts/run_crt_study.py --feed deriv first")
        return

    parts: list[str] = []
    for feed, res in (("Deriv", deriv), ("FundedNext", fn)):
        if res is None:
            continue
        parts.append(f"## {feed}\n")
        parts.append(f"Markets: {', '.join(res['markets'])}\n")
        parts.append(section(res, "sweep ", "Model 1 — 4H manipulation sweep"))
        parts.append(section(res, "crt-ltf ", "Model 2 — 4H CRT, lower-timeframe entry"))
        parts.append(section(res, "crt-conf ", "Model 3 — the confluence stack"))

    out = DEBUG / "crt_report_tables.md"
    out.write_text("\n".join(parts), encoding="utf-8")
    print(f"wrote {out}")

    for feed, res in (("deriv", deriv), ("fundednext", fn)):
        if res is None:
            continue
        print(f"\n=== {feed}: best of each model by expectancy ===")
        for pref in ("sweep ", "crt-ltf ", "crt-conf "):
            b = best_of(res, pref)
            if b:
                k, s = b
                print(f"  {k:<52s} n={s['n']:>5d} win={s['win_rate']:5.1f}% "
                      f"E={s['expectancy_r']:+.4f}R t={s['t_stat']:+.2f} "
                      f"PF={s['profit_factor']:.2f}")


if __name__ == "__main__":
    main()
