"""
backend/services/api_key_store.py

Encrypted API key storage using Fernet symmetric encryption.
Source: TradingBot_MasterPlan-2.md Section 9.1
"""

from typing import Optional
from backend.config import settings
from backend.data.database import get_session
from backend.data.models import APIKey
from backend.utils.logger import get_logger
from sqlalchemy import select

logger = get_logger(__name__)

try:
    from cryptography.fernet import Fernet
    HAS_FERNET = True
except ImportError:
    HAS_FERNET = False


def _get_cipher():
    """Get Fernet cipher from configured encryption key."""
    key = settings.security.encryption_key
    if not key or not HAS_FERNET:
        return None
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception:
        logger.error("Invalid ENCRYPTION_KEY — cannot encrypt API keys")
        return None


async def store_api_key(user_id: str, provider: str, api_key: str) -> bool:
    """Encrypt and store an API key in PostgreSQL."""
    cipher = _get_cipher()
    if not cipher:
        logger.error("Encryption not available — cannot store API key")
        return False

    encrypted = cipher.encrypt(api_key.encode())

    async with get_session() as session:
        result = await session.execute(
            select(APIKey).where(APIKey.user_id == user_id, APIKey.provider == provider)
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.encrypted_key = encrypted
            existing.verified = False
        else:
            session.add(APIKey(
                user_id=user_id,
                provider=provider,
                encrypted_key=encrypted,
                verified=False,
            ))

    logger.info(f"API key stored for user {user_id}, provider {provider}")
    return True


async def get_api_key(user_id: str, provider: str) -> Optional[str]:
    """Retrieve and decrypt an API key."""
    cipher = _get_cipher()
    if not cipher:
        return None

    async with get_session() as session:
        result = await session.execute(
            select(APIKey).where(APIKey.user_id == user_id, APIKey.provider == provider)
        )
        key_record = result.scalar_one_or_none()

    if not key_record:
        return None

    try:
        return cipher.decrypt(key_record.encrypted_key).decode()
    except Exception:
        logger.error(f"Failed to decrypt API key for {user_id}/{provider}")
        return None


async def delete_api_key(user_id: str, provider: str) -> bool:
    """Remove a stored API key."""
    async with get_session() as session:
        result = await session.execute(
            select(APIKey).where(APIKey.user_id == user_id, APIKey.provider == provider)
        )
        key_record = result.scalar_one_or_none()
        if key_record:
            await session.delete(key_record)
            return True
    return False
