"""
app/utils/helpers.py
General utility helpers for SanjeevaniRxAI.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict


def utcnow() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(tz=timezone.utc)


def normalize_record(doc: Dict[str, Any]) -> Dict[str, Any]:
    """
    Standardizes MongoDB document keys for the frontend.
    e.g. 'Order ID' -> 'order_id', 'Patient Name' -> 'customer_name'
    """
    mapping = {
        "Order ID": "order_id",
        "Patient Name": "customer_name",
        "Medicine Name": "product_name",
        "Total Amount": "total_amount",
        "Order Status": "order_status",
        "Order Date": "order_date",
        "Quantity": "quantity",
    }
    normalized = {}
    for k, v in doc.items():
        new_key = mapping.get(k, k.lower().replace(" ", "_"))
        normalized[new_key] = v
    return normalized


def normalize_list(items: list) -> list:
    """Apply normalization to a list of records."""
    return [normalize_record(item) if isinstance(item, dict) else item for item in items]


def build_pagination_response(
    data: list,
    total: int,
    page: int,
    page_size: int,
    status: str = "ok",
) -> Dict[str, Any]:
    """Construct a standard paginated JSON response body."""
    # Automated normalization for all paginated responses
    normalized_data = normalize_list(data)
    return {
        "status": status,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": -(-total // page_size),  # ceiling division
        "data": normalized_data,
    }
