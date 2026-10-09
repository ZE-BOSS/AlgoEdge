"""
scripts/run_rl_gate.py

Train an agent on real bars, distil its charter, and run the section 4.6 gate.
Reports whatever happened, pass or fail.

WHY THIS IS A SCRIPT AND NOT A TEST
-----------------------------------
It is an experiment, not an assertion. The tests check that the gate REJECTS
the things it should reject; this runs a real agent through it on real data and
prints the answer. A test that asserted "the agent passes" would be a test that
has to be deleted the first time the market disagrees.

COMPUTE, HONESTLY
-----------------
Five seeds times eight purged paths is forty trainings per symbol, and each one
is tens of engine runs. That is the constraint the plan states and no amount of
arranging compresses it: `--iterations` and `--window` are the two dials, and
smaller values buy a weaker claim rather than a faster one.

    py -3.12 scripts/run_rl_gate.py --symbol "Crash 1000 Index" --iterations 10
    py -3.12 scripts/run_rl_gate.py --timing          # size the job first
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ARCHIVE = Path("data/deriv_new")


def load(symbol: str, timeframe: str = "M5") -> pd.DataFrame:
    """Real Deriv bars from the archive this project already keeps.

    `spread_points` is carried through as `spread`, which is the column the
    engine reads for the per-bar spread. Dropping it would price every trade at
    the asset-class average instead of what the broker actually quoted - and on
    Crash 1000 that average is nowhere near the quote.
    """
    path = ARCHIVE / f"{symbol.replace(' ', '_').replace('(', '').replace(')', '')}_{timeframe}.npz"
    if not path.exists():
        candidates = sorted(p.name for p in ARCHIVE.glob(f"*{timeframe}.npz"))
        raise SystemExit(f"no archive at {path}\navailable:\n  " + "\n  ".join(candidates))
    with np.load(path, allow_pickle=False) as z:
        cols = {k: z[k] for k in z.files}
    frame = pd.DataFrame({
        "time": cols["time"].astype("int64"),
        "open": cols["open"], "high": cols["high"], "low": cols["low"],
        "close": cols["close"],
        "volume": cols.get("tick_volume", np.zeros(len(cols["time"]))),
    })
    if "spread_points" in cols:
        frame["spread"] = cols["spread_points"]
    return frame


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="Crash 1000 Index")
    ap.add_argument("--timeframe", default="M5")
    ap.add_argument("--iterations", type=int, default=10)
    ap.add_argument("--window", type=int, default=8000)
    ap.add_argument("--paths", type=int, default=8)
    ap.add_argument("--exit-tries", type=int, default=2)
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--risk", type=float, default=0.5)
    ap.add_argument("--optimiser", default="ppo", choices=["ppo", "cem"])
    ap.add_argument("--timing", action="store_true",
                    help="measure one rollout and estimate the whole job")
    ap.add_argument("--out", default="data/rl/gate_report.json")
    args = ap.parse_args()

    from backend.rl import distill
    from backend.rl import env as E
    from backend.rl import features as F
    from backend.rl import policy as pol
    from backend.rl import train as T
    from backend.rl import validate as V
    from backend.rl.spec import AgentSpec

    frame = load(args.symbol, args.timeframe)
    print(f"{args.symbol} {args.timeframe}: {len(frame):,} bars  "
          f"{pd.Timestamp(int(frame['time'].iloc[0]), unit='s')} -> "
          f"{pd.Timestamp(int(frame['time'].iloc[-1]), unit='s')}")

    spec = AgentSpec(
        name=f"gate-{args.symbol.split()[0].lower()}",
        symbols=(args.symbol,), timeframe=args.timeframe,
        seeds=tuple(range(1, args.seeds + 1)),
        iterations=args.iterations, eval_windows=1, eval_window_bars=args.window,
        max_cpcv_paths=args.paths, exit_head=True, exit_tries=args.exit_tries,
        attempted_symbols=(args.symbol,),
    )
    data = E.EpisodeData.build(args.symbol, args.timeframe, frame, spec.features)
    cfg = E.default_risk_config(args.symbol, risk_per_trade_pct=args.risk)
    folds = E.cpcv_folds(len(data), spec.cpcv_groups, spec.cpcv_test_groups,
                         spec.embargo_bars)[:spec.max_cpcv_paths]
    print(f"spec {spec.content_hash} · {len(spec.seeds)} seeds · {len(folds)} purged paths "
          f"· embargo {spec.embargo_bars} bars · optimiser {args.optimiser}")

    if args.timing:
        scaler = F.Scaler().fit(data.X[:args.window], data.names)
        env = E.TradingEnv(spec, data.slice(0, args.window), cfg, scaler=scaler)
        p = pol.LinearPolicy(n_features=spec.n_features, exit_head=True)
        p.from_flat(np.random.default_rng(1).normal(0, 0.5, p.n_params))
        t0 = time.time()
        ep = env.rollout(p)
        one = time.time() - t0
        per_fold = one * args.iterations * (2 + args.exit_tries)
        print(f"one rollout over {args.window:,} bars: {one:.2f}s "
              f"({ep.n_signals} signals, {ep.breakdown.n_trades} trades)")
        print(f"estimate: {per_fold / 60:.1f} min per fold, "
              f"{per_fold * len(folds) * len(spec.seeds) / 3600:.1f} h for the whole gate")
        return 0

    t0 = time.time()
    run = T.train_agent(spec, {args.symbol: frame}, cfg,
                        agent_id=spec.name, optimiser=args.optimiser)
    print(f"\ntrained in {run.seconds / 60:.1f} min · {len(run.results)} "
          f"(seed, path) results · Sharpe dispersion "
          f"{'n/a' if run.sharpe_dispersion == float('inf') else f'{run.sharpe_dispersion:.3f}'}")
    if run.best is None:
        print("no result at all - nothing to validate")
        return 1

    policy = pol.LinearPolicy.from_dict(run.best.policy)
    scaler = F.Scaler.from_dict(run.best.scaler)
    print(f"best: seed {run.best.seed} path {run.best.fold} · "
          f"{run.best.test_trades} test trades · {run.best.test_mean_r:+.4f}R")

    env = E.TradingEnv(spec, data, cfg, scaler=scaler)
    ep = env.rollout(policy, transitions=True)
    charter = distill.distil(
        policy, env.Z, data.names, spec, agent_id=spec.name,
        trade_bars=distill.trade_bars_from([ep]),
        n_trades=ep.breakdown.n_trades, mean_r=ep.breakdown.mean_r or None,
        scaler_note=scaler.fitted_on)
    print("\n" + "=" * 78)
    print(charter.english())
    print("=" * 78 + "\n")

    report = V.validate(spec, policy, scaler, data, cfg, agent_id=spec.name,
                        training_run=run, charter=charter, optimiser=run.optimiser)
    print(report.text())
    print(f"\ntotal {(time.time() - t0) / 60:.1f} min")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "spec": spec.to_dict(), "training": run.to_dict(),
        "charter": charter.to_dict(), "report": report.to_dict(),
    }, indent=2, default=str), encoding="utf-8")
    print(f"written to {out}")
    return 0 if report.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
