"""Security helpers for password hashing and JWT token generation."""

import datetime
import logging
import uuid
from typing import cast

import bcrypt
from jose import jwt  # type: ignore[import-untyped]

from wealthdock_server.core.config import get_settings

logger = logging.getLogger(__name__)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain text password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except ValueError as e:
        logger.error("Bcrypt password verification failed (malformed hash): %s", e)
        return False


def get_password_hash(password: str) -> str:
    """Hash a password using bcrypt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def create_access_token(
    subject: str | uuid.UUID, expires_delta: datetime.timedelta | None = None
) -> str:
    """Generate a JWT token for the given subject (e.g. user id or email)."""
    settings = get_settings()
    if expires_delta:
        expire = datetime.datetime.now(datetime.UTC) + expires_delta
    else:
        expire = datetime.datetime.now(datetime.UTC) + datetime.timedelta(
            minutes=settings.jwt_access_token_expire_minutes
        )
    to_encode = {"exp": expire, "sub": str(subject)}
    return cast(
        str,
        jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm),
    )
