#!/usr/bin/env python
"""
scripts/collect_deribit_flow.py

Accumulate real options order flow, for free, so the flow hypothesis can
eventually be tested instead of argued about.

THE PROBLEM THIS SOLVES
-----------------------
`Implementation/OPTIONS-INTRADAY-2026-09-28.md` established that option STRIKES
do nothing to the underlying intraday — well powered, five markets, nothing at
any spacing. What that leaves open is whether option FLOW does: whether a call
being bought, right now, at a particular strike, moves the underlying through
the dealer's hedge.

Deribit publishes exactly the right data for nothing and without a key. Every
option trade carries the strike, the expiry, call or put, the aggressor's side,
the size, the implied volatility AND the underlying index price at that instant:

    BTC-27NOV26-99000-C  buy  1.0  iv 38.11  index 83045.03

The only thing wrong with it is depth: probing backwards, the public trade feed
returns nothing past about 36 hours. So the data cannot be backtested today. It
can be COLLECTED, and in a few weeks there is a sample.

WHAT IS STORED
--------------
  trades/YYYY-MM-DD.jsonl   every option trade, append-only, deduped on trade_id
  chain/YYYY-MM-DDTHH.json  a periodic snapshot of the whole chain with open
                            interest, because OI is a level and trades are a flow

Run it once to seed from whatever history Deribit still holds, or on a loop
(--watch) to keep it current. Re-running is safe: trade ids already stored are
skipped, so a gap is filled rather than duplicated.

    py -3.12 scripts/collect_deribit_flow.py --seed
    py -3.12 scripts/collect_deribit_flow.py --watch --minutes 60
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "data" / "deribit"
API = "https://www.deribit.com/api/v2/public/"
PAGE = 1000            # Deribit's per-request ceiling


def get(method: str, **params) -> dict:
    url = API + method + "?" + "&".join(f"{k}={v}" for k, v in params.items())
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=90) as r:
                return json.loads(r.read()).get("result") or {}
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            if attempt == 3:
                raise
            # Deribit rate-limits rather than erroring, so back off politely.
            print(f"  retry {attempt + 1}/3 after {e}", file=sys.stderr)
            time.sleep(2 ** attempt)
    return {}


def fetch_trades(currency: str, start_ms: int, end_ms: int) -> list[dict]:
    """Every option trade in the window, paged forward.

    Paged by advancing `start_timestamp` past the last trade seen rather than by
    an offset: the feed is append-only and an offset would silently skip trades
    that arrived mid-page.
    """
    out: list[dict] = []
    seen: set[str] = set()
    cursor = start_ms
    while cursor < end_ms:
        got = get("get_last_trades_by_currency_and_time", currency=currency,
                  kind="option", start_timestamp=cursor, end_timestamp=end_ms,
                  count=PAGE, sorting="asc")
        trades = got.get("trades") or []
        fresh = [t for t in trades if t.get("trade_id") not in seen]
        if not fresh:
            break
        for t in fresh:
            seen.add(t["trade_id"])
        out += fresh
        newest = max(int(t["timestamp"]) for t in fresh)
        if newest <= cursor:
            break
        cursor = newest + 1
        if len(trades) < PAGE:
            break
    return out


def store_trades(trades: list[dict]) -> tuple[int, int]:
    """Append to one file per UTC day, skipping ids already there."""
    if not trades:
        return 0, 0
    by_day: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        day = datetime.fromtimestamp(int(t["timestamp"]) / 1000, timezone.utc).date()
        by_day[day.isoformat()].append(t)

    written = skipped = 0
    folder = OUT / "trades"
    folder.mkdir(parents=True, exist_ok=True)
    for day, rows in by_day.items():
        path = folder / f"{day}.jsonl"
        existing: set[str] = set()
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    existing.add(json.loads(line)["trade_id"])
                except (json.JSONDecodeError, KeyError):
                    continue
        with path.open("a", encoding="utf-8") as fh:
            for row in sorted(rows, key=lambda x: int(x["timestamp"])):
                if row.get("trade_id") in existing:
                    skipped += 1
                    continue
                fh.write(json.dumps(row, separators=(",", ":")) + "\n")
                written += 1
    return written, skipped


def store_chain(currency: str) -> int:
    """A snapshot of the whole chain, for the open-interest level.

    Trades are a flow and open interest is a level; the gamma story needs both,
    and OI is only available as "right now" so it has to be sampled.
    """
    rows = get("get_book_summary_by_currency", currency=currency, kind="option")
    if not rows:
        return 0
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
    folder = OUT / "chain"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{currency}-{stamp}.json").write_text(
        json.dumps({"captured": datetime.now(timezone.utc).isoformat(), "rows": rows},
                   separators=(",", ":")))
    return len(rows)


def inventory() -> None:
    folder = OUT / "trades"
    if not folder.exists():
        print("  nothing collected yet")
        return
    total = 0
    days = sorted(folder.glob("*.jsonl"))
    for path in days:
        n = sum(1 for _ in path.open(encoding="utf-8"))
        total += n
        print(f"  {path.stem}  {n:>7,} trades")
    chains = len(list((OUT / 'chain').glob('*.json'))) if (OUT / "chain").exists() else 0
    print(f"  {'TOTAL':10s}  {total:>7,} trades over {len(days)} day(s), {chains} chain snapshot(s)")


def cycle(currencies: list[str], minutes: int, with_chain: bool) -> None:
    now = int(time.time() * 1000)
    start = now - minutes * 60 * 1000
    for currency in currencies:
        trades = fetch_trades(currency, start, now)
        written, skipped = store_trades(trades)
        line = (f"{datetime.now(timezone.utc):%H:%M:%S} {currency}: "
                f"{len(trades):>5,} fetched, {written:>5,} new, {skipped:>5,} already held")
        if with_chain:
            line += f", chain {store_chain(currency):>4d} contracts"
        print(line, flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--currencies", nargs="*", default=["BTC", "ETH"])
    ap.add_argument("--minutes", type=int, default=60,
                    help="how far back each pass reaches")
    ap.add_argument("--seed", action="store_true",
                    help="one pass reaching back 48h, to take whatever history is left")
    ap.add_argument("--watch", action="store_true", help="keep polling")
    ap.add_argument("--every", type=int, default=900, help="seconds between passes")
    ap.add_argument("--no-chain", action="store_true")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    minutes = 48 * 60 if args.seed else args.minutes

    print(f"\nDeribit flow collector -> {OUT}")
    print(f"currencies {', '.join(args.currencies)} | reaching back {minutes} min"
          f"{' | chain snapshots on' if not args.no_chain else ''}\n")

    try:
        cycle(args.currencies, minutes, not args.no_chain)
        while args.watch:
            time.sleep(args.every)
            cycle(args.currencies, args.minutes, not args.no_chain)
    except KeyboardInterrupt:
        print("\nstopped")

    print("\nheld:")
    inventory()


if __name__ == "__main__":
    main()
