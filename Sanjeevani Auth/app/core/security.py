"""
app/core/security.py
──────────────────────────────────────────────────
Security utilities:
  - Password hashing & verification (bcrypt via passlib)
  - JWT token creation & decoding (PyJWT)
  - get_current_user FastAPI dependency
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext

from app.core.config import settings

# ── Password hashing ───────────────────────────────────────────────────────
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain_password: str) -> str:
    """Hash a plaintext password using bcrypt."""
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Check a plaintext password against its bcrypt hash."""
    return pwd_context.verify(plain_password, hashed_password)


# ── JWT utilities ──────────────────────────────────────────────────────────
security = HTTPBearer()


def create_access_token(data: dict) -> tuple[str, int]:
    """
    Generate a signed JWT.

    Returns:
        (token_string, expiry_in_seconds)
    """
    expiry_seconds = settings.JWT_EXPIRY_HOURS * 3600
    expire = datetime.utcnow() + timedelta(seconds=expiry_seconds)
    payload = {**data, "exp": expire, "iat": datetime.utcnow()}
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    return token, expiry_seconds


def decode_access_token(token: str) -> dict:
    """
    Decode and validate a JWT. Raises HTTP 401 on failure.
    """
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired. Please log in again.",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token.",
        )


# ── FastAPI dependency: get current user from Bearer token ─────────────────
async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """
    Dependency to extract the current user from the Authorization header.

    Usage in any route (in this service OR the main AI service):
        @router.get("/me")
        async def me(user = Depends(get_current_user)):
            return user
    """
    return decode_access_token(credentials.credentials)
