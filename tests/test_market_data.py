"""Integration tests for the market-data quote endpoint."""

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from wealthdock_server.db.base import Base
from wealthdock_server.db.session import get_db
from wealthdock_server.main import app

DATABASE_URL = "sqlite+aiosqlite:///:memory:"
engine = create_async_engine(DATABASE_URL, echo=False)
TestingSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

# Patch targets match how market_data.py imports these names (src.-prefixed),
# since that's the module path actually registered in sys.modules at runtime.
FINNHUB_PATCH_TARGET = "wealthdock_server.api.v1.market_data.fetch_finnhub_quote"
COINGECKO_PATCH_TARGET = "wealthdock_server.api.v1.market_data.fetch_coingecko_price"


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


async def _get_auth_headers(client: AsyncClient) -> dict[str, str]:
    """Register a throwaway user and return valid auth headers."""
    res = await client.post(
        "/api/v1/auth/register",
        json={"email": "marketdata@example.com", "password": "secure_password_123"},
    )
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_quote_requires_authentication() -> None:
    """Verify the endpoint rejects requests with no auth token."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get(
            "/api/v1/market-data/quote", params={"symbol": "AAPL", "asset_class": "stock"}
        )
        assert res.status_code == 401


@pytest.mark.asyncio
async def test_stock_quote_cache_miss_then_hit() -> None:
    """Verify a fresh symbol fetches from Finnhub, then a repeat call is served from cache."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = await _get_auth_headers(client)

        with patch(FINNHUB_PATCH_TARGET, new_callable=AsyncMock) as mock_finnhub:
            mock_finnhub.return_value = 309.35

            # First call: cache miss, provider called
            res = await client.get(
                "/api/v1/market-data/quote",
                params={"symbol": "AAPL", "asset_class": "stock"},
                headers=headers,
            )
            assert res.status_code == 200
            body = res.json()
            assert body["symbol"] == "AAPL"
            assert body["price"] == 309.35
            assert body["cached"] is False
            mock_finnhub.assert_awaited_once_with("AAPL")

            # Second call: cache hit, provider NOT called again
            res = await client.get(
                "/api/v1/market-data/quote",
                params={"symbol": "AAPL", "asset_class": "stock"},
                headers=headers,
            )
            assert res.status_code == 200
            body = res.json()
            assert body["price"] == 309.35
            assert body["cached"] is True
            mock_finnhub.assert_awaited_once()  # still only called once total


@pytest.mark.asyncio
async def test_crypto_quote_dispatches_to_coingecko() -> None:
    """Verify asset_class=crypto calls the CoinGecko client, not Finnhub."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = await _get_auth_headers(client)

        with (
            patch(COINGECKO_PATCH_TARGET, new_callable=AsyncMock) as mock_coingecko,
            patch(FINNHUB_PATCH_TARGET, new_callable=AsyncMock) as mock_finnhub,
        ):
            mock_coingecko.return_value = 76636.0

            res = await client.get(
                "/api/v1/market-data/quote",
                params={"symbol": "BTC", "asset_class": "crypto"},
                headers=headers,
            )
            assert res.status_code == 200
            body = res.json()
            assert body["price"] == 76636.0
            assert body["cached"] is False
            mock_coingecko.assert_awaited_once_with("BTC")
            mock_finnhub.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_symbol_returns_404_not_500() -> None:
    """Verify a symbol the provider can't find returns a clean 404."""
    from wealthdock_server.core.providers import QuoteNotFoundError

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = await _get_auth_headers(client)

        with patch(FINNHUB_PATCH_TARGET, new_callable=AsyncMock) as mock_finnhub:
            mock_finnhub.side_effect = QuoteNotFoundError("NOTAREAL")

            res = await client.get(
                "/api/v1/market-data/quote",
                params={"symbol": "NOTAREAL", "asset_class": "stock"},
                headers=headers,
            )
            assert res.status_code == 404


@pytest.mark.asyncio
async def test_different_symbols_cached_independently() -> None:
    """Verify caching one symbol doesn't produce a hit for a different symbol."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = await _get_auth_headers(client)

        with patch(FINNHUB_PATCH_TARGET, new_callable=AsyncMock) as mock_finnhub:
            mock_finnhub.side_effect = [309.35, 411.20]

            res = await client.get(
                "/api/v1/market-data/quote",
                params={"symbol": "AAPL", "asset_class": "stock"},
                headers=headers,
            )
            assert res.json()["cached"] is False

            res = await client.get(
                "/api/v1/market-data/quote",
                params={"symbol": "MSFT", "asset_class": "stock"},
                headers=headers,
            )
            assert res.json()["cached"] is False
            assert res.json()["price"] == 411.20

            assert mock_finnhub.await_count == 2


@pytest.mark.asyncio
async def test_provider_failure_returns_503_not_500() -> None:
    """Verify a provider timeout/connection/HTTP error returns a clean 503, not an unhandled 500."""
    from wealthdock_server.core.providers import QuoteProviderError

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = await _get_auth_headers(client)

        with patch(FINNHUB_PATCH_TARGET, new_callable=AsyncMock) as mock_finnhub:
            mock_finnhub.side_effect = QuoteProviderError("Finnhub request timed out")

            res = await client.get(
                "/api/v1/market-data/quote",
                params={"symbol": "AAPL", "asset_class": "stock"},
                headers=headers,
            )
            assert res.status_code == 503
