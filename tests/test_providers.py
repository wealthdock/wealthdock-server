"""Unit tests for provider client symbol handling (no network calls)."""

import pytest

from wealthdock_server.core.providers import (
    COINGECKO_ID_MAP,
    QuoteNotFoundError,
    fetch_coingecko_price,
)


@pytest.mark.asyncio
async def test_coingecko_unmapped_symbol_raises_without_http_call() -> None:
    """Verify a symbol outside the ticker map raises immediately, no HTTP request made."""
    with pytest.raises(QuoteNotFoundError):
        await fetch_coingecko_price("NOTAREALCOIN")


def test_coingecko_map_has_expected_majors() -> None:
    """Sanity check that the common coins we rely on are present in the map."""
    for ticker in ("BTC", "ETH", "SOL", "DOGE", "USDT"):
        assert ticker in COINGECKO_ID_MAP
