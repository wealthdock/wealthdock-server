"""FastAPI application factory for wealthdock-server."""

import http
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.exceptions import HTTPException as StarletteHTTPException

from wealthdock_server.api.v1 import market_data
from wealthdock_server.api.v1.auth import router as auth_router
from wealthdock_server.api.v1.bank import router as bank_router
from wealthdock_server.api.v1.sync import router as sync_router
from wealthdock_server.core.config import get_settings

logger = logging.getLogger(__name__)


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Global handler for Starlette/FastAPI HTTPExceptions."""
    try:
        title = http.HTTPStatus(exc.status_code).phrase
    except ValueError:
        title = "HTTP Error"

    content = {
        "type": "about:blank",
        "title": title,
        "status": exc.status_code,
        "detail": exc.detail if isinstance(exc.detail, str) else str(exc.detail),
        "instance": request.url.path,
    }
    return JSONResponse(
        status_code=exc.status_code,
        content=content,
        media_type="application/problem+json",
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Global handler for Pydantic/FastAPI RequestValidationError."""
    errors = []
    for err in exc.errors():
        errors.append(
            {
                "loc": [str(x) for x in err.get("loc", [])],
                "msg": err.get("msg", ""),
                "type": err.get("type", ""),
            }
        )

    content = {
        "type": "validation-error",
        "title": "Validation Error",
        "status": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "detail": "The request contains invalid data.",
        "instance": request.url.path,
        "errors": errors,
    }
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=content,
        media_type="application/problem+json",
    )


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Global handler for unhandled exceptions to prevent leaking server details/stack traces."""
    logger.exception("Unhandled server error occurred on path: %s", request.url.path, exc_info=exc)

    content = {
        "type": "about:blank",
        "title": "Internal Server Error",
        "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
        "detail": "An unexpected error occurred on the server.",
        "instance": request.url.path,
    }
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=content,
        media_type="application/problem+json",
    )


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

    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, generic_exception_handler)

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness check used by orchestrators/self-host deployments."""
        return {"status": "ok"}

    app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])
    app.include_router(bank_router, prefix="/api/v1")
    app.include_router(sync_router, prefix="/api/v1")
    app.include_router(market_data.router, prefix="/api/v1")

    settings = get_settings()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    return app


app = create_app()
