from __future__ import annotations

import json
import re
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any, Iterable, Iterator, Optional

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


def _split_path(path: str) -> list[str]:
    return [part for part in path.split(".") if part]


def _get_value(document: dict[str, Any], path: str, default: Any = None) -> Any:
    current: Any = document
    for part in _split_path(path):
        if isinstance(current, dict):
            current = current.get(part, default)
        elif isinstance(current, list):
            collected = []
            for item in current:
                if isinstance(item, dict) and part in item:
                    collected.append(item.get(part))
            current = collected if collected else default
        else:
            return default
    return current


def _set_value(document: dict[str, Any], path: str, value: Any) -> None:
    current = document
    parts = _split_path(path)
    for part in parts[:-1]:
        next_value = current.get(part)
        if not isinstance(next_value, dict):
            next_value = {}
            current[part] = next_value
        current = next_value
    current[parts[-1]] = value


def _unset_value(document: dict[str, Any], path: str) -> None:
    current = document
    parts = _split_path(path)
    for part in parts[:-1]:
        next_value = current.get(part)
        if not isinstance(next_value, dict):
            return
        current = next_value
    current.pop(parts[-1], None)


def _coerce_numeric(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _match_operator(field_value: Any, operator: str, expected: Any) -> bool:
    if operator == "$exists":
        exists = field_value is not None
        return exists == bool(expected)
    if operator == "$ne":
        return field_value != expected
    if operator == "$in":
        return field_value in expected
    if operator == "$nin":
        return field_value not in expected
    if operator == "$regex":
        pattern = str(expected)
        flags = 0
        return bool(re.search(pattern, str(field_value or ""), flags))
    if operator == "$gte":
        return field_value is not None and field_value >= expected
    if operator == "$lte":
        return field_value is not None and field_value <= expected
    if operator == "$gt":
        return field_value is not None and field_value > expected
    if operator == "$lt":
        return field_value is not None and field_value < expected
    return False


def _match_value(field_value: Any, condition: Any) -> bool:
    if isinstance(field_value, list) and not isinstance(condition, dict):
        return condition in field_value

    if isinstance(condition, dict):
        regex_options = str(condition.get("$options", ""))
        for operator, expected in condition.items():
            if operator == "$options":
                continue
            if operator == "$regex":
                flags = re.IGNORECASE if "i" in regex_options.lower() else 0
                if not re.search(str(expected), str(field_value or ""), flags):
                    return False
                continue
            if not _match_operator(field_value, operator, expected):
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
        if key == "$nor":
            return not any(_match_query(document, clause) for clause in value)

        field_value = _get_value(document, key)
        if not _match_value(field_value, value):
            return False
    return True


def _apply_projection(document: dict[str, Any], projection: Optional[dict[str, int]]) -> dict[str, Any]:
    if not projection:
        return dict(document)

    include_fields = [key for key, value in projection.items() if value]
    exclude_fields = [key for key, value in projection.items() if not value]

    if include_fields and not exclude_fields:
        projected = {}
        for field in include_fields:
            value = _get_value(document, field)
            if value is not None:
                _set_value(projected, field, value)
        if projection.get("_id", 1):
            projected["_id"] = document.get("_id")
        return projected

    projected = dict(document)
    for field in exclude_fields:
        if field == "_id":
            projected.pop("_id", None)
        else:
            _unset_value(projected, field)
    return projected


def _evaluate_expression(expression: Any, document: dict[str, Any]) -> Any:
    if isinstance(expression, str) and expression.startswith("$"):
        return _get_value(document, expression[1:])
    if not isinstance(expression, dict):
        return expression

    if "$sum" in expression:
        return _evaluate_expression(expression["$sum"], document)
    if "$avg" in expression:
        return _evaluate_expression(expression["$avg"], document)
    if "$toDouble" in expression:
        return _coerce_numeric(_evaluate_expression(expression["$toDouble"], document))
    if "$ifNull" in expression:
        left, default = expression["$ifNull"]
        value = _evaluate_expression(left, document)
        return default if value is None else value
    if "$cond" in expression:
        condition, if_true, if_false = expression["$cond"]
        return _evaluate_expression(if_true, document) if _evaluate_condition(condition, document) else _evaluate_expression(if_false, document)
    if "$year" in expression:
        value = _evaluate_expression(expression["$year"], document)
        return value.year if isinstance(value, (datetime, date)) else None
    if "$month" in expression:
        value = _evaluate_expression(expression["$month"], document)
        return value.month if isinstance(value, (datetime, date)) else None
    if "$dayOfMonth" in expression:
        value = _evaluate_expression(expression["$dayOfMonth"], document)
        return value.day if isinstance(value, (datetime, date)) else None
    if "$eq" in expression:
        left, right = expression["$eq"]
        return _evaluate_expression(left, document) == _evaluate_expression(right, document)
    if "$gte" in expression:
        left, right = expression["$gte"]
        return _evaluate_expression(left, document) >= _evaluate_expression(right, document)
    if "$and" in expression:
        return all(_evaluate_condition(item, document) for item in expression["$and"])
    return {key: _evaluate_expression(value, document) for key, value in expression.items()}


def _evaluate_condition(condition: Any, document: dict[str, Any]) -> bool:
    result = _evaluate_expression(condition, document)
    return bool(result)


def _apply_update(document: dict[str, Any], update: dict[str, Any], *, is_insert: bool) -> dict[str, Any]:
    next_doc = dict(document)
    for operator, payload in update.items():
        if operator == "$set":
            for path, value in payload.items():
                _set_value(next_doc, path, value)
        elif operator == "$setOnInsert" and is_insert:
            for path, value in payload.items():
                _set_value(next_doc, path, value)
        elif operator == "$inc":
            for path, value in payload.items():
                current = _coerce_numeric(_get_value(next_doc, path, 0))
                _set_value(next_doc, path, current + _coerce_numeric(value))
        elif operator == "$push":
            for path, value in payload.items():
                current = _get_value(next_doc, path, [])
                if not isinstance(current, list):
                    current = []
                current = list(current)
                current.append(value)
                _set_value(next_doc, path, current)
        elif operator == "$addToSet":
            for path, value in payload.items():
                current = _get_value(next_doc, path, [])
                if not isinstance(current, list):
                    current = []
                current = list(current)
                if value not in current:
                    current.append(value)
                _set_value(next_doc, path, current)
        elif operator == "$unset":
            for path in payload.keys():
                _unset_value(next_doc, path)
    return next_doc


class DocumentCursor:
    def __init__(self, documents: list[dict[str, Any]]):
        self._documents = documents
        self._skip = 0
        self._limit: Optional[int] = None

    def sort(self, field: Any, direction: Optional[int] = None):
        if isinstance(field, list):
            for field_name, sort_direction in reversed(field):
                reverse = sort_direction == -1
                self._documents.sort(key=lambda doc: _get_value(doc, field_name), reverse=reverse)
            return self

        reverse = direction == -1
        self._documents.sort(key=lambda doc: _get_value(doc, field), reverse=reverse)
        return self

    def skip(self, count: int):
        self._skip = max(count, 0)
        return self

    def limit(self, count: int):
        self._limit = max(count, 0)
        return self

    def _slice(self) -> list[dict[str, Any]]:
        docs = self._documents[self._skip :]
        if self._limit is not None:
            docs = docs[: self._limit]
        return docs

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self._slice())


class DocumentCollection:
    def __init__(self, store: "PostgresDocumentStore", name: str):
        self._store = store
        self._name = name

    def create_index(self, *args, **kwargs):
        return None

    def find(self, query: Optional[dict[str, Any]] = None, projection: Optional[dict[str, int]] = None) -> DocumentCursor:
        documents = [
            _apply_projection(document, projection)
            for document in self._store.load_collection(self._name)
            if _match_query(document, query)
        ]
        return DocumentCursor(documents)

    def find_one(
        self,
        query: Optional[dict[str, Any]] = None,
        projection: Optional[dict[str, int]] = None,
        sort: Optional[list[tuple[str, int]]] = None,
    ) -> Optional[dict[str, Any]]:
        cursor = self.find(query, projection)
        if sort:
            cursor.sort(sort)
        docs = list(cursor.limit(1))
        return docs[0] if docs else None

    def count_documents(self, query: Optional[dict[str, Any]] = None) -> int:
        return sum(1 for document in self._store.load_collection(self._name) if _match_query(document, query))

    def insert_one(self, document: dict[str, Any]):
        payload = dict(document)
        payload.setdefault("_id", str(uuid.uuid4()))
        self._store.save_document(self._name, payload["_id"], payload)

        class Result:
            inserted_id = payload["_id"]

        return Result()

    def insert_many(self, documents: Iterable[dict[str, Any]]):
        inserted_ids: list[str] = []
        for document in documents:
            payload = dict(document)
            payload.setdefault("_id", str(uuid.uuid4()))
            self._store.save_document(self._name, payload["_id"], payload)
            inserted_ids.append(payload["_id"])

        class Result:
            inserted_ids = inserted_ids

        return Result()

    def update_one(self, query: dict[str, Any], update: dict[str, Any], upsert: bool = False):
        documents = self._store.load_collection(self._name)
        for document in documents:
            if _match_query(document, query):
                next_doc = _apply_update(document, update, is_insert=False)
                self._store.save_document(self._name, next_doc["_id"], next_doc)
                return None
        if upsert:
            seed = {"_id": str(uuid.uuid4())}
            for key, value in query.items():
                if not key.startswith("$") and not isinstance(value, dict):
                    seed[key] = value
            next_doc = _apply_update(seed, update, is_insert=True)
            self._store.save_document(self._name, next_doc["_id"], next_doc)
        return None

    def update_many(self, query: dict[str, Any], update: dict[str, Any]):
        for document in self._store.load_collection(self._name):
            if _match_query(document, query):
                next_doc = _apply_update(document, update, is_insert=False)
                self._store.save_document(self._name, next_doc["_id"], next_doc)
        return None

    def find_one_and_update(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        projection: Optional[dict[str, int]] = None,
        return_document: Any = None,
        upsert: bool = False,
        array_filters: Optional[list[dict[str, Any]]] = None,
    ) -> Optional[dict[str, Any]]:
        documents = self._store.load_collection(self._name)
        for document in documents:
            if _match_query(document, query):
                next_doc = _apply_update(document, update, is_insert=False)
                self._store.save_document(self._name, next_doc["_id"], next_doc)
                return _apply_projection(next_doc, projection)
        if not upsert:
            return None

        seed = {"_id": str(uuid.uuid4())}
        for key, value in query.items():
            if not key.startswith("$") and not isinstance(value, dict):
                seed[key] = value
        next_doc = _apply_update(seed, update, is_insert=True)
        self._store.save_document(self._name, next_doc["_id"], next_doc)
        return _apply_projection(next_doc, projection)

    def aggregate(self, pipeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
        documents = self._store.load_collection(self._name)
        for stage in pipeline:
            if "$match" in stage:
                documents = [document for document in documents if _match_query(document, stage["$match"])]
            elif "$group" in stage:
                group_spec = stage["$group"]
                group_key_expr = group_spec.get("_id")
                grouped: dict[str, dict[str, Any]] = {}

                for document in documents:
                    group_key = _evaluate_expression(group_key_expr, document)
                    group_key_token = json.dumps(_encode_special(group_key), sort_keys=True)
                    bucket = grouped.setdefault(group_key_token, {"_id": group_key})
                    for field, accumulator in group_spec.items():
                        if field == "_id":
                            continue
                        if "$sum" in accumulator:
                            bucket[field] = bucket.get(field, 0) + _coerce_numeric(_evaluate_expression(accumulator["$sum"], document))
                        elif "$avg" in accumulator:
                            values = bucket.setdefault(f"__avg_{field}", [])
                            values.append(_coerce_numeric(_evaluate_expression(accumulator["$avg"], document)))

                grouped_docs = list(grouped.values())
                for group_doc in grouped_docs:
                    avg_keys = [key for key in list(group_doc.keys()) if key.startswith("__avg_")]
                    for avg_key in avg_keys:
                        field = avg_key.replace("__avg_", "", 1)
                        values = group_doc.pop(avg_key, [])
                        group_doc[field] = sum(values) / len(values) if values else 0
                documents = grouped_docs
            elif "$sort" in stage:
                sort_spec = stage["$sort"]
                for field_name, direction in reversed(list(sort_spec.items())):
                    documents.sort(key=lambda doc: _get_value(doc, field_name), reverse=direction == -1)
            elif "$limit" in stage:
                documents = documents[: int(stage["$limit"])]
            elif "$bucket" in stage:
                bucket_spec = stage["$bucket"]
                group_by = bucket_spec["groupBy"]
                boundaries = bucket_spec["boundaries"]
                default_label = bucket_spec.get("default")
                output = bucket_spec.get("output", {"count": {"$sum": 1}})
                buckets: list[dict[str, Any]] = []
                for idx in range(len(boundaries) - 1):
                    buckets.append({"_id": boundaries[idx], "count": 0})
                if default_label is not None:
                    buckets.append({"_id": default_label, "count": 0})

                for document in documents:
                    value = _evaluate_expression(group_by, document)
                    target = None
                    for idx in range(len(boundaries) - 1):
                        lower = boundaries[idx]
                        upper = boundaries[idx + 1]
                        if value is not None and lower <= value < upper:
                            target = buckets[idx]
                            break
                    if target is None and default_label is not None:
                        target = buckets[-1]
                    if target is not None:
                        target["count"] += 1
                documents = buckets
        return documents


class PostgresDocumentDatabase:
    def __init__(self, store: "PostgresDocumentStore"):
        self._store = store

    def __getitem__(self, collection_name: str) -> DocumentCollection:
        return DocumentCollection(self._store, collection_name)

    def __getattr__(self, collection_name: str) -> DocumentCollection:
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
                cur.execute(
                    "create index if not exists app_documents_collection_idx on app_documents (collection_name)"
                )
                cur.execute(
                    "create index if not exists app_documents_merchant_idx on app_documents (merchant_id)"
                )
            conn.commit()
        self._schema_ready = True

    def load_collection(self, collection_name: str) -> list[dict[str, Any]]:
        self.ensure_schema()
        with self.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select payload
                    from app_documents
                    where collection_name = %s
                    order by updated_at asc
                    """,
                    (collection_name,),
                )
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
                    do update set
                        merchant_id = excluded.merchant_id,
                        payload = excluded.payload,
                        updated_at = now()
                    """,
                    (collection_name, doc_id, merchant_id, serialized),
                )
            conn.commit()

