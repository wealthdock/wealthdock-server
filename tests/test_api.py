"""Integration tests for authentication and synchronization APIs."""

import datetime
from collections.abc import AsyncGenerator
from typing import cast

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt  # type: ignore[import-untyped]
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from wealthdock_server.core.config import get_settings
from wealthdock_server.db.base import Base
from wealthdock_server.db.models import User
from wealthdock_server.db.session import get_db
from wealthdock_server.main import app

DATABASE_URL = "sqlite+aiosqlite:///:memory:"
engine = create_async_engine(DATABASE_URL, echo=False)
TestingSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency override to yield a test session."""
    async with TestingSessionLocal() as session:
        yield session


@pytest.fixture(autouse=True)
async def setup_db() -> AsyncGenerator[None, None]:
    """Clean the database before and after each test run."""
    app.dependency_overrides[get_db] = override_get_db
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_happy_path_auth_and_sync() -> None:
    """Verify registration, login, and sync updates work correctly."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Register
        reg_resp = await client.post(
            "/api/v1/auth/register",
            json={"email": "happy@example.com", "password": "secure_password_123"},
        )
        assert reg_resp.status_code == 201
        assert "access_token" in reg_resp.json()

        # 2. Login
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "happy@example.com", "password": "secure_password_123"},
        )
        assert login_resp.status_code == 200
        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 3. GET initial sync state (version 0)
        get_resp = await client.get("/api/v1/sync", headers=headers)
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert data["version"] == 0
        assert "assets" in data["payload"]

        # 4. POST sync state (version 0 -> version 1)
        payload_data = '{"assets":[{"id":"1","value":100}]}'
        post_resp = await client.post(
            "/api/v1/sync",
            headers=headers,
            json={"payload": payload_data, "version": 0},
        )
        assert post_resp.status_code == 200
        assert post_resp.json()["version"] == 1
        assert post_resp.json()["payload"] == payload_data


@pytest.mark.asyncio
async def test_auth_registration_validation() -> None:
    """Verify email formatting, password byte length, and duplicate registration behavior."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Invalid email address
        res = await client.post(
            "/api/v1/auth/register",
            json={"email": "not-an-email", "password": "secure_password"},
        )
        assert res.status_code == 422

        # Too short password
        res = await client.post(
            "/api/v1/auth/register",
            json={"email": "valid@example.com", "password": "short"},
        )
        assert res.status_code == 422

        # Empty password
        res = await client.post(
            "/api/v1/auth/register",
            json={"email": "valid@example.com", "password": ""},
        )
        assert res.status_code == 422

        # Non-ASCII password exceeding 72 bytes (72 chars of 'ä' = 144 bytes)
        res = await client.post(
            "/api/v1/auth/register",
            json={"email": "nonascii@example.com", "password": "ä" * 72},
        )
        assert res.status_code == 422

        # Valid non-ASCII password (30 chars of 'ä' = 60 bytes <= 72)
        res = await client.post(
            "/api/v1/auth/register",
            json={"email": "valid_nonascii@example.com", "password": "ä" * 30},
        )
        assert res.status_code == 201

        # Valid registration
        res = await client.post(
            "/api/v1/auth/register",
            json={"email": "dup@example.com", "password": "secure_password"},
        )
        assert res.status_code == 201

        # Duplicate email registration returns 201 with dummy token to defend against enumeration
        res = await client.post(
            "/api/v1/auth/register",
            json={"email": "dup@example.com", "password": "secure_password"},
        )
        assert res.status_code == 201
        dummy_token = res.json()["access_token"]

        # Using dummy token fails authentication (401 Unauthorized)
        res = await client.get(
            "/api/v1/sync",
            headers={"Authorization": f"Bearer {dummy_token}"},
        )
        assert res.status_code == 401


@pytest.mark.asyncio
async def test_auth_login_edge_cases() -> None:
    """Verify wrong password, wrong email, and inactive user rejection return 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Seed a user
        await client.post(
            "/api/v1/auth/register",
            json={"email": "user@example.com", "password": "secure_password"},
        )

        # Login with incorrect password
        res = await client.post(
            "/api/v1/auth/login",
            json={"email": "user@example.com", "password": "wrong_password_123"},
        )
        assert res.status_code == 401

        # Login with non-existent email
        res = await client.post(
            "/api/v1/auth/login",
            json={"email": "missing@example.com", "password": "secure_password"},
        )
        assert res.status_code == 401

        # Login with inactive user
        async with TestingSessionLocal() as session:
            result = await session.execute(select(User).where(User.email == "user@example.com"))
            user = result.scalar_one()
            user.is_active = False
            await session.commit()

        res = await client.post(
            "/api/v1/auth/login",
            json={"email": "user@example.com", "password": "secure_password"},
        )
        assert res.status_code == 401


@pytest.mark.asyncio
async def test_sync_unauthorized_cases() -> None:
    """Verify unauthorized tokens, expired signatures, and missing auth header rejections."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Missing authorization header
        res = await client.get("/api/v1/sync")
        assert res.status_code == 401

        # 2. Malformed token format
        res = await client.get("/api/v1/sync", headers={"Authorization": "Bearer malformed"})
        assert res.status_code == 401

        # 3. Expired token signature
        settings = get_settings()
        expired_time = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=10)
        expired_token = jwt.encode(
            {"exp": expired_time, "sub": "some-uuid"},
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
        res = await client.get(
            "/api/v1/sync",
            headers={"Authorization": f"Bearer {cast(str, expired_token)}"},
        )
        assert res.status_code == 401


@pytest.mark.asyncio
async def test_sync_optimistic_concurrency() -> None:
    """Verify version mismatch conflicts return 409 Conflict."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Register and login
        reg_resp = await client.post(
            "/api/v1/auth/register",
            json={"email": "concur@example.com", "password": "secure_password"},
        )
        token = reg_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # POST with wrong initial version (expected 0, sending 5) -> returns 409
        payload_data = '{"assets":[]}'
        res = await client.post(
            "/api/v1/sync",
            headers=headers,
            json={"payload": payload_data, "version": 5},
        )
        assert res.status_code == 409

        # POST with correct version (0) -> returns version 1
        res = await client.post(
            "/api/v1/sync",
            headers=headers,
            json={"payload": payload_data, "version": 0},
        )
        assert res.status_code == 200
        assert res.json()["version"] == 1

        # POST with stale version (0 again, but DB is at 1) -> returns 409 Conflict
        res = await client.post(
            "/api/v1/sync",
            headers=headers,
            json={"payload": payload_data, "version": 0},
        )
        assert res.status_code == 409

        # POST with current version (1) -> returns version 2
        res = await client.post(
            "/api/v1/sync",
            headers=headers,
            json={"payload": payload_data, "version": 1},
        )
        assert res.status_code == 200
        assert res.json()["version"] == 2


@pytest.mark.asyncio
async def test_user_data_isolation() -> None:
    """Verify that User A cannot read or modify User B's sync state data."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Register User A
        res_a = await client.post(
            "/api/v1/auth/register",
            json={"email": "usera@example.com", "password": "secure_password"},
        )
        token_a = res_a.json()["access_token"]

        # Register User B
        res_b = await client.post(
            "/api/v1/auth/register",
            json={"email": "userb@example.com", "password": "secure_password"},
        )
        token_b = res_b.json()["access_token"]

        # User A updates sync state
        payload_a = '{"assets":[{"id":"a1"}]}'
        res = await client.post(
            "/api/v1/sync",
            headers={"Authorization": f"Bearer {token_a}"},
            json={"payload": payload_a, "version": 0},
        )
        assert res.status_code == 200

        # User B reads sync state -> must receive the empty default, NOT User A's data
        res = await client.get("/api/v1/sync", headers={"Authorization": f"Bearer {token_b}"})
        assert res.status_code == 200
        data = res.json()
        assert data["version"] == 0
        assert "a1" not in data["payload"]
