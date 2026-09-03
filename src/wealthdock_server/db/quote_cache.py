"""Read/write helpers for the quote_cache table (TTL-based price cache)."""

import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from wealthdock_server.db.models import QuoteCache, utcnow

CACHE_TTL_MINUTES = 10


async def get_cached_quote(db: AsyncSession, symbol: str) -> float | None:
    """Return a cached price for `symbol` if it exists and isn't stale, else None."""
    result = await db.execute(select(QuoteCache).where(QuoteCache.symbol == symbol))
    row = result.scalar_one_or_none()
    if row is None:
        return None

    age = utcnow() - row.fetched_at
    if age > datetime.timedelta(minutes=CACHE_TTL_MINUTES):
        return None

    return row.price


async def upsert_quote_cache(db: AsyncSession, symbol: str, asset_class: str, price: float) -> None:
    """Insert or update the cached price for `symbol`."""
    stmt = insert(QuoteCache).values(
        symbol=symbol,
        asset_class=asset_class,
        price=price,
        fetched_at=utcnow(),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[QuoteCache.symbol],
        set_={"price": price, "asset_class": asset_class, "fetched_at": utcnow()},
    )
    await db.execute(stmt)
    await db.commit()
