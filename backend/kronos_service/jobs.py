"""
backend/kronos_service/jobs.py

Kronos work as JOBS, each in its own process:

    python -m backend.kronos_service.jobs data/kronos/jobs/<id>.json

The API (routes/kronos.py) writes the job file and spawns this at Windows
BelowNormal priority with `.kronos_deps` on PYTHONPATH, so torch is never
loaded into the trading backend and a long precompute cannot take CPU from
MT5 execution. This process writes its progress back into the same file
(atomically), which is all the page polls.

Kinds:
  forecast    the latest bars -> every sampled path + features, per symbol
  precompute  walk-forward features over a date range (what KRONOS_v1 and the
              gate read in a backtest)
  skill       forecast skill vs momentum / always-long, net of spread
  strategy    KRONOS_v1's rule through the app engine vs random direction
  benchmark   latency per model on this machine
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

JOB_DIR = Path("data/kronos/jobs")


class Job:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.d = json.loads(self.path.read_text(encoding="utf-8"))

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.d, default=str), encoding="utf-8")
        # On Windows os.replace fails while the API has the file open for a
        # poll; a progress write must never be what kills a 40-minute job.
        for attempt in range(20):
            try:
                os.replace(tmp, self.path)
                return
            except PermissionError:
                time.sleep(0.05 * (attempt + 1))
        # Still locked: this one progress update is skipped, the next will land.

    def set(self, **kw) -> None:
        self.d.update(kw)
        self.d["updated_at"] = time.time()
        self.save()

    def event(self, text: str) -> None:
        ev = self.d.setdefault("events", [])
        ev.append({"t": time.time(), "text": text})
        del ev[:-200]
        self.set()

    def cancelled(self) -> bool:
        try:
            return json.loads(self.path.read_text(encoding="utf-8")).get("cancel_requested", False)
        except Exception:                               # noqa: BLE001
            return False


def _cfg(p: dict):
    from backend.kronos_service.config import KronosConfig
    return KronosConfig(model=p.get("model", "Kronos-mini"), lookback=int(p.get("lookback", 512)),
                        pred_len=int(p.get("pred_len", 12)), sample_count=int(p.get("samples", 5)),
                        threads=int(p.get("threads", 1)))


def _engine(job: Job, cfg):
    job.set(stage="loading model", pct=2.0)
    from backend.kronos_service.predictor import KronosEngine
    eng = KronosEngine(cfg)
    job.event(f"loaded {cfg.model} ({cfg.model_id})")
    return eng


def _progress(job: Job, lo: float, hi: float, label: str):
    def cb(done: int, total: int) -> None:
        if job.cancelled():
            raise KeyboardInterrupt("cancelled")
        job.set(pct=round(lo + (hi - lo) * done / max(1, total), 1),
                stage=f"{label}: {done}/{total} forecasts")
    return cb


def run_forecast(job: Job, p: dict) -> dict:
    from backend.kronos_service import features as kf
    from backend.kronos_service import precompute as kp
    from backend.kronos_service.research import load_bars

    cfg = _cfg(p)
    eng = _engine(job, cfg)
    out = {}
    syms = p["symbols"]
    for k, sym in enumerate(syms):
        job.set(stage=f"{sym}: forecasting", pct=10 + 85 * k / len(syms))
        tf = p.get("tf", "H1")
        f = load_bars(sym, tf, p.get("start") or "2025-01-01", p.get("end") or time.strftime("%Y-%m-%d", time.gmtime(time.time() + 86400)))
        # MT5 returns the bar still FORMING as the last row. A forecast from a
        # half-built candle is not the forecast the closed bar would give, and
        # it is not what precompute (closed bars only) stores - drop it.
        from backend.kronos_service.precompute import TF_MINUTES
        if len(f) and int(f["time"].iloc[-1]) + TF_MINUTES.get(tf, 60) * 60 > time.time():
            f = f.iloc[:-1]
        i = len(f) - 1
        w = kp.window_at(f, i, cfg, sym, p.get("tf", "H1"))
        paths, secs = eng.paths([w])
        a = kf.atr(w.frame["high"].to_numpy(), w.frame["low"].to_numpy(), w.frame["close"].to_numpy())
        feats = kf.compute(paths[0], float(w.frame["close"].iloc[-1]), a)
        hist = f.iloc[-120:]
        out[sym] = {
            "asof": w.asof, "atr": a, "seconds": round(secs, 2), "features": feats,
            "history": {"time": hist["time"].astype(int).tolist(),
                        "close": hist["close"].round(6).tolist()},
            "future_times": [int(x.timestamp()) for x in w.future_times],
            "paths_close": paths[0][:, :, 3].round(6).tolist(),
            "paths_high": paths[0][:, :, 1].round(6).tolist(),
            "paths_low": paths[0][:, :, 2].round(6).tolist(),
        }
        job.event(f"{sym}: p_up {feats['p_up']:.0%}, expected {feats['exp_return_pct']:+.3f}% "
                  f"in {cfg.pred_len} bars ({secs:.1f}s)")
    return out


def run_precompute(job: Job, p: dict) -> dict:
    from backend.kronos_service import precompute as kp
    from backend.kronos_service.research import load_bars

    cfg = _cfg(p)
    eng = _engine(job, cfg)
    tf = p.get("tf", "H1")
    stride = int(p.get("stride") or cfg.pred_len)
    out = {}
    syms = p["symbols"]
    for k, sym in enumerate(syms):
        f = load_bars(sym, tf, p.get("start", "2024-07-01"), p.get("end") or time.strftime("%Y-%m-%d", time.gmtime(time.time() + 86400)))
        rows = list(range(cfg.lookback - 1, len(f), stride))
        lo, hi = 5 + 90 * k / len(syms), 5 + 90 * (k + 1) / len(syms)
        store = kp.extend(eng, f, rows, sym, tf, batch=4,
                          on_progress=_progress(job, lo, hi, sym))
        out[sym] = {"rows": len(store), "bars": len(f), "stride": stride,
                    "file": str(kp.store_path(sym, tf, cfg))}
        job.event(f"{sym}: {len(store)} forecast rows stored")
    return out


def run_skill(job: Job, p: dict) -> dict:
    from backend.kronos_service import evaluate as ev
    from backend.kronos_service import precompute as kp
    from backend.kronos_service.research import load_bars, spread_bps

    cfg = _cfg(p)
    eng = _engine(job, cfg)
    tf = p.get("tf", "H1")
    out = {}
    syms = p["symbols"]
    for k, sym in enumerate(syms):
        f = load_bars(sym, tf, p.get("start", "2024-07-01"), p.get("end") or time.strftime("%Y-%m-%d", time.gmtime(time.time() + 86400)))
        rows = list(range(cfg.lookback - 1, len(f) - cfg.pred_len - 1, cfg.pred_len))[-int(p.get("points", 400)):]
        lo, hi = 5 + 90 * k / len(syms), 5 + 90 * (k + 1) / len(syms)
        store = kp.extend(eng, f, rows, sym, tf, batch=4, on_progress=_progress(job, lo, hi, sym))
        t = f["time"].to_numpy(dtype="int64")
        by_row = {i: store[int(t[i])] for i in rows if int(t[i]) in store}
        out[sym] = ev.forecast_skill(f, by_row, cfg.pred_len, spread_bps(sym, f))
        k_ = out[sym].get("kronos") or {}
        job.event(f"{sym}: Kronos {k_.get('net_bps')} bp/trade (t {k_.get('t')}), "
                  f"momentum {out[sym].get('momentum', {}).get('net_bps')} bp")
    return out


def run_strategy(job: Job, p: dict) -> dict:
    from backend.kronos_service import precompute as kp
    from backend.kronos_service import strategy_test as st
    from backend.kronos_service.research import load_bars
    from backend.rl import env as E

    cfg = _cfg(p)
    tf = p.get("tf", "H1")
    out = {}
    syms = p["symbols"]
    for k, sym in enumerate(syms):
        job.set(stage=f"{sym}: backtesting", pct=5 + 90 * k / len(syms))
        rows = kp.load(sym, tf, cfg)
        if not rows:
            out[sym] = {"error": "no stored forecasts - run a precompute job first"}
            continue
        f = load_bars(sym, tf, p.get("start", "2024-07-01"), p.get("end") or time.strftime("%Y-%m-%d", time.gmtime(time.time() + 86400)))
        out[sym] = st.run_legs(f, rows, sym, tf, E.default_risk_config(sym),
                               int(p.get("hold", 10)), seeds=int(p.get("seeds", 30)))
        c = out[sym].get("kronos_confident") or {}
        job.event(f"{sym}: confident calls {c.get('trades')} trades, {c.get('mean_r')}R (t {c.get('t')})")
    return out


def run_benchmark(job: Job, p: dict) -> dict:
    from backend.kronos_service import benchmark as bm
    job.set(stage="benchmarking", pct=10.0)
    rows = bm.run(models=tuple(p.get("models", ["Kronos-mini", "Kronos-small"])),
                  batches=(1,), samples=(int(p.get("samples", 5)),), threads=1,
                  repeats=int(p.get("repeats", 2)))
    Path("data/kronos").mkdir(parents=True, exist_ok=True)
    Path("data/kronos/benchmark.json").write_text(json.dumps({"at": time.time(), "rows": rows}))
    return {"rows": rows}


KINDS = {"forecast": run_forecast, "precompute": run_precompute, "skill": run_skill,
         "strategy": run_strategy, "benchmark": run_benchmark}


def main(path: str) -> None:
    from backend.utils.logger import configure_worker
    configure_worker("kronos-job")
    job = Job(Path(path))
    job.set(status="running", started_at=time.time(), pid=os.getpid(), pct=1.0, stage="starting")
    try:
        fn = KINDS[job.d["kind"]]
        result = fn(job, job.d.get("params") or {})
        job.d["result"] = result
        job.set(status="complete", pct=100.0, stage="complete", finished_at=time.time())
    except KeyboardInterrupt:
        job.set(status="cancelled", stage="cancelled", finished_at=time.time())
    except Exception as exc:                            # noqa: BLE001
        job.set(status="failed", stage="failed", error=f"{type(exc).__name__}: {exc}",
                trace=traceback.format_exc()[-4000:], finished_at=time.time())


if __name__ == "__main__":
    main(sys.argv[1])
