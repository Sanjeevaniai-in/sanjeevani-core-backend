from __future__ import annotations
from typing import Any, Optional
from motor.motor_asyncio import AsyncIOMotorClient
from app.core.config import settings
from app.db.pg_document_db import AsyncDocumentDatabase, PostgresDocumentStore

_store: Optional[Any] = None
_db: Optional[Any] = None


def _resolve_dsn() -> str:
    # Prioritize MONGO_URI if we are shifting to local MongoDB
    return settings.MONGO_URI or settings.SUPABASE_DB_URL or settings.POSTGRES_DSN


def _is_mongo_dsn(dsn: str) -> bool:
    return dsn.startswith("mongodb://") or dsn.startswith("mongodb+srv://")


def get_client() -> Any:
    global _store
    if _store is None:
        dsn = _resolve_dsn()
        if _is_mongo_dsn(dsn):
            _store = AsyncIOMotorClient(dsn)
        else:
            _store = PostgresDocumentStore(dsn)
            _store.ensure_schema()
    return _store


def get_db() -> Any:
    global _db
    if _db is None:
        client = get_client()
        if isinstance(client, AsyncIOMotorClient):
            try:
                _db = client.get_default_database()
            except Exception:
                _db = client[settings.MONGO_DB_NAME]
        else:
            _db = AsyncDocumentDatabase(client)
    return _db


async def close_client():
    global _store, _db
    if _store is not None:
        if hasattr(_store, "close"):
            _store.close()
    _store = None
    _db = None


async def create_indexes():
    # Only need to ensure schema for Postgres mode; MongoDB handles it dynamically
    client = get_client()
    if not isinstance(client, AsyncIOMotorClient):
        client.ensure_schema()

