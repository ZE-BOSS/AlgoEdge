#!/usr/bin/env python
"""
scripts/run_playbook_matrix.py

Backtest a strategy x symbol matrix on a named MT5 terminal, RESUMABLY.

WHY RESUMABLE
-------------
Each cell fetches and scans nine months of M5 bars, so a full matrix runs for
hours. A first attempt piped its output through `grep` and was killed at a time
limit with the pipe still buffered: eighteen completed backtests, nothing
written, all of it lost.

So every cell is appended to a JSONL file the moment it finishes, and a cell
already in that file is skipped on the next run. Stopping it costs the cell in
flight and nothing else.

WHY --terminal IS NOT OPTIONAL
------------------------------
MT5 attaches to whichever terminal was used last, and it DRIFTS mid-session.
A FundedNext symbol requested while attached to Deriv does not report "unknown
symbol" -- it reports `(-1, 'Terminal: Call failed')`, which reads as a dead
terminal. Worse, a symbol that exists on both returns the WRONG broker's bars
with no error at all. Pinning the terminal is what makes a two-broker
comparison mean anything.

    py -3.12 scripts/run_playbook_matrix.py --terminal fundednext \\
        --symbols NDX100 SPX500 US30 --strategies OvernightSession_v1 ORB_v1
    py -3.12 scripts/run_playbook_matrix.py --terminal fundednext --report
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "playbook"

SYMBOLS = {
    "fundednext": ["NDX100", "SPX500", "US30", "XAUUSD", "GBPJPY", "EURJPY"],
    "deriv": ["US Tech 100", "US SP 500", "XAUUSD", "GBPJPY", "EURJPY"],
}
STRATEGIES = ["OvernightSession_v1", "ORB_v1", "VWAP_v1"]


def done_keys(path: Path) -> set[tuple[str, str, str]]:
    if not path.exists():
        return set()
    out = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
            out.add((r["terminal"], r["symbol"], r["strategy"], r.get("mode", "flat")))
        except Exception:
            continue
    return out


def run_cell(terminal, symbol, strategy, args, mode) -> dict:
    cmd = [sys.executable, str(ROOT / "scripts" / "run_app_backtest.py"),
           "--terminal", terminal, "--strategy", strategy, "--symbol", symbol,
           "--start", args.start, "--end", args.end,
           "--balance", str(args.balance), "--risk", str(args.risk),
           "--sizing-basis", "STATIC" if mode == "flat" else "BALANCE"]
    if mode == "prop":
        # prop-firm rules on: FundedNext's daily and overall loss limits
        cmd += ["--daily-dd", "5", "--weekly-dd", "10"]
    else:
        cmd += ["--daily-dd", "100", "--weekly-dd", "100"]
    env = {**os.environ, "PYTHONPATH": f"{ROOT / 'venv' / 'Lib' / 'site-packages'};{ROOT}"}
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, env=env,
                           cwd=str(ROOT), timeout=args.cell_timeout)
        res = json.loads(p.stdout)
        res["ok"] = True
    except Exception as exc:
        res = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    res.update(terminal=terminal, symbol=symbol, strategy=strategy, mode=mode,
               seconds=round(time.time() - t0, 1))
    return res


def report(path: Path) -> None:
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    ok = [r for r in rows if r.get("ok") and r.get("trades")]
    if not ok:
        print("no completed cells yet"); return
    hdr = (f"{'terminal':<11}{'symbol':<14}{'strategy':<21}{'mode':<6}{'n':>5}{'WR':>6}"
           f"{'P&L $':>11}{'ret%':>8}{'DD%':>7}{'PF':>7}{'expR':>8}")
    print(hdr); print("-" * len(hdr))
    for r in sorted(ok, key=lambda x: -(x.get("net_pnl") or -1e18)):
        bal = r.get("balance") or 10000.0
        pnl = r.get("net_pnl") or 0.0
        print(f"{r['terminal']:<11}{r['symbol']:<14}{r['strategy']:<21}{r['mode']:<6}"
              f"{r.get('trades', 0):>5}{(r.get('win_rate') or 0) * 100:>5.0f}%"
              f"{pnl:>11,.0f}{pnl / bal * 100:>7.1f}%{(r.get('max_dd_pct') or 0):>6.1f}%"
              f"{(r.get('profit_factor') or 0):>7.2f}{(r.get('expectancy_r') or 0):>+8.3f}")
    bad = [r for r in rows if not r.get("ok")]
    if bad:
        print(f"\n{len(bad)} cell(s) failed:")
        for r in bad[:8]:
            print(f"  {r['symbol']:<14}{r['strategy']:<21}{r.get('error', '')[:80]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--terminal", required=True, choices=["deriv", "fundednext"])
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--strategies", nargs="*", default=STRATEGIES)
    ap.add_argument("--modes", nargs="*", default=["flat"],
                    choices=["flat", "compounded", "prop"])
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--end", default="2026-10-04")
    ap.add_argument("--balance", type=float, default=10_000.0)
    ap.add_argument("--risk", type=float, default=1.0)
    ap.add_argument("--cell-timeout", type=int, default=900)
    ap.add_argument("--budget", type=int, default=1500,
                    help="stop starting new cells after this many seconds")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "results.jsonl"
    if args.report:
        report(path); return

    done = done_keys(path)
    symbols = args.symbols or SYMBOLS[args.terminal]
    started = time.time()
    for mode in args.modes:
        for sym in symbols:
            for strat in args.strategies:
                key = (args.terminal, sym, strat, mode)
                if key in done:
                    print(f"  skip  {sym:<14}{strat:<21}{mode}", flush=True)
                    continue
                if time.time() - started > args.budget:
                    print(f"\nbudget reached; {len(done)} cells on disk. "
                          f"Run again to continue.", flush=True)
                    return
                r = run_cell(args.terminal, sym, strat, args, mode)
                # append IMMEDIATELY -- a cell that finished must survive a kill
                with path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(r, default=str) + "\n")
                done.add(key)
                if r.get("ok"):
                    print(f"  done  {sym:<14}{strat:<21}{mode:<6} "
                          f"{r.get('trades', 0):>5} trades  "
                          f"${r.get('net_pnl') or 0:>10,.0f}  ({r['seconds']:.0f}s)", flush=True)
                else:
                    print(f"  FAIL  {sym:<14}{strat:<21}{mode:<6} {r['error'][:70]}", flush=True)
    print(f"\n-> {path}")


if __name__ == "__main__":
    main()
