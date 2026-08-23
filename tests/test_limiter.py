"""Tests for rate limiting on authentication and sync endpoints."""

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

from tests.test_api import engine, override_get_db
from wealthdock_server.core.limiter import limiter
from wealthdock_server.db.base import Base
from wealthdock_server.db.session import get_db
from wealthdock_server.main import app


@pytest.fixture(autouse=True)
async def setup_limiter_and_db() -> AsyncGenerator[None, None]:
    """Enable limiter and set up clean database for rate limit tests."""
    limiter.enabled = True
    limiter.reset()
    app.dependency_overrides[get_db] = override_get_db
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    app.dependency_overrides.clear()
    limiter.reset()
    limiter.enabled = False


@pytest.mark.asyncio
async def test_auth_register_rate_limit() -> None:
    """Verify register endpoint rate limit is enforced after 5 requests."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # First 5 requests should pass validation or register (not return 429)
        for i in range(5):
            res = await client.post(
                "/api/v1/auth/register",
                json={"email": f"user{i}@example.com", "password": "secure_password_123"},
            )
            assert res.status_code != 429
            # Check rate limit status headers are included in the API response.
            assert "x-ratelimit-limit" in res.headers
            assert "x-ratelimit-remaining" in res.headers

        # 6th request must be rate limited (429)
        res = await client.post(
            "/api/v1/auth/register",
            json={"email": "limit@example.com", "password": "secure_password_123"},
        )
        assert res.status_code == 429
        assert "x-ratelimit-limit" in res.headers


@pytest.mark.asyncio
async def test_auth_login_rate_limit() -> None:
    """Verify login endpoint rate limit is enforced after 5 requests."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # First 5 requests should be unauthorized or OK (not 429)
        for _ in range(5):
            res = await client.post(
                "/api/v1/auth/login",
                json={"email": "nonexistent@example.com", "password": "password"},
            )
            assert res.status_code != 429

        # 6th request must be rate limited (429)
        res = await client.post(
            "/api/v1/auth/login",
            json={"email": "nonexistent@example.com", "password": "password"},
        )
        assert res.status_code == 429


@pytest.mark.asyncio
async def test_sync_rate_limit() -> None:
    """Verify sync endpoint rate limit is enforced after 60 requests."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # First register and log in to get a valid token
        reg_res = await client.post(
            "/api/v1/auth/register",
            json={"email": "sync_limit@example.com", "password": "secure_password_123"},
        )
        assert reg_res.status_code == 201
        token = reg_res.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Reset limiter for sync route key (based on JWT subject)
        limiter.reset()

        # Send 60 requests
        for _ in range(60):
            res = await client.get("/api/v1/sync", headers=headers)
            assert res.status_code == 200

        # 61st request must be rate limited (429)
        res = await client.get("/api/v1/sync", headers=headers)
        assert res.status_code == 429
