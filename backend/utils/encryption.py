"""
backend/utils/encryption.py

Fernet (AES-128-CBC + HMAC-SHA256) encryption for API keys and credentials.
Source: Frontend_PWA_LLM_Spec.md Section 4.6
"""

from cryptography.fernet import Fernet, InvalidToken
from backend.config import settings


class EncryptionService:
    """Encrypts/decrypts sensitive data using Fernet symmetric encryption."""

    def __init__(self, key: str = ""):
        key = key or settings.security.encryption_key
        if not key:
            raise ValueError(
                "ENCRYPTION_KEY not set. Generate one with: "
                "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
        self._cipher = Fernet(key.encode() if isinstance(key, str) else key)

    def encrypt(self, plaintext: str) -> bytes:
        """Encrypt a string, returns encrypted bytes."""
        return self._cipher.encrypt(plaintext.encode())

    def decrypt(self, encrypted: bytes) -> str:
        """Decrypt bytes back to string."""
        try:
            return self._cipher.decrypt(encrypted).decode()
        except InvalidToken:
            raise ValueError("Decryption failed — invalid key or corrupted data")


# Singleton instance (lazy — only created when needed)
_encryption_service = None


def get_encryption_service() -> EncryptionService:
    global _encryption_service
    if _encryption_service is None:
        _encryption_service = EncryptionService()
    return _encryption_service
