"""
app/api/customers.py  –  /api/v1/customers
"""

from __future__ import annotations

from typing import List, Optional
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Depends
from pymongo import ASCENDING, DESCENDING

from app.database.mongo_client import get_db
from app.modules.patient_context import PatientContextService
from app.modules.recommendation_engine import RecommendationEngine
from app.utils.logger import get_logger
from app.utils.security import get_current_user
from app.utils.helpers import normalize_list

router = APIRouter(prefix="/customers", tags=["Customers"])
logger = get_logger(__name__)
_ctx = PatientContextService()
_eng = RecommendationEngine()


def _paginate(
    collection, query: dict, skip: int, limit: int, sort_field: str, sort_dir: int
):
    cursor = collection.find(query, {"_id": 0}).sort(sort_field, sort_dir)
    total = collection.count_documents(query)
    items = list(cursor.skip(skip).limit(limit))
    return items, total


@router.get("/", summary="List all patients")
def list_customers(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    search: Optional[str] = Query(
        default=None, description="Search by name or patient_id"
    ),
    sort_by: str = Query(default="name"),
    sort_order: str = Query(default="asc", regex="^(asc|desc)$"),
    user: dict = Depends(get_current_user),
):
    """Return a paginated list of patients with optional name/ID search.

    Args:
        page: 1-indexed page number.
        page_size: Number of items per page (1-100).
        search: Optional case-insensitive substring match against patient
            name or ``patient_id``.
        sort_by: Field name to sort by. Defaults to ``name``.
        sort_order: Sort direction, ``"asc"`` or ``"desc"``.
        user: The authenticated user, injected via ``get_current_user``.
            Its ``merchant_id`` scopes the results to the caller's pharmacy.

    Returns:
        dict: ``{"status": "ok", "page": ..., "page_size": ..., "total": ...,
            "total_pages": ..., "data": [...]}`` with the matching patient
            records.

    Raises:
        HTTPException: 422 if ``sort_order`` fails the query regex
            validation.
    """
    db = get_db()
    query: dict = {"merchant_id": user["merchant_id"]}
    if search:
        query["$or"] = [
            {"name": {"$regex": search, "$options": "i"}},
            {"patient_id": {"$regex": search, "$options": "i"}},
        ]

    skip = (page - 1) * page_size
    sort_dir = ASCENDING if sort_order == "asc" else DESCENDING

    items, total = _paginate(db["patients"], query, skip, page_size, sort_by, sort_dir)
    return {
        "status": "ok",
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": -(-total // page_size),
        "data": normalize_list(items),
    }


@router.get("/{patient_id}", summary="Get patient profile")
def get_customer(patient_id: str):
    """Return the full patient profile, including adherence and active medicines.

    Args:
        patient_id: Identifier of the patient to fetch.

    Returns:
        dict: ``{"status": "ok", "data": <profile>}`` where ``data`` is the
            profile produced by ``PatientContextService.get_patient_profile``.

    Raises:
        HTTPException: 404 if no profile is found for ``patient_id``.
    """
    profile = _ctx.get_patient_profile(patient_id)
    if not profile:
        raise HTTPException(
            status_code=404, detail=f"Patient '{patient_id}' not found."
        )
    return {"status": "ok", "data": profile}


@router.get("/{patient_id}/orders", summary="Patient order history")
def get_customer_orders(
    patient_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: Optional[str] = Query(default=None),
):
    """Return a paginated order history for a patient.

    Args:
        patient_id: Patient ID or patient name to match against the
            ``Patient ID``/``Patient Name`` order fields.
        page: 1-indexed page number.
        page_size: Number of items per page (1-100).
        status: Optional filter on the order's ``Order Status``.

    Returns:
        dict: ``{"status": "ok", "page": ..., "page_size": ..., "total": ...,
            "data": [...]}`` with the patient's matching orders, sorted by
            ``Order Date`` descending.
    """
    db = get_db()
    query: dict = {"$or": [{"Patient ID": patient_id}, {"Patient Name": patient_id}]}
    if status:
        query["Order Status"] = status

    skip = (page - 1) * page_size
    total = db["consumer_orders"].count_documents(query)
    items = list(
        db["consumer_orders"]
        .find(query, {"_id": 0})
        .sort("Order Date", DESCENDING)
        .skip(skip)
        .limit(page_size)
    )
    return {
        "status": "ok",
        "page": page,
        "page_size": page_size,
        "total": total,
        "data": items,
    }


@router.get("/{patient_id}/risk", summary="Patient refill risk")
def get_customer_risk(patient_id: str, medicine: str = Query(...)):
    """Return the refill risk score for a specific medicine for this patient.

    Args:
        patient_id: Identifier of the patient to score.
        medicine: Name of the medicine to compute the refill risk score
            for.

    Returns:
        dict: ``{"status": "ok", "data": <result>}`` where ``data`` is the
            risk score produced by
            ``PatientContextService.generate_refill_risk_score``.

    Raises:
        HTTPException: 500 if the risk score cannot be computed.
    """
    try:
        result = _ctx.generate_refill_risk_score(patient_id, medicine)
        return {"status": "ok", "data": result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{patient_id}/recommendations", summary="Personalised recommendations")
def get_customer_recommendations(patient_id: str):
    """Return personalised refill and alternative recommendations for a patient.

    Args:
        patient_id: Identifier of the patient to generate recommendations
            for.

    Returns:
        dict: ``{"status": "ok", "data": <result>}`` where ``data`` is
            produced by
            ``RecommendationEngine.get_personalized_recommendations``.
    """
    result = _eng.get_personalized_recommendations(patient_id)
    return {"status": "ok", "data": result}


@router.get("/live/summary", summary="Live patient summary derived from real orders")
def get_live_patient_summary(
    search: Optional[str] = Query(default=None, description="Search by patient name or id"),
    user: dict = Depends(get_current_user),
):
    """Return a real-time patient list derived from the merchant's actual orders.

    Builds a patient summary on the fly from the ``consumer_orders``
    collection instead of the (potentially sparse or out-of-sync) legacy
    ``patients`` collection.

    Args:
        search: Optional case-insensitive substring match against patient
            name or ID.
        user: The authenticated user, injected via ``get_current_user``.
            Its ``merchant_id`` scopes the results to the caller's pharmacy.

    Returns:
        dict: ``{"status": "ok", "data": {"patients": [...], "summary":
            {"total_patients": ..., "repeat_patients": ...,
            "high_activity_patients": ..., "generated_at": ...}}}``.
    """
    db = get_db()
    merchant_id = user["merchant_id"]
    query: dict = {"merchant_id": merchant_id}
    if search:
        query["$or"] = [
            {"Patient Name": {"$regex": search, "$options": "i"}},
            {"Patient ID": {"$regex": search, "$options": "i"}},
        ]

    orders = list(
        db["consumer_orders"]
        .find(query, {"_id": 0})
        .sort("Order Date", DESCENDING)
    )

    patients_map: dict[str, dict] = {}
    for order in orders:
        patient_name = (
            order.get("Patient Name")
            or order.get("patient_name")
            or "Customer"
        )
        patient_id = (
            order.get("Patient ID")
            or order.get("patient_id")
            or f"PT-{patient_name}".replace(" ", "-").upper()
        )
        key = f"{patient_id}|{patient_name}".lower()

        entry = patients_map.get(key)
        if entry is None:
            entry = {
                "patient_id": patient_id,
                "name": patient_name,
                "orders_count": 0,
                "last_order_date": order.get("Order Date"),
                "last_order_id": order.get("Order ID"),
                "last_channel": order.get("Order Channel"),
                "latest_medicine": order.get("Medicine Name"),
                "contact_number": order.get("Contact Number"),
                "status": "active",
            }
            patients_map[key] = entry

        entry["orders_count"] += 1
        if not entry.get("last_order_date"):
            entry["last_order_date"] = order.get("Order Date")
        if not entry.get("latest_medicine"):
            entry["latest_medicine"] = order.get("Medicine Name")
        if not entry.get("contact_number"):
            entry["contact_number"] = order.get("Contact Number")

    patients = list(patients_map.values())
    patients.sort(
        key=lambda p: str(p.get("last_order_date") or ""),
        reverse=True,
    )

    chronic_like = sum(1 for p in patients if (p.get("orders_count") or 0) >= 3)
    high_activity = sum(1 for p in patients if (p.get("orders_count") or 0) >= 5)

    return {
        "status": "ok",
        "data": {
            "patients": normalize_list(patients),
            "summary": {
                "total_patients": len(patients),
                "repeat_patients": chronic_like,
                "high_activity_patients": high_activity,
                "generated_at": datetime.utcnow().isoformat(),
            },
        },
    }