"""FastAPI application factory for wealthdock-server."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from wealthdock_server.api.v1.auth import router as auth_router
from wealthdock_server.api.v1.sync import router as sync_router
from wealthdock_server.core.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage application lifecycle event handlers (e.g. database engine)."""
    engine = create_async_engine(get_settings().database_url, echo=False)
    app.state.db_engine = engine
    app.state.db_session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield
    finally:
        await engine.dispose()


def create_app() -> FastAPI:
    """Build and configure the FastAPI application.

    Domain routers get mounted under `/api/v1` as they're added. A
    top-level `/health` endpoint is exposed for liveness checks.
    """
    app = FastAPI(
        title="wealthdock-server",
        description=(
            "Self-hostable backend for wealthdock: cross-device sync, bank-API "
            "integration, data storage, auth, and encryption of sensitive "
            "financial data."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness check used by orchestrators/self-host deployments."""
        return {"status": "ok"}

    app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])
    app.include_router(sync_router, prefix="/api/v1")

    settings = get_settings()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    return app


app = create_app()
