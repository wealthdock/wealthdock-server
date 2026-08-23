"""Rate limiter configuration and setup."""

from fastapi import Request
from jose import JWTError, jwt  # type: ignore[import-untyped]
from slowapi import Limiter
from slowapi.util import get_remote_address

from wealthdock_server.core.config import get_settings


def get_limiter_key(request: Request) -> str:
    """Return rate limit key based on JWT subject or client IP address.

    Args:
        request: The incoming request object.

    Returns:
        A string key representing the client or user.
    """
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
        settings = get_settings()
        try:
            payload = jwt.decode(
                token,
                settings.jwt_secret,
                algorithms=[settings.jwt_algorithm],
            )
            user_id = payload.get("sub")
            if user_id:
                return f"user:{user_id}"
        except JWTError:
            pass
    return get_remote_address(request)


settings = get_settings()
storage_uri = settings.redis_url or "memory://"

limiter = Limiter(
    key_func=get_limiter_key,
    storage_uri=storage_uri,
    headers_enabled=True,
)
