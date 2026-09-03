"""Outbound clients for external market-data providers (Finnhub, CoinGecko)."""

import httpx

from wealthdock_server.core.config import get_settings


class QuoteNotFoundError(Exception):
    """Raised when a provider has no data for the requested symbol."""


class QuoteProviderError(Exception):
    """Raised when a provider is unreachable or returns an unexpected error."""


COINGECKO_ID_MAP: dict[str, str] = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "DOGE": "dogecoin",
    "ADA": "cardano",
    "XRP": "ripple",
    "BNB": "binancecoin",
    "MATIC": "matic-network",
    "DOT": "polkadot",
    "LTC": "litecoin",
    "AVAX": "avalanche-2",
    "LINK": "chainlink",
    "UNI": "uniswap",
    "ATOM": "cosmos",
    "USDT": "tether",
    "USDC": "usd-coin",
}


async def fetch_finnhub_quote(symbol: str) -> float:
    """Fetch the current price for a stock/ETF symbol from Finnhub."""
    settings = get_settings()
    if not settings.finnhub_api_key:
        raise RuntimeError("FINNHUB_API_KEY is not configured.")

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://finnhub.io/api/v1/quote",
                params={"symbol": symbol, "token": settings.finnhub_api_key},
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        raise QuoteProviderError(f"Finnhub request failed for '{symbol}': {e}") from e

    price = data.get("c")
    if price is None or price == 0:
        raise QuoteNotFoundError(symbol)
    return float(price)


async def fetch_coingecko_price(symbol: str) -> float:
    """Fetch the current USD price for a crypto symbol from CoinGecko."""
    coin_id = COINGECKO_ID_MAP.get(symbol.upper())
    if coin_id is None:
        raise QuoteNotFoundError(symbol)

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": coin_id, "vs_currencies": "usd"},
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        raise QuoteProviderError(f"CoinGecko request failed for '{symbol}': {e}") from e

    price = data.get(coin_id, {}).get("usd")
    if price is None:
        raise QuoteNotFoundError(symbol)
    return float(price)
