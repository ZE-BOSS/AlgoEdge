#!/usr/bin/env python
"""
scripts/run_forecast_test.py — [P3.10 / P3.11]

The post-cutoff forecasting test from the command line. Needs a connected MT5
terminal on this machine for bars; results and the hash-chained journal land in
data/forecast_runs/.

    # free — validates the entire pipeline with the momentum baseline
    python scripts/run_forecast_test.py

    # price a paid model WITHOUT spending anything
    python scripts/run_forecast_test.py --provider anthropic:claude-haiku-4-5 \
        --max-forecasts 25 --surrogates 3 --surrogate-instruments XAUUSD EURUSD BTCUSD --dry-run

    # run it; nothing is spent if the plan exceeds the ceiling
    python scripts/run_forecast_test.py --provider anthropic:claude-haiku-4-5 \
        --max-forecasts 25 --surrogates 3 --surrogate-instruments XAUUSD EURUSD BTCUSD --max-cost 2.00

Bar times come from MT5 as epoch seconds in server time. Deriv's server runs on
GMT, so they are treated as UTC; a broker on a different server clock shifts the
cutoff by that offset.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.analytics.forecast_harness import (  # noqa: E402
    DEFAULT_POST_CUTOFF,
    CostCeilingExceeded,
    ProviderUnavailable,
    plan_run,
    run_test,
)
from backend.services.forecast_providers import AnthropicProvider, build_provider  # noqa: E402

DEFAULT_INSTRUMENTS = ["XAUUSD", "XAGUSD", "XPTUSD", "EURUSD", "GBPJPY", "BTCUSD",
                       "US Tech 100", "US SP 500"]
TF_MINUTES = {"M15": 15, "H1": 60, "H4": 240}
OUT_DIR = ROOT / "data" / "forecast_runs"


def fetch_bars(symbols: list[str], timeframe: str, cutoff: datetime, lookback: int) -> dict:
    import MetaTrader5 as mt5

    tf = {"M15": mt5.TIMEFRAME_M15, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4}[timeframe]
    if not mt5.initialize():
        raise SystemExit(f"MT5 is not available on this machine: {mt5.last_error()}")
    # x3 covers weekends and holidays so the first post-cutoff decision has its full lookback
    start = cutoff - timedelta(minutes=TF_MINUTES[timeframe] * lookback * 3)
    end = datetime.now(timezone.utc)
    data = {}
    try:
        for s in symbols:
            if mt5.symbol_info(s) is None and not mt5.symbol_select(s, True):
                print(f"  {s}: not available from this broker — skipped")
                continue
            r = mt5.copy_rates_range(s, tf, start, end)
            if r is None or len(r) == 0:
                print(f"  {s}: no bars returned — skipped")
                continue
            data[s] = {"time": r["time"], "open": r["open"], "high": r["high"],
                       "low": r["low"], "close": r["close"], "volume": r["tick_volume"]}
    finally:
        mt5.shutdown()
    return data


def print_plan(provider, plan: dict, args) -> None:
    print(f"\nPLAN — {provider.name}, {args.timeframe}, cutoff {args.cutoff}, horizon {args.horizon} bars")
    for s, n in plan["decisions_per_instrument"].items():
        print(f"  {s:16} {n:>4} decisions")
    print(f"  real calls {plan['real_calls']}  +  surrogate calls {plan['surrogate_calls']}"
          f"  =  {plan['total_calls']}")
    if provider.costs_money:
        print(f"  est. cost ${plan['est_cost_usd']:.2f}  (${plan['per_call_usd']:.4f}/call; {plan['note']})")
        print("\n  same plan on other models, for comparison:")
        for m in ("claude-haiku-4-5", "claude-sonnet-5", "claude-opus-5"):
            unit = AnthropicProvider(model=m).estimate_call_cost_usd()
            print(f"    {m:18} ${unit * plan['total_calls']:>8.2f}")


def print_result(res) -> None:
    print(f"\nRESULT — {res.run_id}")
    print(f"{'instrument':16} {'fcsts':>6} {'ops':>4} {'acted':>6} {'abstain':>8} {'E[R]':>9} {'win%':>6}")
    for s, v in res.per_instrument.items():
        e = f"{v['expectancy_r']:+.4f}" if v["expectancy_r"] is not None else "—"
        w = f"{v['win_rate'] * 100:.1f}" if v["win_rate"] is not None else "—"
        a = f"{v['abstain_rate'] * 100:.1f}%" if v["abstain_rate"] is not None else "—"
        print(f"{s:16} {v['forecasts']:>6} {v['operational_failures']:>4} {v['actionable']:>6} "
              f"{a:>8} {e:>9} {w:>6}")
    sc = res.score
    print(f"\npooled: {sc['n_actionable']} actionable of {sc['n_forecasts']} | "
          f"E[R] {sc['expectancy_r']:+.4f} | win {sc['win_rate'] * 100:.1f}% | "
          f"Brier skill {sc['brier_skill']} | calibration error {sc['calibration_error']}")
    for label, sig in (("significance (conservative)", res.significance),
                       ("significance (independent)", res.significance_independent)):
        print(f"{label:28} n={sig['n']} indep={sig['n_nonoverlap']} "
              f"t={sig['t_nonoverlap']:+.2f} -> {sig['verdict']}")
    sur = res.surrogate
    if sur:
        if sur.get("ran") and sur.get("p_value") == sur.get("p_value"):
            print(f"surrogate control ({sur['mode']}, {sur['method']}): real {sur['real_expectancy']:+.4f} "
                  f"vs surrogate mean {sur['surrogate_mean']:+.4f}, p95 {sur['surrogate_p95']:+.4f}, "
                  f"p={sur['p_value']:.3f}")
        else:
            print(f"surrogate control: {sur.get('note')}")
    print(f"\nVERDICT: {res.verdict['verdict']}")
    for r in res.verdict["reasons"]:
        print(f"  - {r}")
    print(f"\ncost: {res.cost}")
    if res.journal:
        print(f"journal: {res.journal}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", default="baseline",
                    help="baseline | anthropic:<model> | anthropic:<model>:plain")
    ap.add_argument("--instruments", nargs="+", default=DEFAULT_INSTRUMENTS)
    ap.add_argument("--timeframe", default="H1", choices=sorted(TF_MINUTES))
    ap.add_argument("--cutoff", default=DEFAULT_POST_CUTOFF.date().isoformat(),
                    help="first decision date (UTC); must be after the model's knowledge cutoff")
    ap.add_argument("--horizon", type=int, default=24)
    ap.add_argument("--lookback", type=int, default=400)
    ap.add_argument("--max-forecasts", type=int, default=None, help="per instrument")
    ap.add_argument("--surrogates", type=int, default=None,
                    help="surrogate series (default 30 for baseline, 3 for paid providers)")
    ap.add_argument("--surrogate-mode", choices=["full", "bootstrap"], default=None)
    ap.add_argument("--surrogate-instruments", nargs="+", default=None)
    ap.add_argument("--max-cost", type=float, default=0.0, help="USD ceiling; the run refuses above it")
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--n-trials", type=int, default=1,
                    help="configurations compared to arrive at this one (data-mining deflation)")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and price, call nothing")
    args = ap.parse_args()

    cutoff = datetime.fromisoformat(args.cutoff).replace(tzinfo=timezone.utc)
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except Exception:
        pass

    provider = build_provider(args.provider, cache_dir=ROOT / "data" / "forecast_cache")
    n_sur = args.surrogates if args.surrogates is not None else (3 if provider.costs_money else 30)

    print(f"fetching {args.timeframe} bars from MT5 ...")
    data = fetch_bars(args.instruments, args.timeframe, cutoff, args.lookback)
    if not data:
        print("no data — nothing to test")
        return 1

    ctrl = args.surrogate_instruments or list(data)
    plan = plan_run(provider, data, cutoff=cutoff, horizon=args.horizon,
                    max_forecasts_per_instrument=args.max_forecasts,
                    n_surrogates=n_sur, surrogate_instruments=ctrl)
    print_plan(provider, plan, args)
    if args.dry_run:
        print("\n(dry run — nothing was called)")
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    journal = OUT_DIR / f"{provider.name.replace(':', '_')}-{stamp}.journal.jsonl"
    try:
        res = asyncio.run(run_test(
            provider, data, timeframe=args.timeframe, cutoff=cutoff, horizon=args.horizon,
            lookback=args.lookback, max_forecasts_per_instrument=args.max_forecasts,
            n_surrogates=n_sur, surrogate_mode=args.surrogate_mode,
            surrogate_instruments=ctrl, max_cost_usd=args.max_cost, journal_path=journal,
            n_trials=args.n_trials, concurrency=args.concurrency,
        ))
    except CostCeilingExceeded as e:
        print(f"\nREFUSED, nothing spent: {e}")
        return 2
    except ProviderUnavailable as e:
        print(f"\nSTOPPED: {e}")
        return 3

    out = OUT_DIR / f"{res.run_id}.result.json"
    out.write_text(json.dumps(res.to_dict(), indent=2, default=str), encoding="utf-8")
    print_result(res)
    print(f"\nwritten: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
