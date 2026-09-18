"""
Forward record for funding carry (short perp / long spot), Hyperliquid.

Funding is paid hourly and is knowable in advance, so unlike a directional signal
this builds a real track record from day one: snapshot what the venue is paying,
then accrue it. If live funding drifts away from the +8.1%/yr the 2-year backtest
showed, this is where that shows up before any money is at risk.

    py -3.12 scripts/carry_tracker.py snapshot     # run hourly or daily (cron/pm2)
    py -3.12 scripts/carry_tracker.py report

Storage is the paper ledger's SQLite file, in its own table. Nothing here imports
from backend/ or touches live trading.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics as st
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "paper" / "paper.db"
INFO = "https://api.hyperliquid.xyz/info"
COINS = ["BTC", "ETH", "SOL", "HYPE", "DOGE", "XRP", "AVAX", "LINK", "SUI", "LTC", "BNB", "ARB"]
CAPITAL_FACTOR = 1.25          # spot leg fully funded + 25% margin on the short perp

SCHEMA = """
CREATE TABLE IF NOT EXISTS carry_snapshots (
    ts        INTEGER NOT NULL,      -- epoch seconds of the snapshot
    coin      TEXT NOT NULL,
    funding_h REAL NOT NULL,         -- hourly funding rate, as a fraction
    mark_px   REAL,
    oi        REAL,
    PRIMARY KEY (ts, coin)
);
"""


def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def info(body, tries=4):
    for k in range(tries):
        try:
            req = urllib.request.Request(INFO, data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json", "User-Agent": "algoedge-carry"})
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.loads(r.read())
        except Exception:
            time.sleep(2 ** k)
    return None


def cmd_snapshot(_a) -> None:
    ctx = info({"type": "metaAndAssetCtxs"})
    if not ctx or len(ctx) < 2:
        print("could not read Hyperliquid context")
        return
    universe = ctx[0].get("universe", [])
    rows = ctx[1]
    now = int(time.time())
    wrote = 0
    with db() as con:
        for meta, c in zip(universe, rows):
            coin = meta.get("name")
            if coin not in COINS:
                continue
            try:
                con.execute("INSERT OR REPLACE INTO carry_snapshots VALUES (?,?,?,?,?)",
                            (now, coin, float(c["funding"]), float(c.get("markPx") or 0),
                             float(c.get("openInterest") or 0)))
                wrote += 1
            except (KeyError, TypeError, ValueError):
                continue
    rates = [float(c["funding"]) for m, c in zip(universe, rows) if m.get("name") in COINS and c.get("funding")]
    if rates:
        print(f"{datetime.fromtimestamp(now, timezone.utc):%Y-%m-%d %H:%M} UTC — {wrote} coins; "
              f"mean funding {st.mean(rates)*24*365:+.2%}/yr on notional, "
              f"{st.mean(rates)*24*365/CAPITAL_FACTOR:+.2%}/yr on capital")
    else:
        print("no funding values in response")


def cmd_report(a) -> None:
    since = int(time.time()) - a.days * 86400
    with db() as con:
        rows = con.execute("SELECT * FROM carry_snapshots WHERE ts >= ? ORDER BY ts", (since,)).fetchall()
    if not rows:
        print("no snapshots yet — run `snapshot` first, ideally hourly")
        return
    by_coin: dict[str, list] = {}
    for r in rows:
        by_coin.setdefault(r["coin"], []).append(r)
    span_h = (rows[-1]["ts"] - rows[0]["ts"]) / 3600 or 1
    print(f"{len(rows)} snapshots over {span_h/24:.1f} days "
          f"({datetime.fromtimestamp(rows[0]['ts'], timezone.utc):%Y-%m-%d} onward)\n")
    print(f"{'coin':6s} {'snaps':>6s} {'mean funding/yr':>16s} {'on capital':>11s} {'% negative':>11s}")
    port = []
    for coin, rs in sorted(by_coin.items()):
        f = [x["funding_h"] for x in rs]
        ann = st.mean(f) * 24 * 365
        port.append(ann)
        print(f"{coin:6s} {len(rs):6d} {ann:+15.2%} {ann/CAPITAL_FACTOR:+10.2%} "
              f"{sum(1 for v in f if v < 0)/len(f):10.1%}")
    if port:
        p = st.mean(port) / CAPITAL_FACTOR
        print(f"\nequal-weight portfolio: {p:+.2%}/yr on capital")
        for bal in (1_000, 5_000, 10_000):
            print(f"  ${bal:,} -> ${bal*p/12:,.0f}/month at this rate, before hedge tracking error, "
                  f"liquidation risk and exchange risk")
        print("\nBacktest reference (2 years to 2026-09-18): +8.1%/yr on capital, 2026 running 4-7%. "
              "If the live figure sits well below that, the trade is not worth the operational risk.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snapshot", help="record what funding is paying right now")
    s.set_defaults(func=cmd_snapshot)
    r = sub.add_parser("report", help="what the live record says so far")
    r.add_argument("--days", type=int, default=90)
    r.set_defaults(func=cmd_report)
    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
