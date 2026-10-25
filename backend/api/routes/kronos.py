"""
backend/api/routes/kronos.py

The Kronos page's API. Nothing here imports torch: every model run is a JOB
in its own BelowNormal-priority process (backend/kronos_service/jobs.py), so
the trading backend stays as fast as it was with Kronos switched off.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.api.deps import require_admin
from backend.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/kronos", tags=["kronos"], dependencies=[Depends(require_admin)])

ROOT = Path(__file__).resolve().parents[3]
DEPS = ROOT / ".kronos_deps"
JOB_DIR = ROOT / "data" / "kronos" / "jobs"
RESULTS = ROOT / "research" / "kronos" / "results"
MAX_RUNNING = 1          # one model process at a time: the VPS has 2 vCPUs
BELOW_NORMAL = 0x00004000


def _installed() -> dict:
    from backend.kronos_service.config import MODELS, MODEL_ROOT
    models = {}
    for name, meta in MODELS.items():
        d = ROOT / MODEL_ROOT / name
        t = ROOT / MODEL_ROOT / meta["tokenizer"]
        models[name] = {"weights": (d / "model.safetensors").exists(),
                        "tokenizer": (t / "model.safetensors").exists(),
                        "revision": (d / "REVISION").read_text().strip()[:8]
                        if (d / "REVISION").exists() else None,
                        "params": meta["params"], "max_context": meta["max_context"]}
    return {"torch": (DEPS / "torch").exists(), "models": models}


def _safe(obj):
    """NaN / inf -> None. Research results legitimately hold NaN (a t-stat over
    too few trades), and FastAPI refuses to encode it: the page got a 500
    (shown by the browser as a CORS error) instead of the results."""
    import math
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe(v) for v in obj]
    return obj


def _read(path: Path) -> dict | None:
    try:
        return _safe(json.loads(path.read_text(encoding="utf-8")))
    except Exception:                                   # noqa: BLE001
        return None


_ALIVE_CACHE: dict[int, tuple[float, bool]] = {}


def _alive(pid: int | None) -> bool:
    """Is the job process still there? Cached for 5 s: the page polls every
    2 s while a job runs, and spawning `tasklist` that often is real CPU on a
    2-vCPU VPS."""
    if not pid:
        return False
    hit = _ALIVE_CACHE.get(int(pid))
    if hit and time.time() - hit[0] < 5:
        return hit[1]
    alive = _alive_uncached(int(pid))
    _ALIVE_CACHE[int(pid)] = (time.time(), alive)
    return alive


def _alive_uncached(pid: int) -> bool:
    try:
        import psutil  # optional
        return psutil.pid_exists(int(pid))
    except Exception:                                   # noqa: BLE001
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/FI", f"PID eq {int(pid)}"],
                                 capture_output=True, text=True, timeout=10).stdout
            return str(int(pid)) in out
        try:
            os.kill(int(pid), 0)
            return True
        except OSError:
            return False


def _jobs() -> list[dict]:
    JOB_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for p in sorted(JOB_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:50]:
        d = _read(p)
        if not d:
            continue
        # A process that died without writing its own ending (killed, machine
        # restarted) must not read as "running" forever.
        if d.get("status") == "running" and not _alive(d.get("pid")):
            d["status"] = "failed"
            d["error"] = d.get("error") or "the job process is gone (killed or restarted)"
        # A job whose process never started (or died before its first write)
        # must not sit "queued" forever: queued jobs count against the
        # one-at-a-time limit, so one bad spawn would block Kronos for good.
        if d.get("status") == "queued" and time.time() - float(d.get("created_at") or 0) > 120                 and not _alive(d.get("pid")):
            d["status"] = "failed"
            d["error"] = d.get("error") or "the job process never started"
        out.append(d)
    return out


@router.get("/status")
async def status():
    jobs = _jobs()
    bench = _read(ROOT / "data" / "kronos" / "benchmark.json")
    return {"installed": _installed(), "running": [j for j in jobs if j.get("status") == "running"],
            "benchmark": bench, "store": _store_summary()}


def _store_summary() -> list[dict]:
    d = ROOT / "data" / "kronos"
    out = []
    for p in sorted(d.glob("*.csv")) if d.exists() else []:
        try:
            with p.open(encoding="utf-8") as fh:
                n = sum(1 for _ in fh) - 1
        except OSError:
            n = 0
        sym, tf, *rest = p.stem.split("_")
        out.append({"symbol": sym, "timeframe": tf, "model_id": "_".join(rest), "rows": n,
                    "updated": p.stat().st_mtime})
    return out


class JobIn(BaseModel):
    kind: str
    symbols: list[str] = Field(default_factory=lambda: ["SPX500"])
    tf: str = "H1"
    start: str | None = None
    end: str | None = None
    model: str = "Kronos-mini"
    lookback: int = 512
    pred_len: int = 12
    samples: int = 5
    stride: int | None = None
    points: int = 400
    hold: int = 10
    seeds: int = 30


@router.post("/jobs")
async def start_job(body: JobIn):
    from backend.kronos_service.jobs import KINDS
    if body.kind not in KINDS:
        raise HTTPException(400, f"unknown kind {body.kind!r}; one of {sorted(KINDS)}")
    if not _installed()["torch"] and body.kind != "strategy":
        raise HTTPException(409, "Kronos is not installed on this server: see "
                                 "backend/kronos_service/README.md (torch in .kronos_deps)")
    if sum(1 for j in _jobs() if j.get("status") in ("running", "queued")) >= MAX_RUNNING:
        raise HTTPException(409, "a Kronos job is already running - one at a time, so it "
                                 "cannot take CPU from trading")
    jid = f"{body.kind}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
    JOB_DIR.mkdir(parents=True, exist_ok=True)
    path = JOB_DIR / f"{jid}.json"
    params = body.model_dump()
    path.write_text(json.dumps({"id": jid, "kind": body.kind, "params": params, "status": "queued",
                                "pct": 0.0, "stage": "queued", "created_at": time.time(),
                                "events": []}), encoding="utf-8")
    env = dict(os.environ)
    sep = ";" if os.name == "nt" else ":"
    # The child needs the API's own packages too (pandas, MetaTrader5...),
    # however this server was started - venv, or a launcher that edits
    # sys.path - so pass its site-packages through explicitly.
    site = [p for p in sys.path if p and "site-packages" in p]
    env["PYTHONPATH"] = sep.join([str(DEPS), str(DEPS / "_small"), str(ROOT), *site,
                                  env.get("PYTHONPATH", "")])
    kw = {"creationflags": BELOW_NORMAL} if os.name == "nt" else {"preexec_fn": lambda: os.nice(10)}
    log = (JOB_DIR / f"{jid}.log").open("w", encoding="utf-8")
    try:
        proc = subprocess.Popen([sys.executable, "-m", "backend.kronos_service.jobs", str(path)],
                                cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, **kw)
    except Exception as exc:                            # noqa: BLE001
        d = _read(path) or {}
        d.update(status="failed", error=f"could not start the job process: {exc}")
        path.write_text(json.dumps(d), encoding="utf-8")
        raise HTTPException(500, d["error"]) from exc
    finally:
        log.close()          # the child has its own handle; ours would leak per job
    d = _read(path) or {}
    d["pid"] = proc.pid
    path.write_text(json.dumps(d), encoding="utf-8")
    logger.info(f"[KRONOS] started {jid} (pid {proc.pid})")
    return d


@router.get("/jobs")
async def list_jobs():
    return {"jobs": [{k: v for k, v in j.items() if k not in ("result", "trace")} for j in _jobs()]}


@router.get("/jobs/{jid}")
async def get_job(jid: str):
    p = JOB_DIR / f"{Path(jid).name}.json"
    d = _read(p)
    if d is None:
        raise HTTPException(404, f"no job {jid}")
    if d.get("status") == "running" and not _alive(d.get("pid")):
        d["status"], d["error"] = "failed", d.get("error") or "the job process is gone"
    return d


@router.post("/jobs/{jid}/cancel")
async def cancel_job(jid: str):
    p = JOB_DIR / f"{Path(jid).name}.json"
    d = _read(p)
    if d is None:
        raise HTTPException(404, f"no job {jid}")
    d["cancel_requested"] = True
    p.write_text(json.dumps(d), encoding="utf-8")
    pid = d.get("pid")
    if pid and _alive(pid):
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                               capture_output=True, timeout=20)
            else:
                os.kill(int(pid), 15)
        except Exception as exc:                        # noqa: BLE001
            logger.warning(f"[KRONOS] could not stop {jid}: {exc}")
    d["status"], d["stage"] = "cancelled", "cancelled"
    p.write_text(json.dumps(d), encoding="utf-8")
    return d


@router.get("/results")
async def results():
    """The research result files (skill, gate, strategy) the findings came from."""
    out = []
    for p in sorted(RESULTS.glob("*.json")) if RESULTS.exists() else []:
        d = _read(p)
        if d:
            out.append({"file": p.name, "args": d.get("args"), "model_id": d.get("model_id"),
                        "results": d.get("results")})
    return {"results": out}
