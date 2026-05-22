from __future__ import annotations

import time
from typing import Any, Optional

from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

from app.config import settings
from app.database.pg_document_db import PostgresDocumentDatabase, PostgresDocumentStore
from app.utils.logger import get_logger

logger = get_logger(__name__)

_store: Optional[Any] = None
_db: Optional[Any] = None

# Retry configuration for transient Atlas timeouts
_MAX_RETRIES   = 3
_RETRY_BACKOFF = [1, 2, 4]   # seconds between attempts


def _resolve_dsn() -> str:
    return (
        settings.SUPABASE_DB_URL
        or settings.POSTGRES_DSN
        or settings.MONGO_URI
    )


def _is_mongo_dsn(dsn: str) -> bool:
    return dsn.startswith("mongodb://") or dsn.startswith("mongodb+srv://")


def _build_mongo_client(dsn: str) -> MongoClient:
    """
    Create a MongoClient with a short server-selection timeout so that
    transient Atlas DNS issues fail fast rather than blocking the worker.
    """
    return MongoClient(
        dsn,
        serverSelectionTimeoutMS=5_000,   # 5 s – fail fast
        connectTimeoutMS=10_000,
        socketTimeoutMS=30_000,
        retryWrites=True,
    )


def get_client() -> Any:
    global _store
    if _store is not None:
        return _store

    dsn = _resolve_dsn()

    if not _is_mongo_dsn(dsn):
        # Postgres / Supabase – no retry needed (TCP-local)
        _store = PostgresDocumentStore(dsn)
        _store.ensure_schema()
        logger.info("Postgres document store ready", extra={"dsn_prefix": dsn[:24]})
        return _store

    # ── MongoDB with exponential-backoff retry ──
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            client = _build_mongo_client(dsn)
            # Force a ping to verify the connection is live
            client.admin.command("ping")
            _store = client
            logger.info(
                "MongoDB client ready",
                extra={"dsn_prefix": dsn[:24], "attempt": attempt},
            )
            return _store
        except (ConnectionFailure, ServerSelectionTimeoutError) as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                wait = _RETRY_BACKOFF[attempt - 1]
                logger.warning(
                    "MongoDB connection failed – retrying",
                    extra={"attempt": attempt, "wait_s": wait, "error": str(exc)},
                )
                time.sleep(wait)
            else:
                logger.error(
                    "MongoDB connection failed after all retries",
                    extra={"attempts": _MAX_RETRIES, "error": str(exc)},
                )

    # All retries exhausted – raise so the startup health-check catches it
    raise ConnectionFailure(
        f"Could not connect to MongoDB after {_MAX_RETRIES} attempts: {last_exc}"
    )


def get_db(db_name: Optional[str] = None) -> Any:
    global _db
    if _db is None:
        client = get_client()
        if isinstance(client, MongoClient):
            _db = client[db_name or settings.DB_NAME]
        else:
            _db = PostgresDocumentDatabase(client)
    return _db


def close_client() -> None:
    global _store, _db
    if hasattr(_store, "close"):
        _store.close()
    _store = None
    _db = None


def health_check() -> dict[str, object]:
    start = time.monotonic()
    try:
        client = get_client()
        if isinstance(client, MongoClient):
            client.admin.command("ping")
            latency_ms = round((time.monotonic() - start) * 1000, 1)
            return {
                "status": "ok",
                "database": "mongodb",
                "mode": "native",
                "latency_ms": latency_ms,
            }
        client.ensure_schema()
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        return {
            "status": "ok",
            "database": "postgres",
            "mode": "document_compat",
            "latency_ms": latency_ms,
        }
    except Exception as exc:
        logger.error("DB health check failed", extra={"error": str(exc)})
        # Reset singleton so the next real request triggers a fresh retry
        global _store, _db
        _store = None
        _db = None
        return {"status": "error", "detail": str(exc)}
