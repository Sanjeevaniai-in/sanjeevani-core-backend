from __future__ import annotations

import json
import re
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any, Optional

import psycopg
from psycopg.rows import dict_row


def _encode_special(value: Any) -> Any:
    if isinstance(value, datetime):
        return {"__type__": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"__type__": "date", "value": value.isoformat()}
    if isinstance(value, dict):
        return {key: _encode_special(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_encode_special(item) for item in value]
    return value


def _decode_special(value: Any) -> Any:
    if isinstance(value, dict):
        value_type = value.get("__type__")
        if value_type == "datetime":
            return datetime.fromisoformat(value["value"])
        if value_type == "date":
            return date.fromisoformat(value["value"])
        return {key: _decode_special(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_decode_special(item) for item in value]
    return value


def _get_value(document: dict[str, Any], path: str, default: Any = None) -> Any:
    current: Any = document
    for part in path.split("."):
        if not part:
            continue
        if isinstance(current, dict):
            current = current.get(part, default)
        else:
            return default
    return current


def _set_value(document: dict[str, Any], path: str, value: Any) -> None:
    current = document
    parts = [part for part in path.split(".") if part]
    for part in parts[:-1]:
        next_value = current.get(part)
        if not isinstance(next_value, dict):
            next_value = {}
            current[part] = next_value
        current = next_value
    current[parts[-1]] = value


def _match_value(field_value: Any, condition: Any) -> bool:
    if isinstance(condition, dict):
        regex_options = str(condition.get("$options", ""))
        for operator, expected in condition.items():
            if operator == "$options":
                continue
            if operator == "$regex":
                flags = re.IGNORECASE if "i" in regex_options.lower() else 0
                if not re.search(str(expected), str(field_value or ""), flags):
                    return False
            elif operator == "$ne":
                if field_value == expected:
                    return False
            elif operator == "$exists":
                if (field_value is not None) != bool(expected):
                    return False
            else:
                if field_value != expected:
                    return False
        return True
    return field_value == condition


def _match_query(document: dict[str, Any], query: Optional[dict[str, Any]]) -> bool:
    if not query:
        return True
    for key, value in query.items():
        if key == "$or":
            return any(_match_query(document, clause) for clause in value)
        if key == "$and":
            return all(_match_query(document, clause) for clause in value)
        if not _match_value(_get_value(document, key), value):
            return False
    return True


def _apply_update(document: dict[str, Any], update: dict[str, Any], *, is_insert: bool) -> dict[str, Any]:
    next_doc = dict(document)
    for operator, payload in update.items():
        if operator == "$set":
            for path, value in payload.items():
                _set_value(next_doc, path, value)
        elif operator == "$setOnInsert" and is_insert:
            for path, value in payload.items():
                _set_value(next_doc, path, value)
    return next_doc


class AsyncDocumentCursor:
    def __init__(self, documents: list[dict[str, Any]]):
        self._documents = documents

    def sort(self, field: str, direction: int):
        self._documents.sort(key=lambda doc: _get_value(doc, field), reverse=direction == -1)
        return self

    def limit(self, count: int):
        self._documents = self._documents[:count]
        return self

    async def to_list(self, length: int = 100):
        return self._documents[:length]


class AsyncDocumentCollection:
    def __init__(self, store: "PostgresDocumentStore", name: str):
        self._store = store
        self._name = name

    async def create_index(self, *args, **kwargs):
        return None

    def find(self, query: Optional[dict[str, Any]] = None, projection: Optional[dict[str, int]] = None) -> AsyncDocumentCursor:
        docs = [document for document in self._store.load_collection(self._name) if _match_query(document, query)]
        if projection:
            next_docs = []
            for document in docs:
                projected = dict(document)
                for field, include in projection.items():
                    if not include:
                        projected.pop(field, None)
                next_docs.append(projected)
            docs = next_docs
        return AsyncDocumentCursor(docs)

    async def find_one(self, query: Optional[dict[str, Any]] = None, projection: Optional[dict[str, int]] = None):
        docs = await self.find(query, projection).to_list(length=1)
        return docs[0] if docs else None

    async def insert_one(self, document: dict[str, Any]):
        payload = dict(document)
        payload.setdefault("_id", str(uuid.uuid4()))
        self._store.save_document(self._name, payload["_id"], payload)
        return type("InsertOneResult", (), {"inserted_id": payload["_id"]})()

    async def update_one(self, query: dict[str, Any], update: dict[str, Any], upsert: bool = False):
        for document in self._store.load_collection(self._name):
            if _match_query(document, query):
                self._store.save_document(self._name, document["_id"], _apply_update(document, update, is_insert=False))
                return None
        if upsert:
            seed = {"_id": str(uuid.uuid4())}
            for key, value in query.items():
                if not key.startswith("$") and not isinstance(value, dict):
                    seed[key] = value
            next_doc = _apply_update(seed, update, is_insert=True)
            self._store.save_document(self._name, next_doc["_id"], next_doc)
        return None

    async def update_many(self, query: dict[str, Any], update: dict[str, Any]):
        for document in self._store.load_collection(self._name):
            if _match_query(document, query):
                self._store.save_document(self._name, document["_id"], _apply_update(document, update, is_insert=False))
        return None


class AsyncDocumentDatabase:
    def __init__(self, store: "PostgresDocumentStore"):
        self._store = store

    def __getitem__(self, collection_name: str) -> AsyncDocumentCollection:
        return AsyncDocumentCollection(self._store, collection_name)

    def __getattr__(self, collection_name: str) -> AsyncDocumentCollection:
        return self[collection_name]


class PostgresDocumentStore:
    def __init__(self, dsn: str):
        self._dsn = dsn
        self._schema_ready = False

    @contextmanager
    def connection(self):
        conn = psycopg.connect(self._dsn, row_factory=dict_row)
        try:
            yield conn
        finally:
            conn.close()

    def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    create table if not exists app_documents (
                        collection_name text not null,
                        doc_id text not null,
                        merchant_id text,
                        payload jsonb not null,
                        created_at timestamptz not null default now(),
                        updated_at timestamptz not null default now(),
                        primary key (collection_name, doc_id)
                    )
                    """
                )
                cur.execute("create index if not exists app_documents_collection_idx on app_documents (collection_name)")
                cur.execute("create index if not exists app_documents_merchant_idx on app_documents (merchant_id)")
            conn.commit()
        self._schema_ready = True

    def load_collection(self, collection_name: str) -> list[dict[str, Any]]:
        self.ensure_schema()
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("select payload from app_documents where collection_name = %s order by updated_at asc", (collection_name,))
                rows = cur.fetchall()
        return [_decode_special(row["payload"]) for row in rows]

    def save_document(self, collection_name: str, doc_id: str, payload: dict[str, Any]) -> None:
        self.ensure_schema()
        merchant_id = payload.get("merchant_id") or payload.get("pharmacy_id") or payload.get("tenant_id")
        serialized = json.dumps(_encode_special(payload))
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into app_documents (collection_name, doc_id, merchant_id, payload)
                    values (%s, %s, %s, %s::jsonb)
                    on conflict (collection_name, doc_id)
                    do update set merchant_id = excluded.merchant_id, payload = excluded.payload, updated_at = now()
                    """,
                    (collection_name, doc_id, merchant_id, serialized),
                )
            conn.commit()

