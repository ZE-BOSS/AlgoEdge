"""
backend/api/routes/system.py

Operational endpoints the frontend needs to *see* and *fix* bot state:

  GET  /api/telegram/status     is Telegram configured, and what failed last?
  POST /api/telegram/test       send a real message and return the real error
  GET  /api/account/live        the connected MT5 account: balance, equity,
                                and what the configured risk % resolves to
  GET  /api/account/state       what persisted state exists, and for which
                                account
  POST /api/account/reset       forget an old account: clear circuit-breaker
                                state, sync state, and (optionally) the journal
                                and signal history

WHY /api/account/reset EXISTS
-----------------------------
Switching the terminal to a different MT5 login used to leave three kinds of
stale state behind — `backend/data/cb_state.json` (daily/weekly realised P&L and
drawdown pause), `backend/data/bot_sync_state.json` (how far back to read deal
history) and the `trades` / `signals` tables. The first two are now scoped to an
account number and self-invalidate, but the journal rows written under the old
account are still there, and only the user can say whether they want them kept
for the record or cleared.
"""

import json
import os

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user
from backend.data.database import get_db
from backend.data.models import Signal, Trade, TradePosition, User, UserConfigModel
from backend.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["system"])

CB_STATE_FILE = "backend/data/cb_state.json"
SYNC_STATE_FILE = "backend/data/bot_sync_state.json"


# ── Telegram ─────────────────────────────────────────────────────────────────

class TelegramTestRequest(BaseModel):
    message: str | None = None


@router.get("/telegram/status")
async def telegram_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Whether alerts can actually be delivered, and the last failure if not.

    Also reports whether the credentials saved in the database match the ones
    currently loaded in the running process — the exact mismatch that made
    "the token is right there on the frontend" coexist with "no messages
    arrive".
    """
    from backend.services.telegram import telegram_service

    saved_token = saved_chat = ""
    try:
        row = (
            await db.execute(
                select(UserConfigModel).where(UserConfigModel.user_id == current_user.id)
            )
        ).scalar_one_or_none()
        if row and row.config_json:
            cfg = json.loads(row.config_json)
            saved_token = cfg.get("telegram_bot_token", "") or ""
            saved_chat = cfg.get("telegram_chat_id", "") or ""
    except Exception as e:
        logger.warning(f"[Telegram] status: could not read saved config: {e}")

    status = telegram_service.status()
    status["saved_in_db"] = bool(saved_token and saved_chat)
    status["loaded_matches_saved"] = (
        saved_token.strip() == telegram_service.bot_token
        and saved_chat.strip() == telegram_service.chat_id
    )
    if status["saved_in_db"] and not status["loaded_matches_saved"]:
        # Self-heal rather than just reporting it.
        telegram_service.update_config(saved_token, saved_chat)
        status = telegram_service.status()
        status["saved_in_db"] = True
        status["loaded_matches_saved"] = True
        status["note"] = "Credentials were reloaded from the database."
    return status


@router.post("/telegram/test")
async def telegram_test(
    req: TelegramTestRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Send a real Telegram message and return Telegram's real answer.

    `send_message` swallowed every failure into the server log, so from the UI
    a bad token, a chat the bot has never been started in, and a TLS failure all
    looked identical: nothing happens. This returns the HTTP status and
    Telegram's own error description.
    """
    from backend.services.telegram import telegram_service

    # Make sure we are testing what the user actually saved.
    try:
        row = (
            await db.execute(
                select(UserConfigModel).where(UserConfigModel.user_id == current_user.id)
            )
        ).scalar_one_or_none()
        if row and row.config_json:
            cfg = json.loads(row.config_json)
            telegram_service.update_config(
                cfg.get("telegram_bot_token", ""), cfg.get("telegram_chat_id", "")
            )
    except Exception as e:
        logger.warning(f"[Telegram] test: could not reload config: {e}")

    text = (req.message if req and req.message else
            "✅ *AlgoEdge test message*\nIf you can read this, alerts are working.")
    result = await telegram_service.send_message(text)
    result["status"] = telegram_service.status()
    return result


# ── Live account ─────────────────────────────────────────────────────────────

@router.get("/account/live")
async def account_live(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The connected MT5 account, and what the risk settings resolve to on it.

    `get_broker_status` returned only the masked login and server name, so
    nothing in the UI ever showed the account's actual balance — which meant
    there was no way to tell whether "1.8% risk" was 1.8% of the account now
    connected or of a previous, larger one.
    """
    from backend.core.config_schema import UserConfigV2

    out: dict = {
        "connected": False,
        "login": None,
        "server": None,
        "currency": "",
        "balance": None,
        "equity": None,
        "margin_free": None,
        "configured_account": current_user.mt5_account,
        "account_matches_config": None,
    }

    try:
        import MetaTrader5 as mt5

        from backend.mt5.executor import run_mt5
        info = await run_mt5(mt5.account_info)
        if info is not None:
            out.update(
                connected=True,
                login=int(info.login),
                server=getattr(info, "server", "") or "",
                currency=getattr(info, "currency", "") or "",
                balance=float(info.balance),
                equity=float(info.equity),
                margin_free=float(getattr(info, "margin_free", 0.0) or 0.0),
            )
            if current_user.mt5_account:
                out["account_matches_config"] = int(info.login) == int(current_user.mt5_account)
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"

    # Resolve the risk settings against that balance.
    try:
        row = (
            await db.execute(
                select(UserConfigModel).where(UserConfigModel.user_id == current_user.id)
            )
        ).scalar_one_or_none()
        cfg = UserConfigV2.from_dict(json.loads(row.config_json)) if (row and row.config_json) else UserConfigV2()
        risk_pct = float(getattr(cfg.risk, "risk_per_trade_pct", 1.0) or 1.0)
        sizing_basis = getattr(cfg.risk, "sizing_basis", "STATIC") or "STATIC"

        from backend.services.bot_service import bot_service
        static_anchor = getattr(bot_service, "_static_personal_balance_anchor", None)

        if sizing_basis == "EQUITY":
            base = out.get("equity")
            base_label = "live equity"
        elif sizing_basis == "BALANCE":
            base = out.get("balance")
            base_label = "live balance"
        else:
            base = static_anchor if static_anchor else out.get("balance")
            base_label = (
                f"static anchor (first balance seen this process: {static_anchor:.2f})"
                if static_anchor else "live balance (no static anchor set yet)"
            )

        out["risk"] = {
            "risk_per_trade_pct": risk_pct,
            "sizing_basis": sizing_basis,
            "sizing_base_balance": base,
            "sizing_base_label": base_label,
            "risk_per_trade_amount": (base * risk_pct / 100.0) if base else None,
            "max_daily_drawdown_pct": getattr(cfg.risk, "max_daily_drawdown_pct", None),
            "max_daily_drawdown_amount": (
                base * float(getattr(cfg.risk, "max_daily_drawdown_pct", 0) or 0) / 100.0
                if base else None
            ),
        }
    except Exception as e:
        out["risk_error"] = f"{type(e).__name__}: {e}"

    # Live circuit-breaker view, so the UI can say WHY trading is blocked.
    try:
        from backend.services.bot_service import bot_service
        cb = bot_service.circuit_breaker
        if cb:
            out["circuit_breaker"] = {
                "account_id": cb.account_id,
                "is_paused": cb.is_paused,
                "pause_reason": cb.pause_reason,
                "daily_pnl": cb.daily_pnl,
                "weekly_pnl": cb.weekly_pnl,
                "day_start_balance": cb._day_start_balance,
                "daily_trades_count": cb.daily_trades_count,
                "max_daily_drawdown_pct": cb.max_daily_drawdown_pct,
            }
    except Exception:
        pass

    return out


# ── Persisted state / reset ──────────────────────────────────────────────────

def _read_json(path: str) -> dict | None:
    try:
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"Could not read {path}: {e}")
    return None


@router.get("/account/state")
async def account_state(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """What persisted state exists, which account it belongs to, and what is in the journal."""
    cb = _read_json(CB_STATE_FILE) or {}
    sync = _read_json(SYNC_STATE_FILE) or {}

    live_login = None
    try:
        import MetaTrader5 as mt5

        from backend.mt5.executor import run_mt5
        info = await run_mt5(mt5.account_info)
        live_login = int(info.login) if info else None
    except Exception:
        pass

    trades_total = len((await db.execute(
        select(Trade.id).where(Trade.user_id == current_user.id)
    )).all())
    trades_adopted = len((await db.execute(
        select(Trade.id).where(
            Trade.user_id == current_user.id,
            Trade.strategy_id.in_(["MANUAL", "MANUAL_OFFLINE"]),
        )
    )).all())
    signals_total = len((await db.execute(
        select(Signal.id).where(Signal.user_id == current_user.id)
    )).all())

    return {
        "live_login": live_login,
        "circuit_breaker_state": {
            "exists": bool(cb),
            "mt5_account": cb.get("mt5_account"),
            "stale": bool(cb) and live_login is not None and cb.get("mt5_account") != live_login,
            "daily_pnl": cb.get("daily_pnl"),
            "weekly_pnl": cb.get("weekly_pnl"),
            "is_paused": cb.get("is_paused"),
            "pause_reason": cb.get("pause_reason"),
        },
        "sync_state": {
            "exists": bool(sync),
            "mt5_account": sync.get("mt5_account"),
            "stale": bool(sync) and live_login is not None and sync.get("mt5_account") != live_login,
            "last_check_time": sync.get("last_check_time"),
        },
        "journal": {
            "trades_total": trades_total,
            "trades_adopted_from_broker": trades_adopted,
            "signals_total": signals_total,
        },
    }


class ResetRequest(BaseModel):
    clear_risk_state: bool = True
    """cb_state.json + bot_sync_state.json + the in-memory circuit breaker."""
    clear_adopted_trades: bool = True
    """Journal rows the bot adopted from the broker (strategy_id MANUAL / MANUAL_OFFLINE)."""
    clear_all_trades: bool = False
    """Every journal row for this user, including the bot's own."""
    clear_signals: bool = False
    """Every signal row for this user."""


@router.post("/account/reset")
async def account_reset(
    req: ResetRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Forget the previous account's state.

    Nothing here is implicit: each category is opt-in, and the response reports
    exactly what was removed.
    """
    from backend.services.bot_service import bot_service

    removed: dict = {}

    live_login = None
    try:
        import MetaTrader5 as mt5

        from backend.mt5.executor import run_mt5
        info = await run_mt5(mt5.account_info)
        live_login = int(info.login) if info else None
    except Exception:
        pass

    if req.clear_risk_state:
        for path in (CB_STATE_FILE, SYNC_STATE_FILE):
            try:
                if os.path.exists(path):
                    os.remove(path)
                    removed[os.path.basename(path)] = "deleted"
            except Exception as e:
                removed[os.path.basename(path)] = f"failed: {e}"
        try:
            if bot_service.circuit_breaker:
                bot_service.circuit_breaker.reset_for_new_account(live_login)
                removed["circuit_breaker"] = f"reset for account {live_login}"
        except Exception as e:
            removed["circuit_breaker"] = f"failed: {e}"
        try:
            from backend.services.profit_tracker import profit_tracker
            removed["profit_tracker"] = await profit_tracker.reset()
        except Exception as e:
            removed["profit_tracker"] = f"failed: {e}"
        # Drop the cached ticket set so ownership is recomputed from the DB.
        bot_service._bot_tickets_cache = set()
        bot_service._bot_tickets_cache_at = 0.0
        # Re-anchor STATIC sizing to the NEW account's balance rather than
        # keeping the previous account's first-seen balance for the life of the
        # process — that anchor is what would otherwise size positions for a
        # $10,000 account while trading a $700 one.
        bot_service._static_personal_balance_anchor = None
        removed["static_balance_anchor"] = "cleared (re-anchors to the new account)"

    if req.clear_all_trades:
        ids = [r[0] for r in (await db.execute(
            select(Trade.id).where(Trade.user_id == current_user.id)
        )).all()]
        if ids:
            await db.execute(delete(TradePosition).where(TradePosition.parent_trade_id.in_(ids)))
            await db.execute(delete(Trade).where(Trade.id.in_(ids)))
        removed["trades"] = len(ids)
    elif req.clear_adopted_trades:
        ids = [r[0] for r in (await db.execute(
            select(Trade.id).where(
                Trade.user_id == current_user.id,
                Trade.strategy_id.in_(["MANUAL", "MANUAL_OFFLINE"]),
            )
        )).all()]
        if ids:
            await db.execute(delete(TradePosition).where(TradePosition.parent_trade_id.in_(ids)))
            await db.execute(delete(Trade).where(Trade.id.in_(ids)))
        removed["adopted_trades"] = len(ids)

    if req.clear_signals:
        ids = [r[0] for r in (await db.execute(
            select(Signal.id).where(Signal.user_id == current_user.id)
        )).all()]
        if ids:
            await db.execute(delete(Signal).where(Signal.id.in_(ids)))
        removed["signals"] = len(ids)

    await db.commit()

    bot_service.log_system_event(
        f"Account state reset for MT5 login {live_login}: {removed}",
        "WARNING", "SYSTEM",
    )
    logger.warning(f"[RESET] {current_user.email}: {removed}")
    return {"ok": True, "live_login": live_login, "removed": removed}
