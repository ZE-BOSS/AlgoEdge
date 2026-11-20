"""Copy-trading backtest: replay Telegram channels' signals on MT5 history and
run them as a real-money portfolio, alone or alongside saved strategy backtests.

    signals   the channel's own messages (read-only Telegram history, cached for an
              hour), parsed by the live copy trader's parser; follow-ups (breakeven,
              move SL, close, cancel) are attached to the signal they reply to, else
              to the channel's latest signal of the last 48 h
    prices    M1 bars from this server's MT5 account (broker symbol names, broker
              spread charged once per trade), converted to UTC
    rules     each channel's own copy rules unless overridden: sl_fraction, split
              weights, auto breakeven after TP1, which follow-ups to obey,
              max open signals
    money     starting balance, risk % per signal (fixed or compounding), signals
              taken in time order; a channel at its max open signals skips the next
              one, as the live copier would

Each trade row compares the signal with the market: the price when the message
arrived, the written entry, where we filled, the slippage (price and R), the
wait for the fill, how it ended - and, when the live copier took the same
message on this account, what it actually made.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from backend.copytrade.replay import SCHEMES, Result, Sig, replay, summarise
from backend.utils.logger import get_logger

logger = get_logger(__name__)
UTC = timezone.utc
CACHE_DIR = Path(os.getenv("COPYTRADE_BT_CACHE", "data/copytrade_bt_cache"))
CACHE_TTL_S = 3600
FOLLOW_UP_WINDOW_S = 48 * 3600
MAX_DAYS = 365
LIVE_LOOKUPS = 300


# ── messages -> signals (pure) ──────────────────────────────────────────────
def messages_to_signals(msgs: list[dict], only_admins: bool = True, follow: dict | None = None) -> list[Sig]:
    """msgs oldest first: {id, date (ISO), text, reply_to, from_admin}.
    follow: which follow-up kinds to keep, e.g. {"BREAKEVEN": True, "MOVE_SL": False}."""
    from backend.copytrade.parser import parse_message

    follow = follow or {}
    sigs: dict[int, Sig] = {}
    order: list[int] = []
    for m in msgs:
        if only_admins and not m.get("from_admin", True):
            continue
        text = m.get("text") or ""
        if not text.strip() or not m.get("date"):
            continue
        p = parse_message(text)
        t = int(datetime.fromisoformat(m["date"]).timestamp())
        rt = m.get("reply_to")
        if p.kind == "OPEN" and p.side and p.sl is not None and not getattr(p, "add_on", False):
            s = Sig(int(m["id"]), t, p.side, p.order_type, p.entry, float(p.sl), [float(x) for x in p.tps],
                    text[:200])
            s.symbol = p.symbol or "XAUUSD"
            sigs[s.msg_id] = s
            order.append(s.msg_id)
        elif p.kind in ("BREAKEVEN", "MOVE_SL", "CLOSE", "CANCEL"):
            if follow.get(p.kind, True) is False:
                continue
            root = rt if rt in sigs else next((o for o in reversed(order) if t - sigs[o].t < FOLLOW_UP_WINDOW_S), None)
            if root:
                sigs[root].events.append((t, p.kind, p.new_price, p.close_fraction))
    return [sigs[o] for o in order]


def settings_from_rules(rules: dict, override: dict | None = None) -> dict:
    """The replay settings a channel's copy rules imply, with explicit overrides."""
    o = dict(override or {})
    w = [float(x) for x in (rules.get("split_weights") or []) if x]
    out = {
        "sl_fraction": float(rules.get("sl_fraction") or 1.0),
        "scheme": w or "equal",
        "be_after_tp1": int(rules.get("be_after_tp") or 0) == 1,
        "follow": {"BREAKEVEN": bool(rules.get("follow_breakeven", True)), "MOVE_SL": bool(rules.get("follow_move_sl", True)),
                   "CLOSE": bool(rules.get("follow_close", True)), "CANCEL": bool(rules.get("follow_cancel", True))},
        "max_open": int(rules.get("max_open_signals") or 0),
        "only_admins": bool(rules.get("only_admins", True)),
    }
    for k in ("sl_fraction", "be_after_tp1", "max_open"):
        if o.get(k) is not None:
            out[k] = o[k]
    if o.get("scheme"):
        out["scheme"] = o["scheme"] if isinstance(o["scheme"], list) else str(o["scheme"])
        if isinstance(out["scheme"], str) and out["scheme"] not in SCHEMES:
            out["scheme"] = "equal"
    return out


def trade_row(source: str, s: Sig, r: Result, bars: dict | None, risk_usd: float = 100.0) -> dict:
    """One signal, signal vs market."""
    row: dict[str, Any] = {
        "source": source, "msg_id": s.msg_id, "time": _iso(s.t), "symbol": s.symbol, "side": s.side,
        "order_type": s.order_type, "signal_entry": s.entry, "signal_sl": s.sl, "signal_tps": s.tps,
        "follow_ups": [e[1] for e in s.events], "status": r.status, "text": s.text,
    }
    if bars:
        from backend.copytrade.replay import _first_index
        i = _first_index(bars["time"], s.t)
        if i < len(bars["time"]) and bars["time"][i] - s.t < 3 * 3600:
            row["market_at_signal"] = bars["open"][i]
    if r.status != "FILLED":
        row["note"] = r.how or {"NO_FILL": "price never reached the entry", "NO_DATA": "no price history",
                                "PRICE_MISMATCH": "signal prices far from this broker's market"}.get(r.status, "")
        return row
    buy = s.side == "BUY"
    risk_px = abs(r.entry - s.sl) if r.entry is not None else None
    slip = None
    if s.entry is not None and r.entry is not None:
        slip = (r.entry - s.entry) if buy else (s.entry - r.entry)       # + = worse than written
    row.update({
        "fill_price": r.entry, "fill_time": _iso(r.fill_t), "wait_min": round((r.fill_t - s.t) / 60, 1) if r.fill_t else None,
        "slippage": round(slip, 5) if slip is not None else None,
        "slippage_r": round(slip / risk_px, 3) if slip is not None and risk_px else None,
        "exit_time": _iso(r.exit_t), "exit_reason": r.exit_reason, "exit_price": r.exit_price,
        "tps_hit": r.tps_hit, "tp1_hit": r.tp1_hit, "mfe_r": round(r.mfe_r, 2), "mae_r": round(r.mae_r, 2),
        "r": round(r.r_split, 3), "r_tp1_only": round(r.r_tp1, 3), "usd_at_fixed_risk": round(r.r_split * risk_usd, 2),
        "_entry_t": r.fill_t, "_exit_t": r.exit_t, "_r": r.r_split,      # full precision for the money
    })
    return row


# ── portfolio (pure) ────────────────────────────────────────────────────────
def simulate_portfolio(trades: list[dict], balance: float, risk_pct: float, compound: bool = True,
                       max_open: dict[str, int] | None = None, max_open_total: int = 0) -> dict:
    """trades: {source, _entry_t, _exit_t, r}. Money in time order, as the account
    would see it: risk is fixed when a trade opens, P&L lands when it closes."""
    max_open = max_open or {}
    events = []
    for k, t in enumerate(trades):
        if t.get("_entry_t") is None or t.get("_exit_t") is None or t.get("_r", t.get("r")) is None:
            continue
        events.append((t["_entry_t"], 1, k))
        events.append((max(t["_exit_t"], t["_entry_t"]), 0, k))        # closes before opens at the same second
    events.sort()
    equity = peak = float(balance)
    max_dd_usd = max_dd_pct = 0.0
    open_risk: dict[int, float] = {}
    open_by: dict[str, int] = defaultdict(int)
    skipped: dict[str, int] = defaultdict(int)
    taken: set[int] = set()
    by_source: dict[str, dict] = defaultdict(lambda: {"trades": 0, "wins": 0, "losses": 0, "be": 0, "pnl": 0.0})
    curve = [{"t": _iso(events[0][0]) if events else None, "equity": round(equity, 2)}]
    daily: dict[str, float] = defaultdict(float)
    max_conc = max_conc_risk = 0.0
    for t, is_open, k in events:
        tr = trades[k]
        src = tr["source"]
        if is_open:
            cap = int(max_open.get(src) or 0)
            if (cap and open_by[src] >= cap) or (max_open_total and len(open_risk) >= max_open_total):
                skipped[src] += 1
                continue
            base = equity if compound else balance
            open_risk[k] = max(0.0, base * risk_pct / 100.0)
            open_by[src] += 1
            taken.add(k)
            max_conc = max(max_conc, len(open_risk))
            max_conc_risk = max(max_conc_risk, sum(open_risk.values()) / max(equity, 1e-9) * 100)
            continue
        if k not in taken:
            continue
        risk = open_risk.pop(k)
        open_by[src] -= 1
        pnl = tr.get("_r", tr["r"]) * risk
        equity += pnl
        tr["portfolio_risk_usd"], tr["portfolio_pnl"] = round(risk, 2), round(pnl, 2)
        b = by_source[src]
        b["trades"] += 1
        b["pnl"] += pnl
        outcome = "wins" if pnl > 0.005 * risk else "losses" if pnl < -0.005 * risk else "be"
        b[outcome] += 1
        daily[_iso(t)[:10]] += pnl
        peak = max(peak, equity)
        max_dd_usd = max(max_dd_usd, peak - equity)
        max_dd_pct = max(max_dd_pct, (peak - equity) / peak * 100 if peak > 0 else 0)
        curve.append({"t": _iso(t), "equity": round(equity, 2), "source": src})
    for k, tr in enumerate(trades):
        if k not in taken and tr.get("status") == "FILLED":
            tr["portfolio_skipped"] = True
    days = sorted(daily.items())
    return {
        "start_balance": round(balance, 2), "end_balance": round(equity, 2),
        "net": round(equity - balance, 2), "return_pct": round((equity - balance) / balance * 100, 2) if balance else None,
        "max_dd_usd": round(max_dd_usd, 2), "max_dd_pct": round(max_dd_pct, 2),
        "trades": len(taken), "skipped_max_open": dict(skipped),
        "max_concurrent_trades": int(max_conc), "max_concurrent_risk_pct": round(max_conc_risk, 2),
        "best_day": max(days, key=lambda d: d[1]) if days else None,
        "worst_day": min(days, key=lambda d: d[1]) if days else None,
        "losing_days": sum(1 for _, v in days if v < 0), "trading_days": len(days),
        "by_source": {k: {**v, "pnl": round(v["pnl"], 2)} for k, v in by_source.items()},
        "curve": curve,
    }


# ── data (server only) ──────────────────────────────────────────────────────
def _cache_path(chat_id: int) -> Path:
    return CACHE_DIR / f"{int(chat_id)}.json"


async def channel_history(user_id: str, chat_id: int, days: int, refresh: bool = False) -> dict:
    """Messages of the last `days` days, from a cache kept for an hour."""
    from backend.copytrade.telegram_client import telegram_manager

    since = datetime.now(UTC) - timedelta(days=days)
    p = _cache_path(chat_id)
    if not refresh and p.exists():
        try:
            c = json.loads(p.read_text())
            if time.time() - c["fetched_at"] < CACHE_TTL_S and c["since"] <= since.timestamp():
                c["messages"] = [m for m in c["messages"] if datetime.fromisoformat(m["date"]) >= since]
                return c
        except Exception:  # noqa: BLE001 - a bad cache file is just refetched
            pass
    h = await telegram_manager.history_since(user_id, int(chat_id), since)
    h.update({"fetched_at": time.time(), "since": since.timestamp()})
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(h))
    except OSError as e:
        logger.warning(f"[CT-BT] cache not written: {e}")
    return h


async def _mt5():
    import backend  # noqa: F401 - installs the MT5 bridge on Linux
    import MetaTrader5 as mt5
    return mt5


async def load_m1(symbol: str, start: datetime) -> dict | None:
    from backend.mt5.data_fetcher import detect_server_utc_offset_hours
    from backend.mt5.executor import run_mt5

    mt5 = await _mt5()
    end = datetime.now(UTC) + timedelta(hours=3)
    r = await run_mt5(mt5.copy_rates_range, symbol, mt5.TIMEFRAME_M1, start, end)
    if r is None or len(r) == 0:
        return None
    off = int(round(float(await asyncio.to_thread(detect_server_utc_offset_hours)) * 3600))
    return {"time": [int(x["time"]) - off for x in r], "open": [float(x["open"]) for x in r],
            "high": [float(x["high"]) for x in r], "low": [float(x["low"]) for x in r],
            "close": [float(x["close"]) for x in r]}


async def spread_price(symbol: str) -> float:
    from backend.mt5.executor import run_mt5
    mt5 = await _mt5()
    si = await run_mt5(mt5.symbol_info, symbol)
    return float(si.spread) * float(si.point) if si else 0.0


async def earliest_m1(symbol: str) -> str | None:
    """How far back this account's M1 history goes (probed, not downloaded)."""
    from backend.mt5.executor import run_mt5
    mt5 = await _mt5()
    now = datetime.now(UTC)
    best = None
    for d in (7, 30, 60, 90, 180, 365):
        a = now - timedelta(days=d)
        r = await run_mt5(mt5.copy_rates_range, symbol, mt5.TIMEFRAME_M1, a, a + timedelta(days=3))
        if r is None or len(r) == 0:
            break
        best = int(r[0]["time"])
    return _iso(best)[:10] if best else None


async def data_range(user_id: str, channel_ids: list[int]) -> dict:
    """What a backtest can cover: each channel's first message and signals per
    symbol over the last 30 days, and how far back M1 prices go for those symbols."""
    from sqlalchemy import select

    from backend.copytrade import mt5ops
    from backend.copytrade.models import CopyTradeChannel
    from backend.copytrade.rules import resolve_symbol
    from backend.data.database import async_session

    async with async_session() as s:
        chans = (await s.execute(select(CopyTradeChannel).where(
            CopyTradeChannel.user_id == user_id, CopyTradeChannel.id.in_(channel_ids)))).scalars().all()
    names = await mt5ops.available_symbols()
    out, syms = [], set()
    for ch in chans:
        h = await channel_history(user_id, ch.chat_id, 30)
        sigs = messages_to_signals(h["messages"])
        per = defaultdict(int)
        for x in sigs:
            per[x.symbol] += 1
        syms |= set(per)
        out.append({"channel_id": ch.id, "title": ch.title, "first_message": (h.get("first_message") or "")[:10] or None,
                    "signals_last_30d": len(sigs), "symbols": dict(per)})
    prices = {}
    for sym in sorted(syms):
        b = resolve_symbol(sym.upper(), names, {})
        prices[sym] = {"broker_symbol": b, "m1_from": await earliest_m1(b) if b else None}
    return {"channels": out, "prices": prices}


async def _live_results(chat_id: int, msg_ids: list[int]) -> dict[int, dict]:
    """What the live copier did with these messages on this account."""
    from sqlalchemy import select

    from backend.copytrade import mt5ops
    from backend.copytrade.models import CopyTradeOrder, CopyTradeSignal
    from backend.data.database import async_session

    out: dict[int, dict] = {}
    async with async_session() as s:
        rows = (await s.execute(select(CopyTradeSignal).where(
            CopyTradeSignal.chat_id == chat_id, CopyTradeSignal.msg_id.in_(msg_ids),
            CopyTradeSignal.kind == "OPEN"))).scalars().all()
        lookups = 0
        for sig in rows:
            orders = (await s.execute(select(CopyTradeOrder).where(CopyTradeOrder.signal_id == sig.id))).scalars().all()
            net, n = 0.0, 0
            for o in orders:
                if o.status == "CLOSED" and o.ticket and lookups < LIVE_LOOKUPS:
                    lookups += 1
                    c = await mt5ops.closed_deal(o.ticket)
                    if c and c.get("net") is not None:
                        net += c["net"]
                        n += 1
            out[sig.msg_id] = {"status": sig.status, "detail": sig.detail, "legs_closed": n,
                               "net": round(net, 2) if n else None,
                               "fills": [o.entry for o in orders if o.status in ("OPEN", "CLOSED")][:4]}
    return out


async def run(user_id: str, channel_ids: list[int], days: int = 60, balance: float = 10000.0,
              risk_pct: float = 1.0, compound: bool = True, override: dict | None = None,
              strategy_backtest_ids: list[str] | None = None, max_open_total: int = 0,
              refresh: bool = False) -> dict:
    from sqlalchemy import select

    from backend.copytrade import mt5ops
    from backend.copytrade.models import CopyTradeChannel, CopyTradeSettings
    from backend.copytrade.rules import merge_rules, resolve_symbol
    from backend.data.database import async_session
    from backend.data.models import BacktestRun, BacktestTrade

    days = max(1, min(int(days), MAX_DAYS))
    start = datetime.now(UTC) - timedelta(days=days + 1)
    async with async_session() as s:
        st = await s.get(CopyTradeSettings, user_id)
        chans = (await s.execute(select(CopyTradeChannel).where(
            CopyTradeChannel.user_id == user_id, CopyTradeChannel.id.in_(channel_ids)))).scalars().all()
        strat_runs, strat_trades = [], []
        if strategy_backtest_ids:
            strat_runs = (await s.execute(select(BacktestRun).where(
                BacktestRun.user_id == user_id, BacktestRun.id.in_(strategy_backtest_ids)))).scalars().all()
            for br in strat_runs:
                for t in (await s.execute(select(BacktestTrade).where(BacktestTrade.backtest_id == br.id))).scalars().all():
                    strat_trades.append((br, t))
    names = await mt5ops.available_symbols()
    bars_cache: dict[str, dict | None] = {}
    spreads: dict[str, float] = {}
    channels_out, all_trades, caps = [], [], {}
    for ch in chans:
        rules = merge_rules(st.rules if st else None, ch.rules)
        cfg = settings_from_rules(rules, override)
        h = await channel_history(user_id, ch.chat_id, days, refresh=refresh)
        sigs = messages_to_signals(h["messages"], cfg["only_admins"], cfg["follow"])
        title = ch.title or str(ch.chat_id)
        caps[title] = cfg["max_open"]
        pairs, rows = [], []
        for x in sigs:
            sym = resolve_symbol(str(x.symbol).upper(), names, rules) or None
            if sym and sym not in bars_cache:
                bars_cache[sym] = await load_m1(sym, start)
                spreads[sym] = await spread_price(sym) if bars_cache[sym] else 0.0
            bars = bars_cache.get(sym) if sym else None
            r = replay(x, bars, cfg["sl_fraction"], cfg["scheme"], cfg["be_after_tp1"], spreads.get(sym, 0.0)) \
                if bars else Result("NO_DATA", how=f"{x.symbol} is not on this account" if not sym else "no price history")
            pairs.append((x, r))
            rows.append(trade_row(title, x, r, bars))
        live = await _live_results(ch.chat_id, [x.msg_id for x in sigs]) if sigs else {}
        for row in rows:
            if row["msg_id"] in live:
                row["live"] = live[row["msg_id"]]
        summ = summarise(pairs)
        summ.update({"channel_id": ch.id, "title": title, "settings": {k: v for k, v in cfg.items() if k != "follow"},
                     "follow": cfg["follow"], "first_message": (h.get("first_message") or "")[:10] or None,
                     "history_capped": h.get("capped", False)})
        channels_out.append(summ)
        all_trades += rows
    strategies_out = []
    for br in strat_runs:
        src = br.title or f"{br.strategy_id} {br.symbol}"
        n = 0
        for b2, t in strat_trades:
            if b2.id != br.id or t.pnl_r is None or not t.entry_time or not t.exit_time:
                continue
            n += 1
            all_trades.append({"source": src, "kind": "strategy", "symbol": t.symbol, "side": t.direction,
                               "time": t.entry_time.isoformat(), "status": "FILLED", "fill_price": t.entry_price,
                               "exit_price": t.exit_price, "exit_reason": t.exit_reason, "r": float(t.pnl_r),
                               "_entry_t": int(t.entry_time.replace(tzinfo=UTC).timestamp()),
                               "_exit_t": int(t.exit_time.replace(tzinfo=UTC).timestamp())})
        strategies_out.append({"backtest_id": br.id, "title": src, "trades": n,
                               "from": br.start_date.isoformat()[:10], "to": br.end_date.isoformat()[:10]})
    all_trades.sort(key=lambda r: r.get("_entry_t") or 0)
    pf = simulate_portfolio(all_trades, balance, risk_pct, compound, caps, max_open_total)
    for r in all_trades:
        r.pop("_entry_t", None)
        r.pop("_exit_t", None)
        r.pop("_r", None)
    channels_out.sort(key=lambda c: -(c.get("net_r_split") or -1e9))
    return {"days": days, "balance": balance, "risk_pct": risk_pct, "compound": compound,
            "channels": channels_out, "strategies": strategies_out, "portfolio": pf, "trades": all_trades,
            "method": "M1 bars, stop assumed before target inside a bar, limit fill window 12 h, 5-day horizon, "
                      "broker spread charged once per trade, provider follow-ups applied at their time"}


def _iso(t: int | float | None) -> str | None:
    return datetime.fromtimestamp(t, UTC).isoformat() if t else None
