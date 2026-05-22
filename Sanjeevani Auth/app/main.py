from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import auth, admin
from app.core.config import settings
from app.db.mongo import close_client, create_indexes, get_client


if settings.is_development:
    import os
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_client()
    await create_indexes()
    yield
    await close_client()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Sanjeevani Auth Service",
        version="1.1.0",
        description=(
            "Central authentication service for Sanjeevani apps. "
            "Supports email/password login, Google sign-in, app-aware memberships, and shared subscriptions."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    from starlette.middleware.sessions import SessionMiddleware
    app.add_middleware(SessionMiddleware, secret_key=settings.JWT_SECRET)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth.router)
    app.include_router(admin.router)

    @app.get("/health", tags=["Health"], summary="Health check")
    def health():
        return JSONResponse(
            status_code=200,
            content={
                "status": "healthy",
                "service": "Sanjeevani Auth",
                "version": "1.1.0",
                "supported_apps": settings.supported_apps_list,
            },
        )

    @app.get("/", include_in_schema=False)
    def root():
        return {
            "service": "Sanjeevani Auth Service",
            "docs": "/docs",
            "supported_apps": settings.supported_apps_list,
        }

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.APP_PORT,
        reload=settings.is_development,
    )
