"""
Security utilities: password hashing, JWT tokens, credential encryption.
Uses bcrypt directly to avoid passlib/bcrypt version conflicts.
"""
from __future__ import annotations

import base64
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import bcrypt
from cryptography.fernet import Fernet, InvalidToken
from jose import JWTError, jwt

from backend.core.config import settings


# ---------- Password hashing (bcrypt) ----------

def hash_password(password: str) -> str:
    pwd_bytes = password.encode("utf-8")[:72]  # bcrypt max 72 bytes
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        pwd_bytes = plain.encode("utf-8")[:72]
        return bcrypt.checkpw(pwd_bytes, hashed.encode("utf-8"))
    except Exception:
        return False


# ---------- JWT ----------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(subject: str | int, extra: Optional[Dict[str, Any]] = None,
                        expires_minutes: Optional[int] = None) -> str:
    expire = _utcnow() + timedelta(
        minutes=expires_minutes or settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload: Dict[str, Any] = {"sub": str(subject), "exp": expire, "type": "access", "iat": _utcnow()}
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(subject: str | int) -> str:
    expire = _utcnow() + timedelta(minutes=settings.JWT_REFRESH_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": str(subject), "exp": expire, "type": "refresh", "iat": _utcnow()}
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> Dict[str, Any]:
    try:
        return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except JWTError as e:
        raise ValueError(f"Invalid token: {e}") from e


# ---------- Symmetric encryption for broker credentials ----------

def _get_fernet() -> Fernet:
    key = settings.CREDENTIAL_ENCRYPTION_KEY
    if not key:
        # Generate an ephemeral key for the process lifetime.
        key = os.environ.setdefault("AIFX_EPHEMERAL_FERNET_KEY", Fernet.generate_key().decode())
        from backend.core.config import get_settings as _gs
        _gs.cache_clear()
        import warnings
        warnings.warn(
            "CREDENTIAL_ENCRYPTION_KEY not set; using ephemeral key. "
            "Broker credentials will not survive a restart. Set the key in .env."
        )
    if isinstance(key, str):
        # Accept base64 keys and raw keys (pad/derive to 32 bytes)
        k = key.encode("utf-8")
        try:
            return Fernet(k)
        except Exception:
            k32 = k[:32].ljust(32, b"=")
            return Fernet(base64.urlsafe_b64encode(k32))
    return Fernet(key)


def encrypt_credential(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_credential(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    try:
        return _get_fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        raise ValueError("Credential decryption failed — wrong encryption key or corrupted data.")


def generate_fernet_key() -> str:
    return Fernet.generate_key().decode("utf-8")
