"""
app/main.py
─────────────────────────────────────────────────────────────────────────────
FastAPI application factory for SanjeevaniRxAI.

Features
────────
• FastAPI app with Swagger + ReDoc enabled
• All routers mounted under /api/v1
• SlowAPI rate limiting (per-IP, configurable)
• Global exception handlers (HTTP + unhandled 500)
• MongoDB lifecycle: connect on startup, close on shutdown
• /health endpoint for Docker / load-balancer probes
• CORS middleware
• Request ID middleware for traceability
"""

from __future__ import annotations

import time
import uuid
import os
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from app.api import (
    alerts,
    customers,
    dashboard,
    orders,
    products,
    recommendations,
    chat,
    agent_routes,
)
from app.config import settings
from app.database.mongo_client import close_client, get_client, health_check
from app.utils.logger import get_logger, setup_logging

# ── Logging must be configured before the first logger is obtained ─────────
setup_logging()
logger = get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Rate limiter (SlowAPI)
# ─────────────────────────────────────────────────────────────────────────────
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[f"{settings.RATE_LIMIT_PER_MINUTE}/minute"],
)


# ─────────────────────────────────────────────────────────────────────────────
# Application lifespan (startup / shutdown)
# ─────────────────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan: connect to MongoDB on startup, close on shutdown."""
    logger.info(
        "SanjeevaniRxAI API starting",
        extra={"env": settings.ENV, "prefix": settings.API_PREFIX},
    )
    try:
        # We try to connect, but don't block the entire startup if it fails
        # so that Render's health check can still see the app is "running".
        get_client()  
        logger.info("MongoDB ready ✓")
    except Exception as exc:
        logger.error(
            "MongoDB connection failed at startup - App will continue in degraded mode", 
            extra={"error": str(exc)}
        )
        # We don't re-raise here to allow the app to bind to the port
        # and respond to health checks.

    yield  # ── application is live ──────────────────────────────────────

    logger.info("SanjeevaniRxAI API shutting down…")
    try:
        close_client()
        logger.info("Shutdown complete ✓")
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# App factory
# ─────────────────────────────────────────────────────────────────────────────


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_TITLE,
        version=settings.APP_VERSION,
        description=(
            "**SanjeevaniRxAI** — Intelligent Pharmacy Management API.\n\n"
            "Provides patient context, refill prediction, inventory intelligence, "
            "safety validation, and recommendation endpoints."
        ),
        docs_url=f"{settings.API_PREFIX}/docs",
        redoc_url=f"{settings.API_PREFIX}/redoc",
        openapi_url=f"{settings.API_PREFIX}/openapi.json",
        lifespan=lifespan,
    )

    # ── Rate limiting ─────────────────────────────────────────────────────
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)

    # ── CORS ──────────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Request ID + timing middleware ────────────────────────────────────
    @app.middleware("http")
    async def add_request_id(request: Request, call_next: Callable) -> Response:
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time"] = f"{elapsed_ms}ms"
        return response

    # ── Routers ───────────────────────────────────────────────────────────
    prefix = settings.API_PREFIX
    app.include_router(customers.router, prefix=prefix)
    app.include_router(dashboard.router, prefix=prefix)
    app.include_router(products.router, prefix=prefix)
    app.include_router(orders.router, prefix=prefix)
    app.include_router(recommendations.router, prefix=prefix)
    app.include_router(alerts.router, prefix=prefix)
    app.include_router(chat.router, prefix=prefix)
    app.include_router(agent_routes.router, prefix=prefix)

    # ── Static Files ──────────────────────────────────────────────────────
    # Base directory of the project
    base_dir = Path(__file__).resolve().parent.parent
    static_dir = base_dir / "static"

    if not static_dir.exists():
        logger.warning(f"Static directory not found at {static_dir}")
        # Create it if it doesn't exist to avoid crash, though it should be there
        static_dir.mkdir(parents=True, exist_ok=True)

    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # ── Health probe ──────────────────────────────────────────────────────
    @app.get("/health", tags=["Health"], summary="Health check")
    def health():
        """Lightweight health probe used by Docker / load-balancers."""
        db_health = health_check()
        status = "healthy" if db_health["status"] == "ok" else "degraded"
        code = 200 if status == "healthy" else 503
        return JSONResponse(
            status_code=code,
            content={
                "status": status,
                "version": settings.APP_VERSION,
                "env": settings.ENV,
                "database": db_health,
            },
        )

    @app.get("/", tags=["Root"], include_in_schema=False)
    def root():
        index_path = static_dir / "index.html"
        if index_path.exists():
            return FileResponse(str(index_path))
        return JSONResponse(content={"message": "SanjeevaniRxAI API is running"})

    # ── Global exception handlers ─────────────────────────────────────────

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = exc.errors()
        logger.warning(
            f"Validation error at {request.url}: {errors}",
            extra={"url": str(request.url), "errors": errors},
        )
        return JSONResponse(
            status_code=422,
            content={
                "status": "error",
                "detail": "Request validation failed",
                "errors": errors,
            },
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(
        request: Request, exc: HTTPException
    ) -> JSONResponse:
        logger.warning(
            "HTTP exception",
            extra={
                "url": str(request.url),
                "status": exc.status_code,
                "detail": exc.detail,
            },
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={"status": "error", "detail": exc.detail},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.error(
            "Unhandled exception",
            extra={"url": str(request.url), "error": str(exc)},
            exc_info=exc,
        )
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "detail": "Internal server error",
                "message": (
                    str(exc) if settings.is_development else "An error occurred."
                ),
            },
        )

    logger.info("FastAPI application configured", extra={"routes": len(app.routes)})
    return app


# ─────────────────────────────────────────────────────────────────────────────
# ASGI entry-point
# ─────────────────────────────────────────────────────────────────────────────
app = create_app()


if __name__ == "__main__":
    import uvicorn
    import os

    port = int(os.environ.get("PORT", 8080))
    workers = 1 if settings.is_development else 2

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        workers=workers,
        reload=settings.is_development,
        log_level=settings.LOG_LEVEL.lower(),
    )
