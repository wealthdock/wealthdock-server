"""Market data endpoints — live stock/crypto quotes with TTL caching."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from wealthdock_server.api.v1.dependencies import get_current_user
from wealthdock_server.core.providers import (
    QuoteNotFoundError,
    QuoteProviderError,
    fetch_coingecko_price,
    fetch_finnhub_quote,
)
from wealthdock_server.db.models import User
from wealthdock_server.db.quote_cache import get_cached_quote, upsert_quote_cache
from wealthdock_server.db.session import get_db

router = APIRouter(prefix="/market-data", tags=["market-data"])


class QuoteResponse(BaseModel):
    """Response payload for a market-data quote lookup."""

    symbol: str
    asset_class: Literal["stock", "crypto"]
    price: float
    cached: bool


@router.get("/quote", response_model=QuoteResponse)
async def get_quote(
    symbol: str,
    asset_class: Literal["stock", "crypto"],
    db: Annotated[AsyncSession, Depends(get_db)],
    _current_user: Annotated[User, Depends(get_current_user)],
) -> QuoteResponse:
    """Return the current price for a stock or crypto symbol, using a TTL cache."""
    symbol = symbol.upper()

    cached_price = await get_cached_quote(db, symbol)
    if cached_price is not None:
        return QuoteResponse(
            symbol=symbol, asset_class=asset_class, price=cached_price, cached=True
        )

    try:
        if asset_class == "stock":
            price = await fetch_finnhub_quote(symbol)
        else:
            price = await fetch_coingecko_price(symbol)
    except QuoteNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No quote found for symbol '{symbol}'",
        ) from e
    except QuoteProviderError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Market data provider unavailable for '{symbol}'",
        ) from e

    await upsert_quote_cache(db, symbol, asset_class, price)

    return QuoteResponse(symbol=symbol, asset_class=asset_class, price=price, cached=False)
