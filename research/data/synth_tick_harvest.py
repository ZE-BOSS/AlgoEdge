"""Phase 0: harvest 365 days of raw ticks per synthetic index from Deriv's WS API.

Design notes that matter:

* **The rate limit is the binding constraint, and it is published.** `website_status`
  reports `max_requestes_general: {minutely: 220, hourly: 14400}`, and ticks_history
  counts against it. Exceeding it returns `{"code": "RateLimit"}` per request - fast,
  cheap, and silent unless you look. A first version of this script pipelined 6
  connections flat out, collected ~5,900 RateLimit errors per symbol, and wrote
  files that looked plausible (BOOM1000 "340.6d covered") while holding 2M of an
  expected 29M ticks. Hence the token bucket AND the completeness assertion below;
  either alone would have missed it.
* **Failed windows are retried, never dropped.** The same first version merely
  counted errors, so a throttled window vanished from the output with no trace in
  the data itself. Windows now return to the queue with backoff.
* **The 365-day silent fallback.** A request whose `start` is older than 365 days
  does NOT error - it returns the *latest* ticks instead. A naive scraper collects
  duplicates and believes it has two years. `_accept()` rejects any chunk whose
  timestamps fall outside the window asked for.
* **Resumable.** One .npz per symbol plus a .part checkpoint every ~10 min, so a
  5-hour harvest that dies at hour 4 resumes rather than restarts.
* **float64 prices.** Jump 25 trades near 117,553.42 - eight significant figures.
  float32 carries ~7.2 and would round away the tick size Phase 1 measures.

Usage:  python synth_tick_harvest.py [--days 365] [--symbols BOOM1000,R_75]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pickle
import time
from collections import deque
from pathlib import Path

import numpy as np
import websockets

URL = "wss://ws.derivws.com/websockets/v3?app_id=1089"
OUT = Path(__file__).resolve().parent / "ticks"

# Published limits: 220/min, 14400/hour. Sit deliberately under both - this is a
# public endpoint and a 10% margin costs half an hour on a 5-hour job.
REQ_PER_MIN = 200
DEPTH = 16                  # in flight at once; hides latency, does not beat the limit
CHUNK_TICKS = 5000          # server cap per request
CHECKPOINT_EVERY = 600      # seconds; the pickle reaches ~0.5 GB, so keep it rare
MIN_COVERAGE = 0.98         # refuse to declare success below this

SYMBOLS: list[tuple[str, str, int]] = [
    ("BOOM1000",  "Boom 1000 Index",          1),
    ("BOOM500",   "Boom 500 Index",           1),
    ("CRASH1000", "Crash 1000 Index",         1),
    ("CRASH500",  "Crash 500 Index",          1),
    ("stpRNG",    "Step Index",               1),
    ("RB100",     "Range Break 100 Index",    1),
    ("RB200",     "Range Break 200 Index",    1),
    ("JD25",      "Jump 25 Index",            1),
    ("JD100",     "Jump 100 Index",           1),
    ("R_75",      "Volatility 75 Index",      2),
    ("R_25",      "Volatility 25 Index",      2),
    ("R_100",     "Volatility 100 Index",     2),
]


class Bucket:
    """Token bucket over a rolling 60 s window, shared by every in-flight request."""

    def __init__(self, per_min: int):
        self.per_min = per_min
        self.stamps: deque[float] = deque()

    async def take(self) -> None:
        while True:
            now = time.time()
            while self.stamps and now - self.stamps[0] > 60.0:
                self.stamps.popleft()
            if len(self.stamps) < self.per_min:
                self.stamps.append(now)
                return
            await asyncio.sleep(max(0.05, 60.0 - (now - self.stamps[0])))


def _accept(times: list[int], lo: int, hi: int) -> bool:
    """Reject a chunk that did not come from the window we asked for (fallback guard)."""
    return bool(times) and lo <= times[0] <= hi and lo <= times[-1] <= hi


async def harvest(sym: str, spt: int, days: int, part: Path) -> dict:
    now = int(time.time())
    start = now - days * 86400 + 3600
    span = CHUNK_TICKS * spt
    todo: deque[tuple[int, int, int]] = deque()          # (lo, hi, attempts)

    out: dict[int, tuple[list, list]] = {}
    if part.exists():
        out = pickle.loads(part.read_bytes())
        print(f"    resumed {sym} from checkpoint: {len(out):,} windows", flush=True)
    for w in range(start, now, span):
        if w not in out:
            todo.append((w, min(w + span, now), 0))
    n_total = len(out) + len(todo)

    bucket = Bucket(REQ_PER_MIN)
    stats = {"ok": 0, "ratelimit": 0, "other": 0, "rejected": 0, "reconnects": 0}
    t0 = time.time()
    last_ckpt = time.time()

    while todo:
        try:
            async with websockets.connect(URL, ping_interval=20,
                                          max_size=16 * 1024 * 1024) as ws:
                inflight: dict[int, tuple[int, int, int]] = {}
                rid = 0
                while todo or inflight:
                    while todo and len(inflight) < DEPTH:
                        lo, hi, att = todo.popleft()
                        await bucket.take()
                        rid += 1
                        await ws.send(json.dumps({
                            "ticks_history": sym, "start": lo, "end": hi,
                            "count": CHUNK_TICKS, "style": "ticks", "req_id": rid}))
                        inflight[rid] = (lo, hi, att)
                    if not inflight:
                        break
                    r = json.loads(await ws.recv())
                    got = r.get("req_id")
                    if got not in inflight:
                        continue
                    lo, hi, att = inflight.pop(got)
                    if "error" in r:
                        code = r["error"].get("code")
                        if code == "RateLimit":
                            stats["ratelimit"] += 1
                            await asyncio.sleep(2.0)       # cool down, then requeue
                        else:
                            stats["other"] += 1
                        todo.append((lo, hi, att + 1))
                        continue
                    t = r.get("history", {}).get("times", [])
                    p = r.get("history", {}).get("prices", [])
                    if not _accept(t, lo, hi):
                        stats["rejected"] += 1
                        continue                            # genuinely outside range
                    out[lo] = (t, p)
                    stats["ok"] += 1

                    if time.time() - last_ckpt > CHECKPOINT_EVERY:
                        part.write_bytes(pickle.dumps(out))
                        last_ckpt = time.time()
                        done = len(out)
                        el = time.time() - t0
                        rate = done / max(el, 1) * 60
                        eta = (n_total - done) / max(rate, 0.1)
                        print(f"    .. {sym} {done:,}/{n_total:,} windows "
                              f"({done/n_total*100:.0f}%) {rate:.0f} req/min "
                              f"ETA {eta:.0f} min | RL {stats['ratelimit']}", flush=True)
        except Exception as e:
            stats["reconnects"] += 1
            print(f"    !! {sym} reconnect {stats['reconnects']}: {type(e).__name__}",
                  flush=True)
            if stats["reconnects"] > 50:
                raise
            await asyncio.sleep(3)

    stats["elapsed"] = time.time() - t0
    stats["windows"] = len(out)
    stats["_data"] = out
    return stats


def stitch(out: dict) -> tuple[np.ndarray, np.ndarray]:
    """Concatenate the per-window chunks, deduplicate, sort.

    Preallocated and filled by slice rather than built with list.extend(): a full
    year of 1-second ticks is ~29 M points, and as Python lists that is several GB
    of boxed ints before numpy ever sees it. Measured 2.8 GB resident on the first
    run; this keeps it near the 460 MB the arrays actually need.
    """
    keys = sorted(out)
    total = sum(len(out[k][0]) for k in keys)
    ts = np.empty(total, dtype=np.int64)
    px = np.empty(total, dtype=np.float64)
    at = 0
    for k in keys:
        t, p = out[k]
        n = len(t)
        ts[at:at + n] = t
        px[at:at + n] = p
        at += n
    order = np.argsort(ts, kind="stable")
    ts, px = ts[order], px[order]
    keep = np.concatenate(([True], np.diff(ts) != 0))
    return ts[keep], px[keep]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--symbols", type=str, default="")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    OUT.mkdir(exist_ok=True)
    want = set(a.symbols.split(",")) if a.symbols else None
    todo = [s for s in SYMBOLS if not want or s[0] in want]
    print(f"harvesting {len(todo)} symbols x {a.days}d at <= {REQ_PER_MIN} req/min "
          f"-> {OUT}", flush=True)

    for code, mt5name, spt in todo:
        f = OUT / f"{code}_{a.days}d.npz"
        part = OUT / f"{code}_{a.days}d.part"
        if f.exists() and not a.force:
            z = np.load(f)
            print(f"  {code}: cached ({len(z['t']):,} ticks) - skipping", flush=True)
            continue

        st = await harvest(code, spt, a.days, part)
        ts, px = stitch(st.pop("_data"))
        if len(ts) == 0:
            print(f"  {code}: NOTHING HARVESTED", flush=True)
            continue

        # completeness assertion - the check the first version lacked
        expected = (ts[-1] - ts[0]) / spt + 1
        coverage = len(ts) / expected
        gaps = np.diff(ts)
        verdict = "OK" if coverage >= MIN_COVERAGE else "INCOMPLETE"
        print(f"  {code}: {len(ts):,} ticks | {(ts[-1]-ts[0])/86400:.1f}d | "
              f"coverage {coverage:.4%} {verdict} | median gap {np.median(gaps):.0f}s | "
              f"gaps>60s {(gaps>60).sum():,} | max gap {gaps.max():,}s | "
              f"{st['elapsed']/60:.0f} min, RL {st['ratelimit']}, "
              f"reconn {st['reconnects']}", flush=True)
        if coverage < MIN_COVERAGE:
            print(f"  {code}: NOT SAVED - coverage below {MIN_COVERAGE:.0%}. "
                  f"Checkpoint kept at {part.name} for resume.", flush=True)
            continue
        # Write to a temp name and rename. Compressing 31 M ticks takes minutes,
        # and a reader globbing for *.npz during that window gets a truncated zip
        # (BadZipFile) - the coverage line above has already printed by then, so
        # the file looks finished when it is not. Rename is atomic on the same
        # volume, so the .npz only ever exists complete.
        tmp = f.with_suffix(".npz.writing")
        np.savez_compressed(tmp, t=ts, p=px, meta=np.array(
            [code, mt5name, str(spt), str(a.days)], dtype=object))
        tmp.replace(f)
        part.unlink(missing_ok=True)
        print(f"  {code}: saved {f.name}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
