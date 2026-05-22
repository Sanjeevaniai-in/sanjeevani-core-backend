from __future__ import annotations

from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_ENV: str = "development"
    APP_PORT: int = 8001
    APP_WORKERS: int = 2

    MONGO_URI: str = ""
    POSTGRES_DSN: str = ""
    SUPABASE_DB_URL: str = ""
    SUPABASE_URL: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""
    MONGO_DB_NAME: str = "sanjeevani_auth"

    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRY_HOURS: int = 24

    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    PUBLIC_URL: str = ""
    FRONTEND_URL: str = "http://localhost:5173"
    
    # Specific Frontend URLs (Dynamic Redirection)
    FRONTEND_DASHBOARD: str = "http://localhost:5173"
    FRONTEND_STOREFRONT: str = "http://localhost:5174"
    FRONTEND_OPS_HUB: str = "http://localhost:5175"
    
    CORS_ORIGINS: str = "*"  # Default to '*' for deployment ease; restrict in production
    SUPPORTED_APPS: str = "dashboard,storefront,ops_hub"
    DEFAULT_SUBSCRIPTION_PLAN: str = "free"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def cors_origins_list(self) -> List[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def supported_apps_list(self) -> List[str]:
        return [app.strip().lower() for app in self.SUPPORTED_APPS.split(",") if app.strip()]

    @property
    def is_development(self) -> bool:
        return self.APP_ENV == "development"


settings = Settings()
