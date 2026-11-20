"""Backtest every Telegram signal channel the copy-trader's account has joined.

READ-ONLY: reads Telegram history (no joining, no sending) and MT5 price
history; writes nothing to the database and places no orders.

    cd /opt/algoedge/<inst>/app && PYTHONPATH=. ../venv/bin/python scripts/channel_signal_backtest.py <inst> [days]

For each channel/group: every message in the window goes through the copy
trader's own parser. Each OPEN signal is replayed on M1 bars:

  entry   market -> next bar's open after the message; limit/zone -> first touch
          of the written entry within 12 h (else "no fill"); stop -> first cross
  exits   two ways, both shown
          A  "TP1 only": everything closes at TP1 or the stop
          B  "split":    one equal leg per TP; after TP1 the rest move to breakeven
          provider follow-ups (BE, move SL, close, cancel) are applied at their time,
          to the signal they reply to, else to the channel's latest open signal
  order   inside one bar the stop is assumed hit before any target (conservative)
  horizon 5 days; still open then = closed at that bar's close

Results are in R (1R = the money lost at the signal's own stop) and in $ at a
flat $100 risk per signal, so channels with different stop sizes compare fairly.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import sqlite3
import sys
import time

from backend.copytrade.replay import (FILL_WINDOW_S, GRID_BE, GRID_SCHEME, GRID_SL, HORIZON_S,  # noqa: E402,F401
                                     LATENCY_S, RISK_USD, SCHEMES, Result, Sig, _dd, optimise, replay,
                                     summarise, UTC)

OPT_TITLES = ("WILLY", "SUNNY", "XAUUSD VIP", "GOLD SIGNALS.IO", "GTS VIP", "GROW TRADING", "GOLD XAUUSD BTCUSD",
              "UKOIL + US30", "USOIL+UK OIL", "BRENT")


# ── Telegram + MT5 (only on the server) ─────────────────────────────────────
async def read_channels(inst: str, days: int):
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.tl.types import ChannelParticipantsAdmins

    from backend.copytrade import crypto
    from backend.copytrade.parser import parse_message

    c = sqlite3.connect("file:algoedge.db?mode=ro", uri=True)
    row = c.execute("select api_id, api_hash_enc, session_enc from copytrade_settings "
                    "where session_enc is not null limit 1").fetchone()
    configured = {r[0]: r[1] for r in c.execute("select chat_id, title from copytrade_channels")}
    if not row:
        raise SystemExit("no Telegram login on this instance")
    client = TelegramClient(StringSession(crypto.decrypt(row[2])), int(row[0]), crypto.decrypt(row[1]))
    await client.connect()
    if not await client.is_user_authorized():
        raise SystemExit("Telegram session not authorised")
    since = dt.datetime.now(UTC) - dt.timedelta(days=days)
    chans = []
    async for d in client.iter_dialogs(limit=400):
        if not (d.is_channel or d.is_group):
            continue
        ent = d.entity
        broadcast = bool(getattr(ent, "broadcast", False))
        admins = None
        if not broadcast:
            try:
                admins = {u.id for u in await client.get_participants(ent, filter=ChannelParticipantsAdmins())}
            except Exception:
                admins = None
        sigs: dict[int, Sig] = {}
        order: list[int] = []
        msgs = []
        async for m in client.iter_messages(ent, offset_date=None, reverse=False, limit=4000):
            if m.date < since:
                break
            if not (m.message or "").strip():
                continue
            if admins is not None and getattr(m, "sender_id", None) not in admins and m.sender_id != d.id:
                continue
            msgs.append(m)
        msgs.reverse()
        for m in msgs:
            p = parse_message(m.message)
            t = int(m.date.timestamp())
            rt = getattr(m.reply_to, "reply_to_msg_id", None)
            if p.kind == "OPEN" and p.side and p.sl is not None and not p.add_on:
                sigs[m.id] = Sig(m.id, t, p.side, p.order_type, p.entry, p.sl, list(p.tps), m.message[:160])
                sigs[m.id].symbol = p.symbol or "XAUUSD"
                order.append(m.id)
            elif p.kind in ("BREAKEVEN", "MOVE_SL", "CLOSE", "CANCEL"):
                root = rt if rt in sigs else next((o for o in reversed(order) if t - sigs[o].t < 48 * 3600), None)
                if root:
                    sigs[root].events.append((t, p.kind, p.new_price, p.close_fraction))
        chans.append({"chat_id": d.id, "title": d.name, "joined": getattr(ent, "date", None),
                      "configured": d.id in configured, "messages": len(msgs),
                      "sigs": [sigs[o] for o in order]})
    await client.disconnect()
    return chans


def load_bars(mt5, symbol: str, start: dt.datetime, offset_s: int):
    r = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M1, start, dt.datetime.now(UTC) + dt.timedelta(hours=3))
    if r is None or len(r) == 0:
        return None
    return {"time": [int(x["time"]) - offset_s for x in r], "open": [float(x["open"]) for x in r],
            "high": [float(x["high"]) for x in r], "low": [float(x["low"]) for x in r],
            "close": [float(x["close"]) for x in r]}


def main() -> None:
    inst = sys.argv[1] if len(sys.argv) > 1 else "?"
    days = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 60
    import backend  # noqa: F401  MT5 bridge on Linux
    import MetaTrader5 as mt5

    from backend.copytrade.rules import resolve_symbol
    cache = f"/tmp/bt_sigs_{inst}.json"
    if "--cached" in sys.argv:
        chans = json.load(open(cache))
        for ch in chans:
            ch["joined"] = dt.datetime.fromisoformat(ch["joined"]) if ch["joined"] else None
            ch["sigs"] = [Sig(**{k: v for k, v in x.items()}) for x in ch["sigs"]]
            for x in ch["sigs"]:
                x.events = [tuple(e) for e in x.events]
    else:
        chans = asyncio.run(read_channels(inst, days))
        with open(cache, "w") as f:
            json.dump([{**{k: v for k, v in ch.items() if k != "sigs"},
                        "joined": ch["joined"].isoformat() if ch["joined"] else None,
                        "sigs": [x.__dict__ for x in ch["sigs"]]} for ch in chans], f, default=str)
    names = [s.name for s in (mt5.symbols_get() or [])]
    gold = resolve_symbol("XAUUSD", names, {}) or "XAUUSD"
    tick = mt5.symbol_info_tick(gold)
    offset_s = round((tick.time - time.time()) / 3600) * 3600 if tick and abs(tick.time - time.time()) < 6 * 3600 else 0
    start = dt.datetime.now(UTC) - dt.timedelta(days=days + 1)
    bar_cache: dict[str, dict | None] = {}
    report = {"instance": inst, "days": days, "server_offset_h": offset_s / 3600, "channels": []}
    for ch in chans:
        rows = []
        for s in ch["sigs"]:
            sym = resolve_symbol(str(s.symbol).upper(), names, {}) or None
            if sym not in bar_cache and sym:
                bar_cache[sym] = load_bars(mt5, sym, start, offset_s)
            bars = bar_cache.get(sym) if sym else None
            rows.append((s, replay(s, bars) if bars else Result("NO_DATA")))
        summ = summarise(rows)
        summ.update({"title": ch["title"], "configured": ch["configured"], "messages": ch["messages"],
                     "joined": ch["joined"].strftime("%Y-%m-%d") if ch["joined"] else None,
                     "symbols": sorted({str(s.symbol) for s in ch["sigs"]})})
        report["channels"].append(summ)
        print("BT|" + json.dumps(summ, default=str))
    if "--optimise" in sys.argv:
        spreads = {}
        for sym in [k for k, v in bar_cache.items() if v]:
            si = mt5.symbol_info(sym)
            spreads[sym] = float(si.spread) * float(si.point) if si else 0.0
        def sym_of(x):
            return resolve_symbol(str(x.symbol).upper(), names, {}) or None
        for ch in chans:
            if not any(k in ch["title"].upper() for k in OPT_TITLES):
                continue
            o = optimise(ch["sigs"], lambda x: bar_cache.get(sym_of(x)), lambda x: spreads.get(sym_of(x), 0.0))
            print("OPT|" + json.dumps({"title": ch["title"], "spreads": spreads, **(o or {"note": "too few"})}, default=str))
    report["channels"].sort(key=lambda r: -(r.get("net_r_split") or -999))
    with open(f"/tmp/bt_{inst}.json", "w") as f:
        json.dump(report, f, indent=1, default=str)
    print("BT_DONE", inst, "offset_h", offset_s / 3600, "channels", len(chans))


if __name__ == "__main__":
    main()
