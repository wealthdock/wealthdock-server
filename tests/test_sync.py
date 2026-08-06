"""Tests for the cross-device synchronization API and SyncState model."""

import datetime
import uuid
from collections.abc import AsyncGenerator
from typing import cast

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt  # type: ignore[import-untyped]
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from wealthdock_server.core.config import get_settings
from wealthdock_server.db.base import Base
from wealthdock_server.db.models import SyncState, User
from wealthdock_server.db.session import get_db
from wealthdock_server.main import app

# Create in-memory SQLite engine and session factory for isolated testing
test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
test_session_factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)


def test_sync_state_model_creation() -> None:
    """Verify SyncState model attributes, relationships, and persistence."""
    engine = create_engine("sqlite:///", echo=False)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)

    # 1. Create a user and sync state
    with session_factory() as session:
        user = User(
            email="sync_model@example.com",
            hashed_password="hashed_secure_password",
        )
        session.add(user)
        session.commit()
        user_id = user.id

        sync_state = SyncState(
            user_id=user_id,
            payload='{"assets": []}',
            version=1,
        )
        session.add(sync_state)
        session.commit()

    # 2. Fetch and assert the created sync state record
    with session_factory() as session:
        queried = session.get(SyncState, user_id)
        assert queried is not None
        assert queried.user_id == user_id
        assert queried.payload == '{"assets": []}'
        assert queried.version == 1
        assert isinstance(queried.updated_at, datetime.datetime)


async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency override to use the in-memory SQLite database session."""
    async with test_session_factory() as session:
        yield session


@pytest.fixture(autouse=True)
async def setup_db() -> AsyncGenerator[None, None]:
    """Re-create the database tables before each test case and manage overrides."""
    app.dependency_overrides[get_db] = override_get_db
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    app.dependency_overrides.clear()


def create_token(email: str) -> str:
    """Generate a JWT test token."""
    settings = get_settings()
    payload = {
        "sub": email,
        "exp": datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=30),
    }
    return cast(
        str,
        jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm),
    )


async def seed_user(email: str) -> uuid.UUID:
    """Helper to seed a user in the test database and return its ID."""
    async with test_session_factory() as session:
        user_id = uuid.uuid4()
        user = User(
            id=user_id,
            email=email,
            hashed_password="hashedpassword",
        )
        session.add(user)
        await session.commit()
        return user_id


@pytest.mark.asyncio
async def test_unauthenticated_sync_denied() -> None:
    """Verify that unauthenticated requests to the sync endpoint are blocked."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post("/api/v1/sync", json={"since": None, "changes": []})
        assert res.status_code == 401


@pytest.mark.asyncio
async def test_initial_sync_and_push() -> None:
    """Verify clean pull on empty DB and basic push capabilities for authenticated user."""
    email = "user@example.com"
    await seed_user(email)
    token = create_token(email)
    headers = {"Authorization": f"Bearer {token}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Pull with since=None and no changes
        res = await client.post(
            "/api/v1/sync",
            json={"since": None, "changes": []},
            headers=headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert "sync_point" in data
        assert data["changes"] == []

        # 2. Push a new item
        t1 = datetime.datetime.now(datetime.UTC).isoformat()
        item = {
            "id": "uuid-1",
            "type": "account",
            "data": {"name": "Savings", "balance": 1000.5},
            "updated_at": t1,
            "deleted": False,
        }
        res = await client.post(
            "/api/v1/sync",
            json={"since": None, "changes": [item]},
            headers=headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert len(data["changes"]) == 1
        assert data["changes"][0]["id"] == "uuid-1"


@pytest.mark.asyncio
async def test_user_data_isolation() -> None:
    """Verify that user A cannot see or modify user B's synced records."""
    # Seed two separate users
    await seed_user("userA@example.com")
    await seed_user("userB@example.com")
    token_a = create_token("userA@example.com")
    token_b = create_token("userB@example.com")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # User A pushes a record
        t1 = datetime.datetime.now(datetime.UTC).isoformat()
        item = {
            "id": "record-1",
            "type": "account",
            "data": {"name": "User A Account"},
            "updated_at": t1,
            "deleted": False,
        }
        res = await client.post(
            "/api/v1/sync",
            json={"since": None, "changes": [item]},
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert res.status_code == 200

        # User B pulls, should NOT see User A's record
        res_pull = await client.post(
            "/api/v1/sync",
            json={"since": None, "changes": []},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert res_pull.status_code == 200
        data_pull = res_pull.json()
        assert len(data_pull["changes"]) == 0

        # User B attempts to push to User A's record ID
        item_skewed = {
            "id": "record-1",
            "type": "account",
            "data": {"name": "Hijacked"},
            "updated_at": t1,
            "deleted": False,
        }
        res_hijack = await client.post(
            "/api/v1/sync",
            json={"since": None, "changes": [item_skewed]},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert res_hijack.status_code == 200

        # Verify that User A's record is unchanged and not hijacked
        res_verify = await client.post(
            "/api/v1/sync",
            json={"since": None, "changes": []},
            headers={"Authorization": f"Bearer {token_a}"},
        )
        data_verify = res_verify.json()
        assert len(data_verify["changes"]) == 1
        assert data_verify["changes"][0]["data"] == {"name": "User A Account"}


@pytest.mark.asyncio
async def test_sync_conflict_resolution_lww() -> None:
    """Verify that concurrent edits resolve using Last-Write-Wins based on timestamps."""
    email = "user@example.com"
    await seed_user(email)
    token = create_token(email)
    headers = {"Authorization": f"Bearer {token}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Setup: Push an item with timestamp T1
        t1 = datetime.datetime(2026, 8, 2, 12, 0, 0, tzinfo=datetime.UTC)
        item = {
            "id": "uuid-1",
            "type": "account",
            "data": {"name": "Savings", "balance": 100.0},
            "updated_at": t1.isoformat(),
            "deleted": False,
        }
        await client.post(
            "/api/v1/sync",
            json={"since": None, "changes": [item]},
            headers=headers,
        )

        # Scenario A: Push edit with older timestamp T0 (LWW rejects incoming)
        t0 = datetime.datetime(2026, 8, 2, 11, 0, 0, tzinfo=datetime.UTC)
        older_item = {
            "id": "uuid-1",
            "type": "account",
            "data": {"name": "Savings-Old", "balance": 50.0},
            "updated_at": t0.isoformat(),
            "deleted": False,
        }
        res = await client.post(
            "/api/v1/sync",
            json={"since": t0.isoformat(), "changes": [older_item]},
            headers=headers,
        )
        assert res.status_code == 200
        data = res.json()

        # The response must contain the newer server version since it wins conflict
        assert len(data["changes"]) == 1
        assert data["changes"][0]["id"] == "uuid-1"
        assert data["changes"][0]["data"] == {"name": "Savings", "balance": 100.0}

        # Scenario B: Push edit with newer timestamp T2 (LWW accepts incoming)
        t2 = datetime.datetime(2026, 8, 2, 13, 0, 0, tzinfo=datetime.UTC)
        newer_item = {
            "id": "uuid-1",
            "type": "account",
            "data": {"name": "Savings-New", "balance": 200.0},
            "updated_at": t2.isoformat(),
            "deleted": False,
        }
        res = await client.post(
            "/api/v1/sync",
            json={"since": t1.isoformat(), "changes": [newer_item]},
            headers=headers,
        )
        assert res.status_code == 200
        data = res.json()

        # The response must contain the updated newer version
        assert len(data["changes"]) == 1
        assert data["changes"][0]["data"] == {
            "name": "Savings-New",
            "balance": 200.0,
        }


@pytest.mark.asyncio
async def test_sync_since_filtering_on_server_time() -> None:
    """Verify that filtering runs on server_updated_at rather than client updated_at."""
    email = "user@example.com"
    await seed_user(email)
    token = create_token(email)
    headers = {"Authorization": f"Bearer {token}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Push item 1 with client timestamp = Monday
        t_monday = datetime.datetime(2026, 8, 3, 10, 0, 0, tzinfo=datetime.UTC)
        item1 = {
            "id": "uuid-1",
            "type": "account",
            "data": {"balance": 100},
            "updated_at": t_monday.isoformat(),
            "deleted": False,
        }
        res1 = await client.post(
            "/api/v1/sync",
            json={"since": None, "changes": [item1]},
            headers=headers,
        )
        assert res1.status_code == 200
        sync_point_after_1 = res1.json()["sync_point"]

        # Push item 2 with client timestamp = Sunday (e.g. offline change)
        t_sunday = datetime.datetime(2026, 8, 2, 10, 0, 0, tzinfo=datetime.UTC)
        item2 = {
            "id": "uuid-2",
            "type": "account",
            "data": {"balance": 200},
            "updated_at": t_sunday.isoformat(),
            "deleted": False,
        }
        res2 = await client.post(
            "/api/v1/sync",
            json={"since": None, "changes": [item2]},
            headers=headers,
        )
        assert res2.status_code == 200

        # Pull changes since sync_point_after_1. Even though item2 has an older
        # client updated_at, it was written later, so server_updated_at >
        # sync_point_after_1 is true.
        res_pull = await client.post(
            "/api/v1/sync",
            json={"since": sync_point_after_1, "changes": []},
            headers=headers,
        )
        assert res_pull.status_code == 200
        changes = res_pull.json()["changes"]
        assert len(changes) == 1
        assert changes[0]["id"] == "uuid-2"


@pytest.mark.asyncio
async def test_soft_deletion() -> None:
    """Verify that soft-deleted items are stored and returned as tombstones."""
    email = "user@example.com"
    await seed_user(email)
    token = create_token(email)
    headers = {"Authorization": f"Bearer {token}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Push active item
        t1 = datetime.datetime.now(datetime.UTC).isoformat()
        item = {
            "id": "del-1",
            "type": "account",
            "data": {"balance": 100},
            "updated_at": t1,
            "deleted": False,
        }
        await client.post(
            "/api/v1/sync",
            json={"since": None, "changes": [item]},
            headers=headers,
        )

        # Push tombstone with newer timestamp
        t2 = (datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=1)).isoformat()
        deleted_item = {
            "id": "del-1",
            "type": "account",
            "data": {"balance": 100},
            "updated_at": t2,
            "deleted": True,
        }
        await client.post(
            "/api/v1/sync",
            json={"since": None, "changes": [deleted_item]},
            headers=headers,
        )

        # Pull and assert tombstone is returned
        res = await client.post(
            "/api/v1/sync",
            json={"since": None, "changes": []},
            headers=headers,
        )
        assert res.status_code == 200
        changes = res.json()["changes"]
        assert len(changes) == 1
        assert changes[0]["deleted"] is True


@pytest.mark.asyncio
async def test_timestamp_clamping() -> None:
    """Verify client timestamps far in the future are clamped to server time."""
    email = "user@example.com"
    await seed_user(email)
    token = create_token(email)
    headers = {"Authorization": f"Bearer {token}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        future_time = datetime.datetime(2099, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)
        item = {
            "id": "clamp-1",
            "type": "account",
            "data": {},
            "updated_at": future_time.isoformat(),
            "deleted": False,
        }
        res = await client.post(
            "/api/v1/sync",
            json={"since": None, "changes": [item]},
            headers=headers,
        )
        assert res.status_code == 200
        sync_point = res.json()["sync_point"]

        # Pull and verify that the updated_at was clamped and doesn't remain 2099
        res_pull = await client.post(
            "/api/v1/sync",
            json={"since": None, "changes": []},
            headers=headers,
        )
        changes = res_pull.json()["changes"]
        assert len(changes) == 1
        # The stored updated_at should be <= the returned sync_point
        assert datetime.datetime.fromisoformat(
            changes[0]["updated_at"]
        ) <= datetime.datetime.fromisoformat(sync_point)
