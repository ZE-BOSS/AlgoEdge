"""Telegram alerts for the copy trader, and the end-of-day report.

Until this existed the copy trader only wrote to the log: a skipped signal, a
refused order or a stop hit on a copied trade reached nobody's phone. Every
message names the channel the signal came from.

What sends a message
  signal processed   placed / paper / skipped (with the reason) / failed (with
                     MT5's refusal) / a follow-up applied or not applied
  order changes      found by comparing each order before and after a monitor
                     round: limit filled, order cancelled or expired, leg closed
                     (exit reason + P&L), stop moved (breakeven / trail / his
                     instruction), legs added at market; one message per signal
                     per round, and a total when the whole signal is finished
  daily report       21:00 Africa/Lagos by default: strategies and copy trading,
                     per strategy / per channel, skipped signals by reason, the
                     account balance

Delivery goes through one queue that spaces messages ~1.1 s apart (Telegram
allows about one message a second per chat) and retries once after a 429, so a
busy minute never drops alerts. `rules["notify"] = False` silences a channel.
"""

from __future__ import annotations

import asyncio
import os
from collections import Counter, defaultdict
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from backend.utils.logger import get_logger

logger = get_logger(__name__)

MIN_GAP_S = 1.1
REPORT_TZ = os.getenv("DAILY_REPORT_TZ", "Africa/Lagos")
REPORT_AT = os.getenv("DAILY_REPORT_TIME", "21:00")
_STATE = Path(os.getenv("DAILY_REPORT_STATE", "data/daily_report_last.txt"))

KIND_LABEL = {
    "BREAKEVEN": "Breakeven", "MOVE_SL": "Stop moved", "MOVE_TP": "Target moved",
    "CLOSE": "Close", "CANCEL": "Cancel pending orders", "INFO": "Update",
}
# MT5 deal reasons (DEAL_REASON_*)
DEAL_REASON = {0: "closed manually", 1: "closed from the phone app", 2: "closed from the web terminal",
               3: "closed by the bot", 4: "stop loss", 5: "take profit", 6: "stop out (margin)"}
QUIET_IGNORED = ("not a signal", "already handled", "information only", "unknown channel")


def esc(v: Any) -> str:
    """Telegram Markdown V1 escaping for text we did not write."""
    s = "" if v is None else str(v)
    for ch in ("\\", "_", "*", "`", "["):
        s = s.replace(ch, "\\" + ch)
    return s


def fmt_price(v: Any, digits: int = 2) -> str:
    try:
        return f"{float(v):.{digits}f}"
    except (TypeError, ValueError):
        return "-"


def money(v: float | None) -> str:
    if v is None:
        return "-"
    return f"{'+' if v >= 0 else '-'}${abs(v):,.2f}"


def server_label() -> str:
    return os.getenv("ALGOEDGE_SERVER_LABEL", "").replace("(Linux)", "").strip()


def _header(icon: str, title: str, channel: str) -> str:
    lab = server_label()
    return f"{icon} *{title}* - {esc(channel)}" + (f"  `{esc(lab)}`" if lab else "")


# ── signal processed ────────────────────────────────────────────────────────
def signal_message(channel: str, sig: Any, orders: list[Any], digits: int = 2) -> str | None:
    """One message for a processed Telegram message, or None when there is
    nothing worth a phone buzz (chatter, duplicates)."""
    pd = dict(getattr(sig, "parsed", None) or {})
    kind, status = getattr(sig, "kind", ""), getattr(sig, "status", "")
    detail = getattr(sig, "detail", "") or ""
    acts = [a for a in (getattr(sig, "actions", None) or []) if a]
    what = " ".join(x for x in (pd.get("side"), pd.get("symbol")) if x) or "signal"
    quote = esc((getattr(sig, "text", "") or "").strip().replace("\n", " ")[:160])

    if getattr(sig, "edited", False):
        return edit_message(channel, sig, what)

    if kind == "OPEN":
        if status in ("EXECUTED", "DRY_RUN", "PARTIAL"):
            title = {"EXECUTED": "Copy trade placed", "PARTIAL": "Copy trade partly placed"}.get(
                status, "Paper copy trade (dry run)")
            if detail.startswith("replaced"):
                title = "Trade replaced - signal edited"
            elif detail.startswith("edited signal taken"):
                title = "Copy trade placed - signal corrected by edit"
            icon = {"EXECUTED": "🟢", "PARTIAL": "🟠"}.get(status, "📝")
            lines = [_header(icon, title, channel), f"{esc(what)}"]
            sl = next((o.sl for o in orders if getattr(o, "sl", None)), pd.get("sl"))
            lines.append(f"Stop: {fmt_price(sl, digits)}")
            for o in sorted(orders, key=lambda o: o.leg):
                tp = "runner (no target)" if o.tp is None else fmt_price(o.tp, digits)
                ok = "" if o.status not in ("FAILED",) else "  ❌ refused"
                lines.append(f"  {o.leg}. {o.kind} {o.volume:g} lots @ {fmt_price(o.entry, digits)} -> TP {tp}{ok}")
            risk = next((a.split("risk ")[-1] for a in acts if "risk $" in a), None)
            if risk:
                lines.append(f"Risk if stopped: {esc(risk)}")
            notes = [a[6:] for a in acts if a.startswith("note:")][:3]
            lines += [f"_{esc(n)}_" for n in notes]
            if detail.startswith("replaced"):
                lines.insert(2, f"Edit: {esc(detail.split(':')[0][len('replaced '):].strip('()'))}")
                closed = [a for a in acts if ": close" in a or ": cancel" in a][:6]
                if closed:
                    lines.insert(3, "Old trade: " + "; ".join(esc(a) for a in closed))
            return "\n".join(lines)
        if status == "SKIPPED":
            return "\n".join([_header("⏭️", "Signal skipped", channel), esc(what),
                              f"Why: {esc(detail)}", f"\"{quote}\""])
        if status == "ERROR":
            refusals = [a for a in acts if "FAILED" in a][:4]
            return "\n".join([_header("❌", "Copy trade failed", channel), esc(what), f"Why: {esc(detail)}"]
                             + [f"  {esc(r)}" for r in refusals])
        if status == "SUPERSEDED":
            return None
        return None

    label = KIND_LABEL.get(kind, kind.title() if kind else "Update")
    if status in ("EXECUTED", "DRY_RUN"):
        return "\n".join([_header("🔧", f"{label} applied", channel)] + [f"  {esc(a)}" for a in acts[:6]])
    if status == "IGNORED":
        if any(q in detail for q in QUIET_IGNORED) or kind in ("NONE", "", None):
            return None
        return "\n".join([_header("ℹ️", f"{label} not applied", channel), f"Why: {esc(detail)}", f"\"{quote}\""])
    if status == "ERROR":
        return "\n".join([_header("❌", f"{label} failed", channel), f"Why: {esc(detail)}"])
    return None


# Edits that changed nothing worth a phone buzz: formatting, results written into
# a finished signal ("TP1 ✅"), status notes on chatter.
QUIET_EDITS = ("edit changed nothing", "edit does not change a signal", "edit of a signal that is not open")


def edit_message(channel: str, sig: Any, what: str) -> str | None:
    """A provider edited a message we had already handled."""
    status, detail = getattr(sig, "status", ""), getattr(sig, "detail", "") or ""
    acts = [a for a in (getattr(sig, "actions", None) or []) if a and not a.startswith(("plan:", "note:"))]
    quote = esc((getattr(sig, "text", "") or "").strip().replace("\n", " ")[:160])
    if status in ("EXECUTED", "DRY_RUN", "PARTIAL") and acts:
        failed = [a for a in acts if "FAILED" in a or "not followed" in a or "could not" in a]
        title = "Signal edited - trade updated" + (" (dry run)" if status == "DRY_RUN" else "")
        if failed:
            title = "Signal edited - update partly FAILED"
        lines = [_header("⚠️" if failed else "✏️", title, channel), esc(what)]
        lines += [f"  {esc(a)}" for a in acts[:8]]
        if len(acts) > 8:
            lines.append(f"  ... {len(acts) - 8} more")
        lines.append(f"Now reads: \"{quote}\"")
        return "\n".join(lines)
    if status == "ERROR":
        return "\n".join([_header("❌", "Signal edited - update failed", channel), esc(what),
                           f"Why: {esc(detail)}", f"Now reads: \"{quote}\""])
    if status in ("IGNORED", "SKIPPED") and detail and not any(q in detail for q in QUIET_EDITS):
        return "\n".join([_header("ℹ️", "Signal edited - not applied", channel), esc(what),
                           f"Why: {esc(detail)}", f"Now reads: \"{quote}\""])
    return None


# ── order changes in one monitor round ──────────────────────────────────────
def snapshot(rows: list[Any]) -> dict[int, tuple]:
    return {r.id: (r.status, r.sl, r.tp) for r in rows}


def order_events(before: dict[int, tuple], rows: list[Any], closed: dict[int, dict],
                 digits: int = 2) -> list[str]:
    """Lines for what changed. `closed` maps order id -> {"net", "price", "reason"}
    for legs that closed this round (looked up from MT5 by the caller)."""
    out: list[str] = []
    for r in sorted(rows, key=lambda r: (r.leg, r.id)):
        prev = before.get(r.id)
        if prev is None:
            if r.status in ("OPEN", "PENDING", "PAPER", "PAPER_PENDING"):
                out.append(f"➕ Leg {r.leg} added: {r.kind} {r.volume:g} lots @ {fmt_price(r.entry, digits)}")
            continue
        was, old_sl, _ = prev
        if was == "PENDING" and r.status == "OPEN":
            out.append(f"✅ Leg {r.leg} filled: {r.volume:g} lots @ {fmt_price(r.entry, digits)}")
        elif was in ("PENDING", "PAPER_PENDING") and r.status == "CANCELLED":
            why = (r.detail or "").split("|")[-1].strip() or "cancelled"
            out.append(f"🚫 Leg {r.leg} order removed ({esc(why)})")
        elif was in ("OPEN", "PENDING") and r.status == "CLOSED":
            c = closed.get(r.id) or {}
            how = c.get("reason") or "closed"
            at = f" @ {fmt_price(c['price'], digits)}" if c.get("price") else ""
            icon = "🎯" if how == "take profit" else ("🛑" if (c.get("net") or 0) < 0 else "🏁")
            out.append(f"{icon} Leg {r.leg} closed - {esc(how)}{at}, P&L {money(c.get('net'))}")
        if r.status == "OPEN" and old_sl is not None and r.sl is not None and abs((r.sl or 0) - old_sl) > 1e-9:
            be = r.entry is not None and abs(r.sl - r.entry) <= abs(r.entry) * 0.0002
            out.append(f"🔒 Leg {r.leg} stop {'to breakeven' if be else 'moved'}: "
                       f"{fmt_price(old_sl, digits)} -> {fmt_price(r.sl, digits)}")
    return out


def round_message(channel: str, root: Any, lines: list[str], finished_total: float | None) -> str | None:
    if not lines and finished_total is None:
        return None
    pd = dict(getattr(root, "parsed", None) or {})
    what = " ".join(x for x in (pd.get("side"), pd.get("symbol")) if x) or "signal"
    msg = [_header("📊", esc(what), channel)] + lines
    if finished_total is not None:
        msg.append(f"*Signal finished: {money(finished_total)}*")
    return "\n".join(msg)


# ── delivery ────────────────────────────────────────────────────────────────
class TelegramQueue:
    """Sends one message at a time, MIN_GAP_S apart; one retry after a 429."""

    def __init__(self, send: Callable[[str], Awaitable[dict]] | None = None, gap: float = MIN_GAP_S):
        self._send = send
        self.gap = gap
        self._q: asyncio.Queue[str] | None = None
        self._task: asyncio.Task | None = None
        self.sent = 0
        self.failed = 0

    def _sender(self):
        if self._send is None:
            from backend.services.telegram import telegram_service
            self._send = telegram_service.send_message
        return self._send

    def post(self, text: str | None) -> None:
        if not text:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._q is None:
            self._q = asyncio.Queue(maxsize=500)
        if self._task is None or self._task.done():
            self._task = loop.create_task(self._worker())
        try:
            self._q.put_nowait(text[:4000])
        except asyncio.QueueFull:
            logger.warning("[COPYTRADE] alert queue full - dropped one message")

    async def _worker(self) -> None:
        assert self._q is not None
        while True:
            text = await self._q.get()
            try:
                for attempt in (1, 2):
                    res = await self._sender()(text)
                    limited = any((r or {}).get("status") == 429 for r in (res or {}).get("results", []))
                    if res and res.get("ok"):
                        self.sent += 1
                        break
                    if limited and attempt == 1:
                        await asyncio.sleep(5)
                        continue
                    self.failed += 1
                    break
            except Exception as e:  # noqa: BLE001 - an alert must never take the trader down
                self.failed += 1
                logger.warning(f"[COPYTRADE] alert not sent: {e}")
            await asyncio.sleep(self.gap)

    async def drain(self) -> None:
        """Tests: wait until everything queued has been handed to the sender."""
        while self._q is not None and not self._q.empty():
            await asyncio.sleep(0.01)
        await asyncio.sleep(0)


alerts = TelegramQueue()


# ── end-of-day report ───────────────────────────────────────────────────────
def build_daily_report(day_label: str, strategy_trades: list[dict], copy_legs: list[dict],
                       copy_signals: list[dict], account: dict | None) -> str:
    """Pure text builder. strategy_trades: {strategy, symbol, pnl}; copy_legs:
    {channel, signal_id, pnl}; copy_signals: {channel, status, detail}."""
    lab = server_label()
    out = [f"🗓️ *Daily report - {esc(day_label)}*" + (f"  `{esc(lab)}`" if lab else "")]
    total = 0.0

    def block(title: str, rows: dict[str, list[float]]) -> list[str]:
        nonlocal total
        if not rows:
            return [f"*{title}:* no closed trades"]
        lines = [f"*{title}:*"]
        for name, pnls in sorted(rows.items(), key=lambda kv: -sum(kv[1])):
            w = sum(1 for p in pnls if p > 0.5)
            l = sum(1 for p in pnls if p < -0.5)
            be = len(pnls) - w - l
            lines.append(f"  {esc(name)}: {len(pnls)} trades, {w}W/{l}L" + (f"/{be}BE" if be else "")
                         + f", {money(sum(pnls))}, best {money(max(pnls))}, worst {money(min(pnls))}")
            total += sum(pnls)
        return lines

    strat: dict[str, list[float]] = defaultdict(list)
    for t in strategy_trades:
        strat[f"{t.get('strategy')} {t.get('symbol') or ''}".strip()].append(float(t.get("pnl") or 0.0))
    out += block("Strategies", strat)

    per_signal: dict[tuple, float] = defaultdict(float)
    for leg in copy_legs:
        per_signal[(leg.get("channel") or "?", leg.get("signal_id"))] += float(leg.get("pnl") or 0.0)
    chan: dict[str, list[float]] = defaultdict(list)
    for (c, _), p in per_signal.items():
        chan[c].append(p)
    out += block("Copy trading (per signal)", chan)

    received = Counter(s.get("channel") or "?" for s in copy_signals)
    taken = Counter(s.get("channel") or "?" for s in copy_signals if s.get("status") in ("EXECUTED", "CLOSED", "PARTIAL"))
    skipped: dict[str, Counter] = defaultdict(Counter)
    for s in copy_signals:
        if s.get("status") in ("SKIPPED", "ERROR"):
            skipped[s.get("channel") or "?"][(s.get("detail") or s.get("status") or "?").split(":")[0][:50]] += 1
    if received:
        out.append("*Signals received:*")
        for c, n in received.most_common():
            sk = skipped.get(c)
            why = "; ".join(f"{k} x{v}" for k, v in sk.most_common(3)) if sk else ""
            out.append(f"  {esc(c)}: {n} received, {taken.get(c, 0)} taken"
                       + (f", {sum(sk.values())} skipped/failed ({esc(why)})" if sk else ""))
    out.append(f"*Day total: {money(total)}*")
    if account:
        out.append(f"Balance {account.get('balance', 0):,.2f} | Equity {account.get('equity', 0):,.2f}"
                   f" | Open positions {account.get('positions', 0)}")
    return "\n".join(out)


def next_report_at(now: datetime) -> datetime:
    tz = ZoneInfo(REPORT_TZ)
    hh, mm = (int(x) for x in REPORT_AT.split(":"))
    local = now.astimezone(tz)
    at = datetime.combine(local.date(), dtime(hh, mm), tz)
    if at <= local:
        at += timedelta(days=1)
    return at.astimezone(timezone.utc)


def _already_sent(day: str) -> bool:
    try:
        return _STATE.read_text().strip() == day
    except OSError:
        return False


def _mark_sent(day: str) -> None:
    try:
        _STATE.parent.mkdir(parents=True, exist_ok=True)
        _STATE.write_text(day)
    except OSError as e:
        logger.warning(f"[REPORT] could not record the report date: {e}")


async def collect_and_send_daily_report(day_start_utc: datetime, day_end_utc: datetime, label: str,
                                        send: bool = True) -> str:
    """Gather today's numbers from the database and MT5 and send the report."""
    from sqlalchemy import select

    from backend.copytrade import mt5ops
    from backend.copytrade.models import CopyTradeChannel, CopyTradeOrder, CopyTradeSignal
    from backend.data.database import async_session
    from backend.data.models import Trade

    naive = lambda d: d.astimezone(timezone.utc).replace(tzinfo=None)  # noqa: E731 - DB stores naive UTC
    a, b = naive(day_start_utc), naive(day_end_utc)
    async with async_session() as s:
        trades = (await s.execute(select(Trade).where(
            Trade.status == "CLOSED", Trade.exit_time >= a, Trade.exit_time < b))).scalars().all()
        chans = {c.id: (c.title or str(c.chat_id)) for c in (await s.execute(select(CopyTradeChannel))).scalars().all()}
        sigs = (await s.execute(select(CopyTradeSignal).where(
            CopyTradeSignal.kind == "OPEN", CopyTradeSignal.created_at >= a,
            CopyTradeSignal.created_at < b))).scalars().all()
        legs = (await s.execute(select(CopyTradeOrder).where(
            CopyTradeOrder.status == "CLOSED", CopyTradeOrder.updated_at >= a,
            CopyTradeOrder.updated_at < b))).scalars().all()
    copy_legs = []
    for o in legs:
        info = await mt5ops.closed_deal(o.ticket) if o.ticket else None
        if info and info.get("net") is not None:
            copy_legs.append({"channel": chans.get(o.channel_id, "removed channel"), "signal_id": o.signal_id,
                              "pnl": info["net"]})
    text = build_daily_report(
        label,
        [{"strategy": t.strategy_id, "symbol": t.symbol, "pnl": t.pnl} for t in trades],
        copy_legs,
        [{"channel": chans.get(x.channel_id, "removed channel"), "status": x.status, "detail": x.detail} for x in sigs],
        await mt5ops.account_summary(),
    )
    if send:
        alerts.post(text)
    return text


async def daily_report_loop() -> None:
    """Sleeps until REPORT_AT in REPORT_TZ, sends, repeats. A restart after the
    report went out does not send it twice (the date is kept on disk)."""
    while True:
        at = next_report_at(datetime.now(timezone.utc))
        await asyncio.sleep(max(1.0, (at - datetime.now(timezone.utc)).total_seconds()))
        tz = ZoneInfo(REPORT_TZ)
        local_day = at.astimezone(tz).date()
        key = local_day.isoformat()
        if _already_sent(key):
            continue
        start = datetime.combine(local_day, dtime(0, 0), tz)
        try:
            await collect_and_send_daily_report(start, start + timedelta(days=1), local_day.strftime("%a %d %b %Y"))
            _mark_sent(key)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[REPORT] daily report failed: {e}")
        await asyncio.sleep(60)
