#!/usr/bin/env python
"""
scripts/run_conditional_study.py — [P5.2 / P5.3]

Conditional-expectancy study on live MT5 data.

Population: every actionable forecast the free momentum baseline makes over the
full H1 history of each instrument, stepped by the horizon so trades on one
instrument never overlap. Each trade is labelled with the higher-timeframe state
observable when it was taken, and the study asks whether any state carries a
different expectancy — judged against the complement, against the family of
cells, and against time.

    python scripts/run_conditional_study.py
    python scripts/run_conditional_study.py --overlap-scope global
    python scripts/run_conditional_study.py --backtest-json Implementation/resources/backtest_run_cf9355c6.json

`--backtest-json` conditions an exported AlgoEdge backtest instead. The runs
exported so far span ten days, so expect TOO FEW almost everywhere — the output
says so rather than inventing a finding.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.analytics.conditional_expectancy import TradeObs, run_study  # noqa: E402
from backend.analytics.htf_regime import VARIABLES, HTFSeries, RegimeLabeler  # noqa: E402

DEFAULT_INSTRUMENTS = ["XAUUSD", "XAGUSD", "XPTUSD", "EURUSD", "GBPJPY", "BTCUSD",
                       "US Tech 100", "US SP 500"]
OUT_DIR = ROOT / "data" / "studies"


def fetch(symbol: str, counts: dict[str, int]) -> dict[str, dict] | None:
    import MetaTrader5 as mt5

    tfs = {"H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4, "D1": mt5.TIMEFRAME_D1}
    if mt5.symbol_info(symbol) is None and not mt5.symbol_select(symbol, True):
        return None
    out = {}
    for name, tf in tfs.items():
        r = mt5.copy_rates_from_pos(symbol, tf, 0, counts[name])
        if r is None or len(r) == 0:
            return None
        out[name] = {"time": r["time"], "open": r["open"], "high": r["high"],
                     "low": r["low"], "close": r["close"], "volume": r["tick_volume"]}
    return out


def labeler_for(bars: dict[str, dict]) -> RegimeLabeler:
    return RegimeLabeler(
        base=HTFSeries.from_rates(bars["H1"], 3600),
        h4=HTFSeries.from_rates(bars["H4"], 14400),
        d1=HTFSeries.from_rates(bars["D1"], 86400),
    )


async def baseline_population(symbols, counts, horizon, min_history):
    from backend.analytics.forecast_harness import walk_forward
    from backend.services.forecast_providers import BaselineProvider

    obs, labels = [], []
    for s in symbols:
        bars = fetch(s, counts)
        if bars is None:
            print(f"  {s}: unavailable — skipped")
            continue
        lab = labeler_for(bars)
        t = np.asarray(bars["H1"]["time"], dtype=np.int64)
        recs = await walk_forward(BaselineProvider(), {s: bars["H1"]},
                                  cutoff=datetime(2000, 1, 1, tzinfo=timezone.utc),
                                  horizon=horizon, min_history=min_history)
        n0 = len(obs)
        for r in recs:
            if r.r is None or r.direction == "FLAT":
                continue
            entry = int(t[r.decision_index + 1])  # fills at the next bar's open
            obs.append(TradeObs(s, float(entry), float(r.exit_time), float(r.r)))
            labels.append(lab.label(entry))
        print(f"  {s}: {len(recs)} forecasts, {len(obs) - n0} actionable")
    return obs, labels


def backtest_population(path: Path, counts):
    d = json.loads(path.read_text(encoding="utf-8"))
    trades = d.get("grouped_trades") or d.get("trades") or []
    obs, labels, labelers = [], [], {}
    for tr in trades:
        s = tr.get("symbol")
        risk = abs(float(tr["entry_price"]) - float(tr.get("initial_stop_loss", tr["stop_loss"])))
        if not s or risk <= 0 or tr.get("exit_time") is None:
            continue
        if s not in labelers:
            bars = fetch(s, counts)
            labelers[s] = labeler_for(bars) if bars else None
        if labelers[s] is None:
            continue
        long_ = str(tr.get("direction", "BUY")).upper().startswith("B")
        move = (float(tr["exit_price"]) - float(tr["entry_price"])) if long_ \
            else (float(tr["entry_price"]) - float(tr["exit_price"]))
        obs.append(TradeObs(s, float(tr["entry_time"]), float(tr["exit_time"]), move / risk))
        labels.append(labelers[s].label(int(tr["entry_time"])))
    return obs, labels


def print_study(title: str, res: dict) -> None:
    ov = res["overall"]
    print(f"\n{title}")
    print(f"  trades {res['n_trades']} | independent {ov.get('n_nonoverlap')} | "
          f"overall E[R] {ov.get('mean_r', 0):+.4f} | overall verdict {ov.get('verdict')}")
    pc = res["permutation_control"]
    if pc:
        print(f"  cells tested {res['n_cells_tested']} | family-wise critical |t| {res['critical_welch_t']:.2f} "
              f"| best observed |t| {pc['observed_max_abs_t']:.2f} "
              f"(null mean {pc['null_mean_max_abs_t']:.2f}, p={pc['p_value']:.3f})")
    # ASCII headers: a Windows console with a legacy code page prints "½" as "�".
    print(f"\n  {'cell':30} {'n':>5} {'indep':>6} {'E[R]':>8} {'rest':>8} {'welch t':>8} "
          f"{'1st-half':>8} {'2nd-half':>8}  verdict")
    for c in res["cells"]:
        name = f"{c['variable']}={c['value']}"
        f = lambda v: "   —" if v is None else f"{v:+.3f}"  # noqa: E731
        print(f"  {name:30} {c['n']:>5} {c['n_independent']:>6} {f(c['mean_r']):>8} "
              f"{f(c['complement_mean_r']):>8} {c['welch_t']:>+8.2f} {f(c['first_half_mean_r']):>7} "
              f"{f(c['second_half_mean_r']):>7}  {c['verdict']}")
    print(f"\n  HEADLINE: {res['headline']}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--instruments", nargs="+", default=DEFAULT_INSTRUMENTS)
    ap.add_argument("--h1-bars", type=int, default=5000)
    ap.add_argument("--horizon", type=int, default=24)
    ap.add_argument("--min-history", type=int, default=150)
    ap.add_argument("--min-cell-n", type=int, default=30)
    ap.add_argument("--permutations", type=int, default=200)
    ap.add_argument("--overlap-scope", choices=["per_instrument", "global"], default="per_instrument")
    ap.add_argument("--backtest-json", nargs="*", default=None)
    args = ap.parse_args()

    import MetaTrader5 as mt5
    if not mt5.initialize():
        print(f"MT5 is not available on this machine: {mt5.last_error()}")
        return 1
    counts = {"H1": args.h1_bars, "H4": max(600, args.h1_bars // 3), "D1": 1500}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    try:
        if args.backtest_json:
            for p in args.backtest_json:
                obs, labels = backtest_population(Path(p), counts)
                res = run_study(obs, labels, variables=VARIABLES, min_cell_n=args.min_cell_n,
                                overlap_scope=args.overlap_scope, n_permutations=args.permutations)
                print_study(f"BACKTEST {Path(p).name}", res)
                (OUT_DIR / f"conditional-{Path(p).stem}-{stamp}.json").write_text(
                    json.dumps(res, indent=2, default=str), encoding="utf-8")
        else:
            print("building the baseline population ...")
            obs, labels = asyncio.run(baseline_population(args.instruments, counts,
                                                          args.horizon, args.min_history))
            res = run_study(obs, labels, variables=VARIABLES, min_cell_n=args.min_cell_n,
                            overlap_scope=args.overlap_scope, n_permutations=args.permutations)
            print_study(f"BASELINE MOMENTUM x HTF STATE ({args.overlap_scope})", res)
            out = OUT_DIR / f"conditional-baseline-{args.overlap_scope}-{stamp}.json"
            out.write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
            print(f"\nwritten: {out}")
    finally:
        mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
