"""
backend/groups/client_guard.py

Runs inside a VVIP client's own AlgoEdge instance (env VVIP_CLIENT=<id>).

  * Starts the bot by itself once MT5 is connected: a client instance has
    nobody to press Start, and it trades only what live traded (follower
    sync, services/signal_sync.py).
  * Enforces the client's limits on the real account: the daily loss stop
    pauses trading until the next UTC day; the drawdown stop (from the
    account's equity peak) pauses it until the admin resumes it.
  * Talks to the live instance through two small files in the shared folder:
        <inst>.control.json   written by live: wanted state, risk %, limits
        <inst>.status.json    written here: balance, equity, peak, drawdown,
                              today's P/L, open positions, state and reason
    Live's group bot reads the status into the client's record (admin page,
    the client's portal) and tells the admin and the client when a stop fires.

Stopping never closes positions: open trades keep their stops and targets.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from backend.utils.logger import get_logger

logger = get_logger(__name__)

SHARED = Path(os.getenv("VVIP_SHARED_DIR", "/opt/algoedge/shared/vvip"))


@dataclass
class GuardState:
    peak: float = 0.0
    day: str = ""
    day_start: float = 0.0
    state: str = "ACTIVE"          # ACTIVE | PAUSED | DAY_STOP | DD_STOP | STOPPED
    reason: str = ""
    extra: dict = field(default_factory=dict)


def evaluate(st: GuardState, *, equity: float, balance: float, today: str, wanted: str,
             daily_loss_pct: float, max_dd_pct: float) -> GuardState:
    """Pure decision step: update the peak / day and decide the state.

    `wanted` is what the admin / client asked for (ACTIVE, PAUSED, STOPPED).
    A drawdown stop holds until `wanted` changes away from ACTIVE and back
    (the admin resuming), which live signals by sending resume=True; here it
    holds until the control file says so - see Guard.tick."""
    if st.day != today:
        st.day, st.day_start = today, balance
        if st.state == "DAY_STOP":
            st.state, st.reason = "ACTIVE", ""
    st.peak = max(st.peak, equity)
    if wanted in ("PAUSED", "STOPPED"):
        if st.state not in ("DD_STOP",):
            st.state = wanted
            st.reason = "paused" if wanted == "PAUSED" else "stopped"
        return st
    if st.state in ("PAUSED", "STOPPED"):
        st.state, st.reason = "ACTIVE", ""
    if st.state == "DD_STOP":
        return st
    dd = (st.peak - equity) / st.peak * 100 if st.peak > 0 else 0.0
    if max_dd_pct > 0 and dd >= max_dd_pct:
        st.state, st.reason = "DD_STOP", f"drawdown stop: equity {dd:.1f}% below its peak (limit {max_dd_pct:g}%)"
        return st
    day_loss = (st.day_start - equity) / st.day_start * 100 if st.day_start > 0 else 0.0
    if st.state != "DAY_STOP" and daily_loss_pct > 0 and day_loss >= daily_loss_pct:
        st.state, st.reason = "DAY_STOP", f"daily loss stop: down {day_loss:.1f}% today (limit {daily_loss_pct:g}%) - resumes tomorrow"
    return st


class Guard:
    def __init__(self, inst: str, client_id: str):
        self.inst, self.client_id = inst, client_id
        self.state_file = Path(os.getenv("VVIP_STATE_FILE", "data/vvip_guard.json"))
        self.st = self._load()
        self.task: asyncio.Task | None = None
        self.resume_seen = ""

    def _load(self) -> GuardState:
        try:
            return GuardState(**json.loads(self.state_file.read_text()))
        except Exception:  # noqa: BLE001
            return GuardState()

    def _save(self) -> None:
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps(self.st.__dict__))
        except OSError:
            pass

    def control(self) -> dict:
        try:
            return json.loads((SHARED / f"{self.inst}.control.json").read_text())
        except Exception:  # noqa: BLE001
            return {}

    def write_status(self, info: dict) -> None:
        try:
            SHARED.mkdir(parents=True, exist_ok=True)
            tmp = SHARED / f".{self.inst}.status.tmp"
            tmp.write_text(json.dumps(info))
            os.replace(tmp, SHARED / f"{self.inst}.status.json")
        except OSError as e:
            logger.warning(f"[VVIP] status write failed: {e}")

    async def start(self) -> None:
        self.task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        await asyncio.sleep(20)
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[VVIP] guard tick: {e}")
            await asyncio.sleep(30)

    async def tick(self) -> None:
        import MetaTrader5 as mt5
        from backend.services.bot_service import bot_service
        ctl = self.control()
        acc = await asyncio.to_thread(mt5.account_info)
        if acc is None:
            self.write_status({"ok": False, "error": "MT5 not connected", "updated": time.time()})
            return
        if ctl.get("resume_token") and ctl["resume_token"] != self.resume_seen:
            self.resume_seen = ctl["resume_token"]
            if self.st.state == "DD_STOP":
                self.st.state, self.st.reason, self.st.peak = "ACTIVE", "", float(acc.equity)
        await self.apply_risk(ctl)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        prev = self.st.state
        evaluate(self.st, equity=float(acc.equity), balance=float(acc.balance), today=today,
                 wanted=str(ctl.get("state") or "ACTIVE").upper(),
                 daily_loss_pct=float(ctl.get("daily_loss_pct") or os.getenv("VVIP_DAILY_LOSS_PCT") or 5),
                 max_dd_pct=float(ctl.get("max_dd_pct") or os.getenv("VVIP_MAX_DD_PCT") or 20))
        if self.st.state == "ACTIVE" and not bot_service.running:
            await self.start_bot()
        elif self.st.state != "ACTIVE" and bot_service.running:
            await bot_service.stop()
            logger.warning(f"[VVIP] trading paused: {self.st.reason}")
        self._save()
        positions = await asyncio.to_thread(mt5.positions_get) or ()
        self.write_status({
            "ok": True, "updated": time.time(), "client_id": self.client_id, "login": int(acc.login),
            "balance": float(acc.balance), "equity": float(acc.equity), "currency": acc.currency,
            "peak": self.st.peak, "day_start": self.st.day_start,
            "drawdown_pct": round((self.st.peak - float(acc.equity)) / self.st.peak * 100, 2) if self.st.peak else 0.0,
            "day_pnl": round(float(acc.equity) - self.st.day_start, 2) if self.st.day_start else 0.0,
            "open_positions": len(positions), "bot_running": bool(bot_service.running),
            "state": self.st.state, "reason": self.st.reason, "changed": prev != self.st.state})

    async def apply_risk(self, ctl: dict) -> None:
        """Put the risk % from the control file into every slot of this instance's config."""
        risk = ctl.get("risk_pct")
        if risk is None or self.st.extra.get("risk_pct") == risk:
            return
        risk = min(2.0, max(0.1, float(risk)))
        from sqlalchemy import select
        from backend.data.database import async_session
        from backend.data.models import UserConfigModel
        async with async_session() as s:
            for row in (await s.execute(select(UserConfigModel))).scalars().all():
                cfg = json.loads(row.config_json or "{}")
                set_risk(cfg, risk, ctl.get("daily_loss_pct"))
                row.config_json = json.dumps(cfg)
            await s.commit()
        self.st.extra["risk_pct"] = risk
        logger.info(f"[VVIP] risk per trade set to {risk}%")

    async def start_bot(self) -> None:
        from sqlalchemy import select
        from backend.core.config_schema import UserConfigV2
        from backend.data.database import async_session
        from backend.data.models import User, UserConfigModel
        from backend.services.bot_service import bot_service
        async with async_session() as s:
            user = (await s.execute(select(User).where(User.is_admin.is_(True)))).scalars().first()
            if user is None:
                return
            row = await s.get(UserConfigModel, user.id)
        if row is None:
            return
        symbols = bot_service.active_slot_symbols(UserConfigV2.from_dict(json.loads(row.config_json)))
        if symbols:
            await bot_service.start(user_id=user.id, symbols=symbols)
            logger.info(f"[VVIP] bot started for client {self.client_id}: {', '.join(symbols)}")


def set_risk(cfg: dict, risk_pct: float, daily_loss_pct: float | None = None) -> dict:
    """The client's risk on the account default and on every slot (a slot's own value wins otherwise)."""
    r = cfg.setdefault("risk", {})
    r["risk_per_trade_pct"] = risk_pct
    r["max_risk_hard_cap_pct"] = min(2.0, max(risk_pct, float(r.get("max_risk_hard_cap_pct") or 2.0)))
    if daily_loss_pct:
        r["max_daily_drawdown_pct"] = float(daily_loss_pct)
    for slot in cfg.get("instrument_slots") or []:
        slot["risk_per_trade_pct"] = risk_pct
        if isinstance(slot.get("risk"), dict):
            slot["risk"]["risk_per_trade_pct"] = risk_pct
            slot["risk"].pop("max_risk_hard_cap_pct", None)
    return cfg


guard: Guard | None = None


async def start_if_client() -> None:
    global guard
    cid = os.getenv("VVIP_CLIENT", "").strip()
    if not cid:
        return
    from backend.services.signal_sync import _instance_name
    guard = Guard(_instance_name() or f"client{cid}", cid)
    await guard.start()
    logger.info(f"[VVIP] client guard running for client {cid}")
