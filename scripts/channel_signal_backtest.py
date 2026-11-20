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
import statistics as st
import sys
import time
from dataclasses import dataclass, field

UTC = dt.timezone.utc
OPT_TITLES = ("WILLY", "SUNNY", "XAUUSD VIP", "GOLD SIGNALS.IO", "GTS VIP", "GROW TRADING", "GOLD XAUUSD BTCUSD",
              "UKOIL + US30", "USOIL+UK OIL", "BRENT")
FILL_WINDOW_S = 12 * 3600
HORIZON_S = 5 * 86400
LATENCY_S = 5
RISK_USD = 100.0


# ── replay core (pure: no Telegram, no MT5) ─────────────────────────────────
@dataclass
class Sig:
    msg_id: int
    t: int                      # unix seconds, UTC
    side: str
    order_type: str
    entry: float | None
    sl: float
    tps: list[float]
    text: str = ""
    symbol: str = "XAUUSD"
    events: list = field(default_factory=list)   # (t, kind, new_price, close_fraction)


@dataclass
class Result:
    status: str                 # FILLED / NO_FILL / NO_DATA
    entry: float | None = None
    fill_t: int | None = None
    r_tp1: float | None = None
    r_split: float | None = None
    tp1_hit: bool = False
    tps_hit: int = 0
    mfe_r: float = 0.0
    mae_r: float = 0.0
    exit_t: int | None = None
    how: str = ""


def _first_index(times, t):
    lo, hi = 0, len(times)
    while lo < hi:
        mid = (lo + hi) // 2
        if times[mid] < t:
            lo = mid + 1
        else:
            hi = mid
    return lo


SCHEMES = {"equal": None, "tp1": [1.0], "front": [0.5, 0.3, 0.2], "back": [0.2, 0.3, 0.5]}


def _weights(scheme, n):
    w = SCHEMES.get(scheme) if isinstance(scheme, str) else scheme
    if not w:
        return [1.0 / n] * n
    w = (list(w) + [0.0] * n)[:n]
    tot = sum(w) or 1.0
    return [x / tot for x in w]


def replay(s: Sig, bars, sl_frac: float = 1.0, scheme="equal", be_after_tp1: bool = True,
           spread: float = 0.0) -> Result:
    """bars: dict of lists time/open/high/low/close, M1, time ascending (UTC seconds).

    sl_frac   our stop as a fraction of the provider's distance (1.0 = theirs). The
              result is in R of OUR stop, i.e. a fixed $ risk with the lot resized.
    scheme    how the position is split over the provider's targets (SCHEMES)
    be_after_tp1  move the rest to breakeven once TP1 fills
    spread    one round-trip spread in price, charged to every filled trade"""
    T, O, H, L, C = bars["time"], bars["open"], bars["high"], bars["low"], bars["close"]
    i = _first_index(T, s.t + LATENCY_S)
    if i >= len(T) or T[i] - s.t > 3 * 3600:
        return Result("NO_DATA")
    buy = s.side == "BUY"
    # The signal's own numbers must be near the market it is replayed on. A
    # wrong symbol (message names none -> gold assumed) or a broker whose price
    # differs from the channel's feed would otherwise produce nonsense R.
    mkt = O[i]
    if (s.entry is not None and abs(s.entry - mkt) / mkt > 0.02) or abs(s.sl - mkt) / mkt > 0.05:
        return Result("PRICE_MISMATCH")
    # ---- entry
    fill_i, entry = None, None
    if s.order_type == "MARKET" or s.entry is None:
        fill_i, entry = i, O[i]
    else:
        e = s.entry
        for k in range(i, len(T)):
            if T[k] - s.t > FILL_WINDOW_S:
                break
            if any(ev[1] == "CANCEL" and ev[0] <= T[k] for ev in s.events):
                break
            if s.order_type == "LIMIT":
                if k == i and ((buy and O[k] <= e) or (not buy and O[k] >= e)):
                    fill_i, entry = k, O[k]          # already at/through the price: market fill
                    break
                if (buy and L[k] <= e) or (not buy and H[k] >= e):
                    fill_i, entry = k, e
                    break
            else:  # STOP
                if (buy and H[k] >= e) or (not buy and L[k] <= e):
                    fill_i, entry = k, max(e, O[k]) if buy else min(e, O[k])
                    break
        if fill_i is None:
            return Result("NO_FILL")
    if (buy and s.sl >= entry) or (not buy and s.sl <= entry):
        return Result("NO_FILL", how="stop on the wrong side of the fill")
    sgn = 1 if buy else -1
    our_sl = entry - sgn * sl_frac * abs(entry - s.sl)
    risk = abs(entry - our_sl)
    if risk <= 0:
        return Result("NO_FILL", how="zero risk")
    tighter = (lambda a, b: max(a, b)) if buy else (lambda a, b: min(a, b))
    tps = [tp for tp in s.tps if (tp - entry) * sgn > 0]
    n_legs = max(1, len(tps))
    legs_open = [True] * n_legs
    leg_r = [0.0] * n_legs
    weights = _weights(scheme, n_legs)
    sl_split = our_sl
    sl_a, done_a, r_a = our_sl, False, None
    tp1_hit, hits, mfe, mae = False, 0, 0.0, 0.0
    evs = sorted(s.events)
    ev_j = 0
    k = fill_i + 1 if s.order_type != "MARKET" else fill_i
    last = fill_i
    while k < len(T) and T[k] - T[fill_i] <= HORIZON_S and (any(legs_open) or not done_a):
        # provider follow-ups that happened before this bar
        while ev_j < len(evs) and evs[ev_j][0] <= T[k]:
            _, kind, newp, frac = evs[ev_j]
            ev_j += 1
            if kind == "BREAKEVEN":
                sl_split = tighter(sl_split, entry)
                if not done_a:
                    sl_a = tighter(sl_a, entry)
            elif kind == "MOVE_SL" and newp:
                # the provider's move, unless our own stop is already tighter
                sl_split = newp if sl_frac >= 1.0 else tighter(sl_split, newp)
                if not done_a:
                    sl_a = newp if sl_frac >= 1.0 else tighter(sl_a, newp)
            elif kind == "CLOSE":
                px = O[k]
                r_now = (px - entry) * sgn / risk
                frac = frac or 1.0
                open_ix = [x for x in range(n_legs) if legs_open[x]]
                n_close = len(open_ix) if frac >= 0.99 else max(1, round(len(open_ix) * frac))
                for x in open_ix[-n_close:] if frac < 0.99 else open_ix:
                    legs_open[x], leg_r[x] = False, r_now
                if frac >= 0.99 and not done_a:
                    done_a, r_a = True, r_now
        hi, lo = H[k], L[k]
        fav = (hi - entry) / risk if buy else (entry - lo) / risk
        adv = (entry - lo) / risk if buy else (hi - entry) / risk
        # A: TP1 only
        if not done_a:
            if (lo <= sl_a) if buy else (hi >= sl_a):
                done_a, r_a = True, (sl_a - entry) * sgn / risk
            elif tps and ((hi >= tps[0]) if buy else (lo <= tps[0])):
                done_a, r_a = True, (tps[0] - entry) * sgn / risk
        # B: split
        if any(legs_open):
            if (lo <= sl_split) if buy else (hi >= sl_split):
                for x in range(n_legs):
                    if legs_open[x]:
                        legs_open[x], leg_r[x] = False, (sl_split - entry) * sgn / risk
            else:
                for x, tp in enumerate(tps):
                    if legs_open[x] and ((hi >= tp) if buy else (lo <= tp)):
                        legs_open[x], leg_r[x] = False, (tp - entry) * sgn / risk
                        hits += 1
                        if x == 0:
                            tp1_hit = True
                            if be_after_tp1:
                                sl_split = tighter(sl_split, entry)   # breakeven after TP1
        if any(legs_open) or not done_a:
            mfe, mae = max(mfe, fav), max(mae, adv)
        last = k
        k += 1
    # horizon: mark what is left at the last close
    px = C[last]
    for x in range(n_legs):
        if legs_open[x]:
            leg_r[x] = (px - entry) * sgn / risk
    if not done_a:
        r_a = (px - entry) * sgn / risk
    cost = spread / risk
    return Result("FILLED", entry=entry, fill_t=T[fill_i], r_tp1=r_a - cost,
                  r_split=sum(w * r for w, r in zip(weights, leg_r)) - cost,
                  tp1_hit=tp1_hit, tps_hit=hits, mfe_r=mfe, mae_r=mae, exit_t=T[last],
                  how="horizon" if any(legs_open) else "")


def summarise(rows: list[tuple[Sig, Result]]) -> dict:
    filled = [(s, r) for s, r in rows if r.status == "FILLED"]
    out = {"signals": len(rows), "filled": len(filled),
           "no_fill": sum(r.status == "NO_FILL" for _, r in rows),
           "no_price_data": sum(r.status == "NO_DATA" for _, r in rows),
           "price_mismatch": sum(r.status == "PRICE_MISMATCH" for _, r in rows)}
    if not filled:
        return out
    filled.sort(key=lambda x: x[1].exit_t or 0)
    a = [r.r_tp1 for _, r in filled]
    b = [r.r_split for _, r in filled]

    def dd(xs):
        peak = eq = m = 0.0
        for x in xs:
            eq += x
            peak = max(peak, eq)
            m = max(m, peak - eq)
        return m

    def streak(xs):
        best = cur = 0
        for x in xs:
            cur = cur + 1 if x < -0.05 else 0
            best = max(best, cur)
        return best

    wins = [x for x in b if x > 0.05]
    losses = [x for x in b if x < -0.05]
    bi = max(range(len(b)), key=lambda i: b[i])
    wi = min(range(len(b)), key=lambda i: b[i])
    winners_mae = [r.mae_r for _, r in filled if r.tp1_hit]
    days = max(1.0, (max(x.t for x, _ in rows) - min(x.t for x, _ in rows)) / 86400)
    fmt = lambda t: dt.datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%d")
    out.update({
        "first": fmt(min(x.t for x, _ in rows)), "last": fmt(max(x.t for x, _ in rows)),
        "signals_per_day": round(len(rows) / days, 2),
        "tp1_hit_rate": round(sum(r.tp1_hit for _, r in filled) / len(filled), 3),
        "split_win_rate": round(len(wins) / max(1, len(wins) + len(losses)), 3),
        "split_wins": len(wins), "split_losses": len(losses), "split_be": len(b) - len(wins) - len(losses),
        "avg_r_tp1_only": round(st.mean(a), 3), "avg_r_split": round(st.mean(b), 3),
        "net_r_split": round(sum(b), 2), "net_usd_split": round(sum(b) * RISK_USD, 2),
        "net_r_tp1_only": round(sum(a), 2), "net_usd_tp1_only": round(sum(a) * RISK_USD, 2),
        "profit_factor_split": round(sum(wins) / -sum(losses), 2) if losses else None,
        "avg_win_r": round(st.mean(wins), 2) if wins else None,
        "avg_loss_r": round(st.mean(losses), 2) if losses else None,
        "best_r": round(b[bi], 2), "best_date": fmt(filled[bi][0].t),
        "worst_r": round(b[wi], 2), "worst_date": fmt(filled[wi][0].t),
        "max_dd_r": round(dd(b), 2), "max_dd_usd": round(dd(b) * RISK_USD, 2),
        "max_losing_streak": streak(b),
        "median_winner_mae_r": round(st.median(winners_mae), 2) if winners_mae else None,
        "p80_winner_mae_r": round(sorted(winners_mae)[int(0.8 * (len(winners_mae) - 1))], 2) if winners_mae else None,
        "median_stop_distance": round(st.median(abs(s.entry - s.sl) if s.entry else abs(r.entry - s.sl)
                                                for s, r in filled), 2),
        "still_open_at_horizon": sum(r.how == "horizon" for _, r in filled),
    })
    return out


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


GRID_SL = (0.3, 0.4, 0.5, 0.6, 0.75, 1.0)
GRID_SCHEME = ("equal", "tp1", "front", "back")
GRID_BE = (True, False)


def optimise(sigs: list, bars_for, spread_for) -> dict | None:
    """Tune on the first 70% of a channel's signals, judge on the last 30%."""
    sigs = sorted(sigs, key=lambda x: x.t)
    cut = int(len(sigs) * 0.7)
    train, test = sigs[:cut], sigs[cut:]

    def run(group, f, sc, be):
        rs = []
        for x in group:
            b = bars_for(x)
            if not b:
                continue
            r = replay(x, b, f, sc, be, spread_for(x))
            if r.status == "FILLED":
                rs.append(r.r_split)
        return rs

    rows = []
    for f in GRID_SL:
        for sc in GRID_SCHEME:
            for be in GRID_BE:
                a, b = run(train, f, sc, be), run(test, f, sc, be)
                if len(a) < 15 or not b:
                    continue
                rows.append({"sl": f, "split": sc, "be": be, "n_train": len(a), "n_test": len(b),
                             "train_R": round(st.mean(a), 3), "test_R": round(st.mean(b), 3),
                             "test_net_R": round(sum(b), 2), "test_wr": round(sum(x > 0.05 for x in b) / len(b), 3),
                             "test_dd_R": round(_dd(b), 2)})
    if not rows:
        return None
    base = next(r for r in rows if r["sl"] == 1.0 and r["split"] == "equal" and r["be"])
    best = max(rows, key=lambda r: r["train_R"])
    near = [r for r in rows if r["split"] == best["split"] and r["be"] == best["be"]
            and abs(GRID_SL.index(r["sl"]) - GRID_SL.index(best["sl"])) <= 1]
    top = sorted(rows, key=lambda r: -r["train_R"])[:5]
    return {"baseline": base, "best": best, "neighbours_test_R": round(st.mean(r["test_R"] for r in near), 3),
            "top5": top, "first_test_day": dt.datetime.fromtimestamp(test[0].t, UTC).strftime("%Y-%m-%d")}


def _dd(xs):
    peak = eq = m = 0.0
    for x in xs:
        eq += x
        peak = max(peak, eq)
        m = max(m, peak - eq)
    return m


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
