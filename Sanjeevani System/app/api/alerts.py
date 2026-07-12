from __future__ import annotations

"""
app/api/alerts.py  –  /api/v1/alerts
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Path, Query, Depends
from fastapi.params import Query as QueryParam
from pydantic import BaseModel
from pymongo import ASCENDING, DESCENDING

from app.database.mongo_client import get_db
from app.modules.inventory_intelligence import InventoryIntelligenceService
from app.modules.refill_outreach import RefillOutreachService
from app.modules.safety_validation import SafetyValidationService
from app.utils.security import get_current_user
from app.utils.logger import get_logger
from app.utils.helpers import build_pagination_response, normalize_list

router = APIRouter(prefix="/alerts", tags=["Alerts"])
logger = get_logger(__name__)
_inv_svc = InventoryIntelligenceService()
_saf_svc = SafetyValidationService()
_refill_outreach: RefillOutreachService | None = None


def _get_refill_outreach() -> RefillOutreachService:
    global _refill_outreach
    if _refill_outreach is None:
        _refill_outreach = RefillOutreachService()
    return _refill_outreach


@router.get("/", summary="List alerts")
def list_alerts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    alert_type: Optional[str] = Query(
        None,
        description="refill_due | low_stock | expiry_risk | interaction_warning | proactive_outreach",
    ),
    severity: Optional[str] = Query(None, regex="^(low|medium|high|critical)$"),
    is_resolved: Optional[bool] = Query(None),
    patient_id: Optional[str] = Query(None),
    sort_by: str = Query("created_at"),
    sort_order: str = Query("desc", regex="^(asc|desc)$"),
    user: dict = Depends(get_current_user),
):
    """Return a paginated, filterable list of alerts for the merchant.

    Supports filtering by alert type, severity, resolution status, and
    patient, and sorting on an arbitrary field.

    Args:
        page: 1-indexed page number.
        page_size: Number of items per page (1-100).
        alert_type: Optional filter, one of ``refill_due``, ``low_stock``,
            ``expiry_risk``, ``interaction_warning``, or
            ``proactive_outreach``.
        severity: Optional filter, one of ``low``, ``medium``, ``high``, or
            ``critical``.
        is_resolved: Optional filter on resolution status.
        patient_id: Optional case-insensitive substring filter on the
            associated patient ID.
        sort_by: Field name to sort by. Defaults to ``created_at``.
        sort_order: Sort direction, ``"asc"`` or ``"desc"``.
        user: The authenticated user, injected via ``get_current_user``.
            Its ``merchant_id`` scopes the results to the caller's pharmacy.

    Returns:
        dict: A pagination envelope built by
            ``build_pagination_response`` containing the matching alerts.

    Raises:
        HTTPException: 422 if ``severity`` or ``sort_order`` fail the query
            regex validation.
    """

    # Coerce Query parameters to their actual values if they are QueryParam objects
    # This handles cases where the function might be called internally with QueryParam objects
    # instead of the resolved values.
    def _res(v, fallback):
        if hasattr(v, "default"):
            return v.default if v.default is not ... else fallback
        return v

    p = int(_res(page, 1))
    ps = int(_res(page_size, 20))
    alert_type = _res(alert_type, None)
    severity = _res(severity, None)
    is_resolved = _res(is_resolved, None)
    patient_id = _res(patient_id, None)
    sort_by = _res(sort_by, "created_at")
    sort_order = _res(sort_order, "desc")

    db = get_db()
    query: dict = {"merchant_id": user["merchant_id"]}
    if alert_type:
        query["alert_type"] = alert_type
    if severity:
        query["severity"] = severity
    if is_resolved is not None:
        query["is_resolved"] = is_resolved
    if patient_id:
        query["patient_id"] = {"$regex": patient_id, "$options": "i"}

    # Redundant but kept for safety in case of non-int strings from API
    skip = (p - 1) * ps

    sort_dir = ASCENDING if sort_order == "asc" else DESCENDING
    total = db["alerts"].count_documents(query)
    items = list(
        db["alerts"]
        .find(query, {"_id": 0})
        .sort(sort_by, sort_dir)
        .skip(skip)
        .limit(ps)
    )
    return build_pagination_response(
        items,
        total,
        p,
        ps
    )


@router.get("/refills", summary="Get refill alerts")
def get_refill_alerts(user: dict = Depends(get_current_user)):
    """Return refill-due alerts for the merchant.

    Shortcut wrapper around :func:`list_alerts` filtered to
    ``alert_type="refill_due"`` with a page size of 100.

    Args:
        user: The authenticated user, injected via ``get_current_user``.
            Its ``merchant_id`` scopes the results to the caller's pharmacy.

    Returns:
        dict: The same pagination envelope returned by :func:`list_alerts`.
    """
    return list_alerts(page=1, page_size=100, alert_type="refill_due", user=user)


@router.get("/inventory", summary="Get inventory alerts")
def get_inventory_alerts(user: dict = Depends(get_current_user)):
    """Return low-stock inventory alerts for the merchant.

    Shortcut wrapper around :func:`list_alerts` filtered to
    ``alert_type="low_stock"`` with a page size of 100.

    Args:
        user: The authenticated user, injected via ``get_current_user``.
            Its ``merchant_id`` scopes the results to the caller's pharmacy.

    Returns:
        dict: The same pagination envelope returned by :func:`list_alerts`.
    """
    return list_alerts(page=1, page_size=100, alert_type="low_stock", user=user)


@router.get("/summary", summary="Alert counts by type and severity")
def alert_summary(user: dict = Depends(get_current_user)):
    """Return alert counts grouped by type and by severity.

    Provides quick telemetry: total unresolved alerts, a breakdown of
    counts (and unresolved counts) per alert type, and a breakdown of
    counts per severity level.

    Args:
        user: The authenticated user, injected via ``get_current_user``.
            Its ``merchant_id`` scopes the aggregation to the caller's
            pharmacy.

    Returns:
        dict: ``{"status": "ok", "data": {"total_unresolved": <int>,
            "by_type": [...], "by_severity": [...]}}``.
    """
    db = get_db()

    by_type = list(
        db["alerts"].aggregate(
            [
                {"$match": {"merchant_id": user["merchant_id"]}},
                {
                    "$group": {
                        "_id": "$alert_type",
                        "count": {"$sum": 1},
                        "unresolved": {
                            "$sum": {"$cond": [{"$eq": ["$is_resolved", False]}, 1, 0]}
                        },
                    }
                },
                {"$sort": {"count": -1}},
            ]
        )
    )
    by_severity = list(
        db["alerts"].aggregate(
            [
                {"$match": {"merchant_id": user["merchant_id"]}},
                {"$group": {"_id": "$severity", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
            ]
        )
    )
    total_unresolved = db["alerts"].count_documents({"is_resolved": False, "merchant_id": user["merchant_id"]})

    return {
        "status": "ok",
        "data": {
            "total_unresolved": total_unresolved,
            "by_type": [
                {"type": r["_id"], "count": r["count"], "unresolved": r["unresolved"]}
                for r in by_type
            ],
            "by_severity": [
                {"severity": r["_id"], "count": r["count"]} for r in by_severity
            ],
        },
    }


@router.post("/generate/inventory", summary="Generate inventory alerts on-demand")
def generate_inventory_alerts(user: dict = Depends(get_current_user)):
    """Scan inventory and upsert low-stock and expiry-risk alerts.

    Args:
        user: The authenticated user, injected via ``get_current_user``.
            Its ``merchant_id`` scopes the scan to the caller's pharmacy.

    Returns:
        dict: ``{"status": "ok", "data": <counts>}`` where ``data``
            summarizes the alerts created/updated, as returned by
            ``InventoryIntelligenceService.generate_inventory_alerts``.

    Raises:
        HTTPException: 500 if alert generation fails.
    """
    try:
        counts = _inv_svc.generate_inventory_alerts(merchant_id=user["merchant_id"])
        return {"status": "ok", "data": counts}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/generate/safety", summary="Generate safety alerts on-demand")
def generate_safety_alerts(user: dict = Depends(get_current_user)):
    """Scan pending orders and create drug-interaction warning alerts.

    Args:
        user: The authenticated user, injected via ``get_current_user``.
            Its ``merchant_id`` scopes the scan to the caller's pharmacy.

    Returns:
        dict: ``{"status": "ok", "data": <result>}`` where ``data``
            summarizes the safety alerts created, as returned by
            ``SafetyValidationService.generate_safety_alerts``.

    Raises:
        HTTPException: 500 if alert generation fails.
    """
    try:
        result = _saf_svc.generate_safety_alerts(merchant_id=user["merchant_id"])
        return {"status": "ok", "data": result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


class RefillOutreachRequest(BaseModel):
    use_demo_data: bool = True
    demo_file_path: Optional[str] = None
    reminder_days: list[int] = [10, 28]


@router.post("/generate/refill-outreach", summary="Generate refill alerts and send WhatsApp + app outreach")
def generate_refill_outreach(
    body: RefillOutreachRequest = Body(default_factory=RefillOutreachRequest),
    user: dict = Depends(get_current_user),
):
    """Generate refill alerts and dispatch WhatsApp/app outreach messages.

    Runs either a demo outreach flow (using sample or file-provided data)
    or a live outreach flow (scanning real patient refill risk against the
    given reminder-day thresholds), depending on ``body.use_demo_data``.

    Args:
        body: Outreach configuration. When ``use_demo_data`` is ``True``
            (default), an optional ``demo_file_path`` may point to a demo
            dataset. When ``False``, ``reminder_days`` controls how many
            days before/after the predicted refill date reminders fire
            (defaults to ``[10, 28]``).
        user: The authenticated user, injected via ``get_current_user``.
            Its ``merchant_id`` scopes the outreach run to the caller's
            pharmacy.

    Returns:
        dict: ``{"status": "ok", "data": <result>}`` where ``data``
            summarizes the outreach run, as returned by
            ``RefillOutreachService.run_demo_outreach`` or
            ``RefillOutreachService.run_live_outreach``.

    Raises:
        HTTPException: 500 if the outreach run fails.
    """
    try:
        refill_outreach = _get_refill_outreach()
        if body.use_demo_data:
            result = refill_outreach.run_demo_outreach(
                merchant_id=user["merchant_id"],
                demo_file_path=body.demo_file_path,
            )
        else:
            result = refill_outreach.run_live_outreach(
                merchant_id=user["merchant_id"],
                reminder_days=body.reminder_days or [10, 28],
            )
        return {"status": "ok", "data": result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))





class ResolveRequest(BaseModel):
    resolved_by: str = "pharmacist"
    resolution_note: str = ""


@router.patch("/{alert_id}/resolve", summary="Mark an alert as resolved")
def resolve_alert(
    alert_id: str = Path(..., description="Alert identifier"),
    body: ResolveRequest = Body(default_factory=ResolveRequest),
    user: dict = Depends(get_current_user),
):
    """Mark an alert as resolved by a pharmacist.

    Send JSON body: ``{"resolved_by": "pharmacist", "resolution_note": "..."}``

    Args:
        alert_id: Identifier (``_id``) of the alert to resolve.
        body: Resolution details — who resolved it and an optional note.
        user: The authenticated user, injected via ``get_current_user``.
            Its ``merchant_id`` scopes the lookup to the caller's pharmacy.

    Returns:
        dict: ``{"status": "ok", "data": <alert>}`` with the updated alert
            document (excluding ``_id``).

    Raises:
        HTTPException: 404 if no matching alert is found for the merchant.
    """
    db = get_db()

    now = datetime.now(tz=timezone.utc)
    result = db["alerts"].find_one_and_update(
        {"_id": alert_id, "merchant_id": user["merchant_id"]},
        {
            "$set": {
                "is_resolved": True,
                "resolved_by": body.resolved_by,
                "resolution_note": body.resolution_note,
                "resolved_at": now,
                "updated_at": now,
            }
        },
        projection={"_id": 0},
        return_document=True,
    )
    if not result:
        raise HTTPException(status_code=404, detail=f"Alert '{alert_id}' not found.")
    return {"status": "ok", "data": result}


@router.get("/{alert_id}", summary="Get single alert by ID")
def get_alert(alert_id: str, user: dict = Depends(get_current_user)):
    """Fetch one alert by its identifier.

    Args:
        alert_id: Identifier (``_id``) of the alert to fetch.
        user: The authenticated user, injected via ``get_current_user``.
            Its ``merchant_id`` scopes the lookup to the caller's pharmacy.

    Returns:
        dict: ``{"status": "ok", "data": <alert>}`` with the alert document
            (excluding ``_id``).

    Raises:
        HTTPException: 404 if no matching alert is found for the merchant.
    """
    db = get_db()
    doc = db["alerts"].find_one({"_id": alert_id, "merchant_id": user["merchant_id"]}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail=f"Alert '{alert_id}' not found.")
    return {"status": "ok", "data": doc}