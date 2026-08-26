"""
backend/api/routes/broker.py

Self-service broker configuration endpoints.
Users can save, test, and manage their MT5 broker credentials
for both standard brokers (Exness, IC Markets) and Deriv (synthetics).

Passwords are encrypted with Fernet (AES-128-CBC + HMAC-SHA256) before storage.
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user
from backend.data.database import get_db
from backend.data.models import User
from backend.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/broker", tags=["broker"])


# ── Request Models ───────────────────────────────────────────────────────────

class BrokerConfigRequest(BaseModel):
    account: int
    password: str               # Plaintext over HTTPS — encrypted before DB storage
    server: str
    path: str | None = ""    # MT5 terminal path (optional)


class TestBrokerRequest(BaseModel):
    account: int
    password: str
    server: str
    path: str | None = ""


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_encryption():
    """Get the encryption service, with a clear error if not configured."""
    try:
        from backend.utils.encryption import get_encryption_service
        return get_encryption_service()
    except ValueError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Encryption not configured: {e!s}. Set ENCRYPTION_KEY in .env"
        )


async def _test_mt5_connection(account: int, password: str, server: str, path: str = "") -> dict:
    """
    Attempt to connect to MT5 with the given credentials.
    Returns connection status and account info.
    """
    try:
        import MetaTrader5 as mt5
    except ImportError:
        return {
            "connected": False,
            "mock_mode": True,
            "message": "MetaTrader5 package not installed — mock mode only",
        }

    loop = asyncio.get_event_loop()

    # Initialize MT5
    if path:
        init_ok = await loop.run_in_executor(None, lambda: mt5.initialize(path=path))
    else:
        init_ok = await loop.run_in_executor(None, mt5.initialize)

    if not init_ok:
        error = mt5.last_error()
        return {
            "connected": False,
            "message": f"MT5 initialization failed: {error}",
        }

    # Login
    login_ok = await loop.run_in_executor(
        None,
        lambda: mt5.login(account, password=password, server=server)
    )

    if not login_ok:
        error = mt5.last_error()
        await loop.run_in_executor(None, mt5.shutdown)
        return {
            "connected": False,
            "message": f"Login failed: {error}",
        }

    # Get account info
    info = mt5.account_info()
    result = {
        "connected": True,
        "message": "Connected successfully",
        "account_info": {
            "login": info.login,
            "server": info.server,
            "balance": info.balance,
            "equity": info.equity,
            "leverage": info.leverage,
            "currency": info.currency,
            "company": info.company,
            "name": info.name,
        } if info else {},
    }

    await loop.run_in_executor(None, mt5.shutdown)
    return result


def _mask_account(account: int) -> str:
    """Mask account number: show first 2 and last 2 digits."""
    s = str(account)
    if len(s) <= 4:
        return s
    return s[:2] + "*" * (len(s) - 4) + s[-2:]


# ── Standard Broker Endpoints ────────────────────────────────────────────────

@router.post("/standard")
async def save_standard_broker(
    req: BrokerConfigRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Save standard broker (Exness, IC Markets, etc.) credentials.
    Password is encrypted with Fernet before storage.
    """
    encryption = _get_encryption()

    current_user.mt5_account = req.account
    current_user.mt5_password_encrypted = encryption.encrypt(req.password)
    current_user.mt5_server = req.server
    current_user.mt5_path = req.path or ""

    await db.commit()

    # Attempt test connection
    test_result = await _test_mt5_connection(req.account, req.password, req.server, req.path or "")

    try:
        from backend.services.bot_service import bot_service
        bot_service.log_system_event(
            f"Standard broker configured: {req.server} (Account: {_mask_account(req.account)})",
            category="CONFIG"
        )
    except Exception:
        pass

    logger.info(f"Standard broker saved for {current_user.email}: server={req.server}")
    return {
        "saved": True,
        "broker_type": "standard",
        "server": req.server,
        "account_masked": _mask_account(req.account),
        "connection_test": test_result,
    }



# ── Status & Test ────────────────────────────────────────────────────────────

@router.get("/status")
async def get_broker_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the current broker configuration status (no passwords returned)."""
    return {
        "standard": {
            "configured": current_user.mt5_account is not None,
            "account_masked": _mask_account(current_user.mt5_account) if current_user.mt5_account else None,
            "server": current_user.mt5_server,
            "path": current_user.mt5_path,
        }
    }


# ── Symbol identity (Phase 14 Part C / task 14.9) ────────────────────────────

@router.get("/instruments")
async def get_instrument_resolution(
    refresh: bool = False,
    current_user: User = Depends(get_current_user),
):
    """
    How each canonical instrument resolves to a symbol on the connected broker.

    This is what makes a config portable: the config names `GER40`, and the
    active broker's map turns that into `GER30` on Deriv or `GER40` on
    FundedNext. Unavailable instruments are returned explicitly with the reason,
    so pickers can disable them ("not listed by Deriv-Demo") instead of letting
    the user pick one that fails later at data fetch.
    """
    import MetaTrader5 as mt5

    from backend.core.instruments import (
        broker_id_from_account,
        discover_broker_symbols,
        get_broker_map,
    )

    loop = asyncio.get_running_loop()
    if not await loop.run_in_executor(None, mt5.terminal_info):
        return {
            "connected": False,
            "broker_id": None,
            "message": "MT5 is not connected — cannot enumerate symbols",
            "instruments": [],
        }

    info = await loop.run_in_executor(None, mt5.account_info)
    broker_id = broker_id_from_account(
        getattr(info, "company", None), getattr(info, "server", None)
    )

    mapping = None if refresh else get_broker_map(broker_id)
    if mapping is None:
        symbols = await loop.run_in_executor(None, mt5.symbols_get)
        mapping = discover_broker_symbols(broker_id, symbols)

    rows = [
        {
            "canonical": r.canonical,
            "broker_symbol": r.broker_symbol,
            "available": r.available,
            "reason": r.reason,
            "ambiguous_with": r.ambiguous_with,
        }
        for r in sorted(mapping.values(), key=lambda x: (not x.available, x.canonical))
    ]
    return {
        "connected": True,
        "broker_id": broker_id,
        "broker": getattr(info, "company", None),
        "server": getattr(info, "server", None),
        "available_count": sum(1 for r in rows if r["available"]),
        "total_count": len(rows),
        "instruments": rows,
    }


@router.post("/test")
async def test_broker_connection(
    req: TestBrokerRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Test broker connectivity WITHOUT saving credentials.
    Useful for verifying credentials before committing.
    """
    logger.info(f"Testing broker connection: server={req.server} account={_mask_account(req.account)}")
    result = await _test_mt5_connection(req.account, req.password, req.server, req.path or "")
    return result


# ── Delete ───────────────────────────────────────────────────────────────────

@router.delete("/standard")
async def remove_standard_broker(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove stored standard broker credentials."""
    current_user.mt5_account = None
    current_user.mt5_password_encrypted = None
    current_user.mt5_server = None
    current_user.mt5_path = None
    await db.commit()

    logger.info(f"Standard broker removed for {current_user.email}")
    return {"removed": True, "broker_type": "standard"}


