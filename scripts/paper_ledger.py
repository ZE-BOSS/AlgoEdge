"""
Forward paper-trading ledger.

Why this exists: five retrospective studies in a row looked good until a flaw in
the method showed up — a selection window that overlapped the trades being
measured, dead rows dropped from a cohort, six trades mistaken for a track record.
Recording a signal BEFORE the outcome exists makes those mistakes impossible
rather than something to catch by eye.

The ledger is deliberately separate from the app: its own SQLite file, no models,
no migrations, no imports from backend/. Nothing here can affect live trading.

    py -3.12 scripts/paper_ledger.py record  --strategy gold_fomc --market XAUUSD \
        --direction BUY --entry 3180.50 --stop 3172.00 --horizon 14400
    py -3.12 scripts/paper_ledger.py score          # close matured signals
    py -3.12 scripts/paper_ledger.py report         # what the forward record says

Scoring uses MetaTrader 5 for FX/metals/indices and Hyperliquid for crypto perps;
a signal whose price source is unavailable stays open and is retried next run.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "paper" / "paper.db"
HL_INFO = "https://api.hyperliquid.xyz/info"

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key    TEXT UNIQUE,
    recorded_at   INTEGER NOT NULL,      -- epoch seconds, when we SAW the signal
    strategy      TEXT NOT NULL,
    market        TEXT NOT NULL,
    venue         TEXT NOT NULL,         -- mt5 | hyperliquid
    direction     TEXT NOT NULL,         -- BUY | SELL
    entry_px      REAL NOT NULL,
    stop_px       REAL,
    target_px     REAL,
    horizon_s     INTEGER NOT NULL,      -- close at entry time + horizon
    risk_pct      REAL,
    notes         TEXT,
    status        TEXT NOT NULL DEFAULT 'open'
);
CREATE TABLE IF NOT EXISTS outcomes (
    signal_id     INTEGER PRIMARY KEY REFERENCES signals(id),
    scored_at     INTEGER NOT NULL,
    exit_px       REAL NOT NULL,
    exit_reason   TEXT NOT NULL,         -- stop | target | horizon
    r_multiple    REAL,
    pnl_pct       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status);
"""


def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


# ── prices ───────────────────────────────────────────────────────────────────
def hl_candles(coin: str, start_ms: int, end_ms: int, interval: str = "5m"):
    body = {"type": "candleSnapshot", "req": {"coin": coin, "interval": interval,
                                              "startTime": start_ms, "endTime": end_ms}}
    req = urllib.request.Request(HL_INFO, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": "algoedge-paper"})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            rows = json.loads(r.read())
        return [(int(c["t"]) // 1000, float(c["o"]), float(c["h"]), float(c["l"]), float(c["c"])) for c in rows]
    except Exception:
        return []


def mt5_candles(symbol: str, start_s: int, end_s: int):
    try:
        import MetaTrader5 as mt5
    except ImportError:
        return []
    if not mt5.initialize():
        return []
    try:
        mt5.symbol_select(symbol, True)
        r = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5,
                                 datetime.fromtimestamp(start_s, timezone.utc),
                                 datetime.fromtimestamp(end_s, timezone.utc))
        if r is None:
            return []
        return [(int(x["time"]), float(x["open"]), float(x["high"]), float(x["low"]), float(x["close"])) for x in r]
    finally:
        mt5.shutdown()


def candles(venue: str, market: str, start_s: int, end_s: int):
    if venue == "hyperliquid":
        return hl_candles(market, start_s * 1000, end_s * 1000)
    return mt5_candles(market, start_s, end_s)


# ── commands ─────────────────────────────────────────────────────────────────
def cmd_record(a) -> None:
    now = int(time.time())
    key = a.key or f"{a.strategy}|{a.market}|{now // 60}"
    with db() as con:
        try:
            cur = con.execute(
                "INSERT INTO signals (dedupe_key, recorded_at, strategy, market, venue, direction, entry_px,"
                " stop_px, target_px, horizon_s, risk_pct, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (key, now, a.strategy, a.market, a.venue, a.direction.upper(), a.entry, a.stop, a.target,
                 a.horizon, a.risk_pct, a.notes))
            print(f"recorded signal #{cur.lastrowid}: {a.strategy} {a.direction.upper()} {a.market} @ {a.entry} "
                  f"stop {a.stop} horizon {a.horizon}s")
        except sqlite3.IntegrityError:
            print(f"already recorded ({key}) — nothing written")


def cmd_score(a) -> None:
    now = int(time.time())
    with db() as con:
        rows = con.execute("SELECT * FROM signals WHERE status = 'open'").fetchall()
        done = 0
        for s in rows:
            due = s["recorded_at"] + s["horizon_s"]
            if now < due:
                continue
            bars = candles(s["venue"], s["market"], s["recorded_at"], due + 3600)
            bars = [b for b in bars if b[0] >= s["recorded_at"]]
            if not bars:
                continue                                  # source unavailable; retry next run
            sign = 1 if s["direction"] == "BUY" else -1
            exit_px, reason = None, "horizon"
            for t, o, h, l, c in bars:
                if t > due:
                    break
                if s["stop_px"] and ((sign > 0 and l <= s["stop_px"]) or (sign < 0 and h >= s["stop_px"])):
                    exit_px, reason = s["stop_px"], "stop"
                    break
                if s["target_px"] and ((sign > 0 and h >= s["target_px"]) or (sign < 0 and l <= s["target_px"])):
                    exit_px, reason = s["target_px"], "target"
                    break
            if exit_px is None:
                exit_px = next((c for t, o, h, l, c in reversed(bars) if t <= due), bars[-1][4])
            pnl_pct = sign * (exit_px - s["entry_px"]) / s["entry_px"]
            r = None
            if s["stop_px"]:
                risk = abs(s["entry_px"] - s["stop_px"])
                r = sign * (exit_px - s["entry_px"]) / risk if risk else None
            con.execute("INSERT OR REPLACE INTO outcomes VALUES (?,?,?,?,?,?)",
                        (s["id"], now, exit_px, reason, r, pnl_pct))
            con.execute("UPDATE signals SET status = 'closed' WHERE id = ?", (s["id"],))
            done += 1
        open_left = con.execute("SELECT COUNT(*) c FROM signals WHERE status = 'open'").fetchone()["c"]
    print(f"scored {done} signal(s); {open_left} still open")


def cmd_report(a) -> None:
    import math
    import statistics as st
    with db() as con:
        rows = con.execute(
            "SELECT s.strategy, s.market, s.recorded_at, o.r_multiple, o.pnl_pct, o.exit_reason"
            " FROM signals s JOIN outcomes o ON o.signal_id = s.id"
            " WHERE s.recorded_at >= ?", (int(time.time()) - a.days * 86400,)).fetchall()
        open_rows = con.execute("SELECT strategy, COUNT(*) c FROM signals WHERE status='open' GROUP BY strategy").fetchall()
    if not rows:
        print("no closed signals yet")
    by = {}
    for r in rows:
        by.setdefault(r["strategy"], []).append(r)
    print(f"{'strategy':22s} {'n':>4s} {'win':>6s} {'avg R':>7s} {'t':>6s} {'avg %':>7s} {'total %':>8s}")
    for strat, rs in sorted(by.items()):
        rms = [x["r_multiple"] for x in rs if x["r_multiple"] is not None]
        pls = [x["pnl_pct"] for x in rs]
        t = ""
        if len(rms) > 2 and st.pstdev(rms) > 0:
            t = f"{st.mean(rms) / (st.pstdev(rms) / math.sqrt(len(rms))):.2f}"
        print(f"{strat:22s} {len(rs):4d} {sum(1 for x in pls if x > 0)/len(pls):6.1%} "
              f"{(st.mean(rms) if rms else 0):+7.2f} {t:>6s} {st.mean(pls):+7.2%} {sum(pls):+8.2%}")
    for r in open_rows:
        print(f"  open: {r['strategy']} x{r['c']}")
    print("\nA strategy is worth real money only after ~400 trades with t > 2, or a smaller sample that "
          "holds up across markets. Until then this is data collection.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("record", help="write a signal as it happens")
    r.add_argument("--strategy", required=True)
    r.add_argument("--market", required=True)
    r.add_argument("--venue", default="mt5", choices=["mt5", "hyperliquid"])
    r.add_argument("--direction", required=True, choices=["BUY", "SELL", "buy", "sell"])
    r.add_argument("--entry", type=float, required=True)
    r.add_argument("--stop", type=float)
    r.add_argument("--target", type=float)
    r.add_argument("--horizon", type=int, required=True, help="seconds until the position is closed")
    r.add_argument("--risk-pct", type=float, default=1.0)
    r.add_argument("--notes")
    r.add_argument("--key", help="dedupe key; defaults to strategy|market|minute")
    r.set_defaults(func=cmd_record)

    s = sub.add_parser("score", help="close matured signals using real prices")
    s.set_defaults(func=cmd_score)

    q = sub.add_parser("report", help="what the forward record says so far")
    q.add_argument("--days", type=int, default=365)
    q.set_defaults(func=cmd_report)

    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
