"""
app/config.py
─────────────────────────────────────────────────────────────────────────────
Centralised application settings loaded from environment variables / .env file.
Uses pydantic-settings (v2) so every field is type-validated at startup.

Usage
-----
    from app.config import settings

    uri  = settings.MONGO_URI
    db   = settings.DB_NAME
    ...

Environment variables (can also be placed in a `.env` file at the project root)
────────────────────────────────────────────────────────────────────────────────
  MONGO_URI   – MongoDB connection string (required)
  DB_NAME     – Target database name     (default: pharmacy_management)
  API_PREFIX  – Global API route prefix  (default: /api/v1)
  LOG_LEVEL   – Python log level string  (default: INFO)
  ENV         – Deployment environment   (default: development)
  GROQ_API_KEY – Groq LLM API key       (optional, for AI modules)
  SECRET_KEY  – App secret / JWT signing (default: changeme; override in prod)
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal

from pydantic import Field, MongoDsn, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ──────────────────────────────────────────────────────────────────────────────
# Settings class
# ──────────────────────────────────────────────────────────────────────────────


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables and an optional
    `.env` file.  All fields are validated by Pydantic before first use.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,  # env-var names are matched as-is
        extra="ignore",  # silently ignore unknown env vars
    )

    # ── Database ──────────────────────────────────────────────────────────────
    MONGO_URI: str = Field(
        default="mongodb://localhost:27017",
        description="MongoDB connection URI (supports srv:// as well).",
    )
    POSTGRES_DSN: str = Field(
        default="",
        description="Direct PostgreSQL DSN for Supabase/Postgres connectivity.",
    )
    SUPABASE_DB_URL: str = Field(
        default="",
        description="Preferred Supabase PostgreSQL connection string.",
    )
    SUPABASE_URL: str = Field(
        default="",
        description="Supabase project URL for future service-role usage.",
    )
    SUPABASE_SERVICE_ROLE_KEY: str = Field(
        default="",
        description="Supabase service role key for backend-only operations.",
    )
    DB_NAME: str = Field(
        default="sanjeevani_rx_db",
        description="MongoDB database name.",
    )
    DEFAULT_PHARMACY_ID: str = Field(
        default="",
        description="Fallback pharmacy/merchant id for temporary single-pharmacy routing.",
    )

    # ── API ───────────────────────────────────────────────────────────────────
    API_PREFIX: str = Field(
        default="/api/v1",
        description="Global prefix applied to all API routes.",
    )
    APP_TITLE: str = Field(default="SanjeevaniRxAI API")
    APP_VERSION: str = Field(default="1.0.0")

    # ── Logging ───────────────────────────────────────────────────────────────
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        description="Python standard logging level (uppercased).",
    )

    # ── Deployment ────────────────────────────────────────────────────────────
    ENV: Literal["development", "staging", "production"] = Field(
        default="development",
        description="Deployment environment tag.",
    )

    FRONTEND_URL: str = Field(
        default="http://localhost:5173",
        description="Frontend base URL for OAuth redirection.",
    )

    # ── Security ──────────────────────────────────────────────────────────────
    SECRET_KEY: str = Field(
        default="changeme-please-override-in-production",
        description="Secret key for signing tokens.  Must be overridden in prod.",
    )

    # Google OAuth is now handled by Sanjeevani Auth service.
    # JWT authentication below is used to verify tokens from that service.

    # ── JWT ───────────────────────────────────────────────────────────────────
    JWT_SECRET: str = Field(
        default="changeme-jwt-secret",
        description="Secret for JWT token signing. Override in production.",
    )
    JWT_ALGORITHM: str = Field(
        default="HS256",
        description="JWT signing algorithm.",
    )
    JWT_EXPIRATION_HOURS: int = Field(
        default=24,
        description="JWT token expiration time in hours.",
    )

    # ── LLM / AI ──────────────────────────────────────────────────────────────
    GROQ_API_KEY: str = Field(
        default="",
        description="Groq LLM API key.  Leave empty to disable AI features.",
    )
    GROQ_MODEL: str = Field(
        default="llama-3.1-8b-instant",
        description="Default Groq model to use for inference.",
    )

    ANTHROPIC_API_KEY: str = Field(
        default="",
        description="Anthropic Claude API key.",
    )
    ANTHROPIC_MODEL: str = Field(
        default="claude-3-5-sonnet-20240620",
        description="Default Anthropic model to use for complex tasks.",
    )

    # ── Langfuse (LLM Observability) ──────────────────────────────────────────
    LANGFUSE_SECRET_KEY: str = Field(
        default="",
        description="Langfuse secret key for LLM tracking and observability.",
    )
    LANGFUSE_PUBLIC_KEY: str = Field(
        default="",
        description="Langfuse public key for LLM tracking and observability.",
    )
    LANGFUSE_HOST: str = Field(
        default="https://cloud.langfuse.com",
        description="Langfuse host URL for tracking.",
    )

    # ── Vapi Voice ─────────────────────────────────────────────────────────────
    VAPI_API_KEY: str = Field(
        default="",
        description="Vapi API key for voice calling features.",
    )
    VAPI_PHONE_NUMBER_ID: str = Field(
        default="",
        description="Vapi phone number ID for inbound/outbound calls.",
    )
    VAPI_WEBHOOK_SECRET: str = Field(
        default="",
        description="Optional secret to verify Vapi webhook calls.",
    )
    SERVER_URL: str = Field(
        default="http://localhost:8000",
        description="Public base URL of this server (used for Vapi webhook callbacks). Use ngrok URL in dev.",
    )

    # ── Rate Limiting ─────────────────────────────────────────────────────────
    RATE_LIMIT_PER_MINUTE: int = Field(
        default=60,
        description="Max requests per minute per client IP.",
    )

    # ── CORS ─────────────────────────────────────────────────────────────────
    CORS_ORIGINS: list[str] = Field(
        default=["*"],
        description="Allowed CORS origins.  Restrict in production.",
    )

    # ──────────────────────────────────────────────────────────────────────────
    # Validators
    # ──────────────────────────────────────────────────────────────────────────

    @field_validator("SECRET_KEY")
    @classmethod
    def warn_default_secret(cls, v: str) -> str:
        if v == "changeme-please-override-in-production":
            import warnings

            warnings.warn(
                "SECRET_KEY is using the insecure default value. "
                "Set a strong SECRET_KEY env variable before deploying.",
                stacklevel=2,
            )
        return v

    @field_validator("MONGO_URI", mode="before")
    @classmethod
    def assemble_db_url(cls, v: str, info: dict) -> str:
        """
        Support both MONGO_URI and MONGODB_URL environment variables.
        If MONGO_URI is default/empty but MONGODB_URL is set, use the latter.
        """
        if not v or v == "mongodb://localhost:27017":
            # Check for MONGODB_URL in environment or passed data
            alt = os.getenv("MONGODB_URL")
            if alt:
                return alt
        return v

    @field_validator("LOG_LEVEL", mode="before")
    @classmethod
    def upper_log_level(cls, v: str) -> str:
        return str(v).upper()

    @model_validator(mode="after")
    def validate_production_settings(self) -> "Settings":
        """Ensure deployment-critical settings are valid after all fields load."""
        if self.ENV == "production" and (
            "localhost" in self.MONGO_URI or "127.0.0.1" in self.MONGO_URI
        ):
            raise ValueError(
                f"MONGO_URI cannot be {self.MONGO_URI} in production. "
                "Set a valid MONGO_URI or MONGODB_URL in Render environment variables."
            )
        return self

    # ──────────────────────────────────────────────────────────────────────────
    # Convenience helpers
    # ──────────────────────────────────────────────────────────────────────────

    @property
    def is_production(self) -> bool:
        return self.ENV == "production"

    @property
    def is_development(self) -> bool:
        return self.ENV == "development"

    @property
    def debug_mode(self) -> bool:
        return self.LOG_LEVEL == "DEBUG"


# ──────────────────────────────────────────────────────────────────────────────
# Singleton accessor (cached after first call)
# ──────────────────────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return the cached Settings singleton.

    lru_cache ensures the .env file is parsed exactly once per process,
    which is important for performance and test isolation (call
    ``get_settings.cache_clear()`` in tests to reload settings).
    """
    return Settings()


# Module-level convenience alias used throughout the codebase:
#   from app.config import settings
settings: Settings = get_settings()
