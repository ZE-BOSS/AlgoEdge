"""
backend/api/routes/auth.py

User registration, login, and JWT token management.
Email + password authentication with bcrypt hashing.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from jose import jwt
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import JWT_ALGORITHM, JWT_SECRET_KEY, get_current_user
from backend.data.database import get_db
from backend.data.models import User
from backend.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])

# Password hashing. Direct bcrypt, not passlib: passlib 1.7.4 raises on EVERY
# hash and verify against bcrypt 5.x (see backend/core/passwords.py). The hash
# format is unchanged, so existing stored hashes still verify.
from backend.core.passwords import hash_password as _hash_password
from backend.core.passwords import verify_password as _verify_password

# Token expiry
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("JWT_ACCESS_EXPIRE_MINUTES", "15"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("JWT_REFRESH_EXPIRE_DAYS", "7"))


# ── Request/Response Models ──────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=255)
    password: str = Field(..., min_length=6, max_length=128)
    name: str = Field(..., min_length=1, max_length=100)


class LoginRequest(BaseModel):
    email: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: dict


class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    is_active: bool
    is_admin: bool = False


# ── Token Generation ─────────────────────────────────────────────────────────

def create_token(user_id: str, token_type: str = "access") -> str:
    """Create a JWT access or refresh token."""
    if token_type == "access":
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    else:
        expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)

    payload = {
        "sub": user_id,
        "type": token_type,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """Create a new user account and return JWT tokens.

    Open only until the first account exists, or while ALLOW_REGISTRATION=1.
    Every operator account can drive the bot and the broker, and the admin site
    is reachable from any network, so a stranger must not be able to sign up.
    """
    anyone = (await db.execute(select(User.id).limit(1))).scalar_one_or_none()
    if os.getenv("ALLOW_REGISTRATION", "").strip() != "1":
        if anyone is not None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Registration is closed. Ask an administrator for an account.",
            )

    # Check if email already exists
    existing = await db.execute(select(User).where(User.email == req.email.lower()))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    # Create user
    user_id = str(uuid.uuid4())
    user = User(
        id=user_id,
        email=req.email.lower().strip(),
        password_hash=_hash_password(req.password),
        name=req.name.strip(),
        is_active=True,
        is_admin=anyone is None,   # the very first account is the owner
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    logger.info(f"User registered: {user.email} ({user.id})")
    try:
        from backend.services.bot_service import bot_service
        bot_service.log_system_event(f"New user registered: {user.email}", category="SYSTEM")
    except Exception:
        pass

    # Generate tokens
    access_token = create_token(user_id, "access")
    refresh_token = create_token(user_id, "refresh")

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user={
            "id": user.id,
            "email": user.email,
            "name": user.name,
        },
    )


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Authenticate user and return JWT tokens."""

    result = await db.execute(select(User).where(User.email == req.email.lower().strip()))
    user = result.scalar_one_or_none()

    if not user or not _verify_password(req.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated",
        )

    logger.info(f"User logged in: {user.email}")
    try:
        from backend.services.bot_service import bot_service
        bot_service.log_system_event(f"User logged in: {user.email}", category="SYSTEM")
    except Exception:
        pass

    access_token = create_token(user.id, "access")
    refresh_token = create_token(user.id, "refresh")

    if user.is_admin:
        await _note_admin_device(db, user, request)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user={
            "id": user.id,
            "email": user.email,
            "name": user.name,
            # lets the console show admin-only sections; the server still
            # enforces require_admin on every such route
            "is_admin": bool(user.is_admin),
        },
    )


async def _note_admin_device(db: AsyncSession, user: User, request: Request) -> None:
    """Email the admin when their account signs in from a browser not seen before.

    Keyed on the browser (user agent), not the IP: an IP changes every time a
    phone moves between networks, and an alert that fires daily gets ignored.
    The very first sign-in only records the device — there is nothing to
    compare it with. Never blocks the login: a failure here is logged.
    """
    import hashlib
    from datetime import datetime, timezone

    try:
        from backend.investor.models import AdminDevice
        from backend.notify import outbox
        from backend.notify import templates as mail

        ua = (request.headers.get("user-agent") or "")[:300]
        ip = request.client.host if request.client else None
        fp = hashlib.sha256(ua.encode()).hexdigest()
        known = (await db.execute(select(AdminDevice).where(AdminDevice.user_id == user.id))).scalars().all()
        now = datetime.now(timezone.utc)
        match = next((d for d in known if d.fingerprint == fp), None)
        if match:
            match.last_seen, match.ip = now, ip
            return
        db.add(AdminDevice(user_id=user.id, fingerprint=fp, ip=ip, user_agent=ua))
        if known:
            outbox.queue(db, mail.admin_new_device(user, ip, ua, now))
    except Exception as exc:  # never let the alert stop a login
        logger.error(f"[AUTH] new-device check failed: {exc}")


@router.post("/refresh")
async def refresh_token(req: RefreshRequest, db: AsyncSession = Depends(get_db)):
    """Exchange a valid refresh token for a new access token."""
    from jose import JWTError

    try:
        payload = jwt.decode(req.refresh_token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        user_id = payload.get("sub")
        token_type = payload.get("type")

        if user_id is None or token_type != "refresh":
            raise HTTPException(status_code=401, detail="Invalid refresh token")

    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    # Verify user still exists and is active
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or deactivated")

    new_access = create_token(user.id, "access")
    new_refresh = create_token(user.id, "refresh")

    return {
        "access_token": new_access,
        "refresh_token": new_refresh,
        "token_type": "bearer",
    }


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    """Get the currently authenticated user's profile."""
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        name=current_user.name,
        is_active=current_user.is_active,
        is_admin=bool(current_user.is_admin),
    )
