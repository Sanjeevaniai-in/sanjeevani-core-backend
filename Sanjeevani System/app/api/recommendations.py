"""
app/api/recommendations.py  –  /api/v1/recommendations
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.modules.recommendation_engine import RecommendationEngine
from app.modules.refill_prediction import RefillPredictionService
from app.utils.logger import get_logger

router = APIRouter(prefix="/recommendations", tags=["Recommendations"])
logger = get_logger(__name__)
_engine = RecommendationEngine()
_pred = RefillPredictionService()


@router.get("/", summary="Bulk refill recommendations (all patients)")
def list_recommendations(
    risk_level: Optional[str] = Query(
        default=None,
        regex="^(critical|high|medium|low)$",
        description="Filter by risk level",
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """Return paginated, stored refill predictions across all patients.

    Reads directly from the ``predictions`` collection (documents with
    ``prediction_type == "refill"``), optionally filtered by risk level,
    and sorted by ``risk_score`` descending.

    Args:
        risk_level: Optional filter, one of ``"critical"``, ``"high"``,
            ``"medium"``, or ``"low"``.
        page: 1-indexed page number.
        page_size: Number of items per page (1-100).

    Returns:
        dict: ``{"status": "ok", "page": ..., "page_size": ..., "total": ...,
            "total_pages": ..., "data": [...]}`` with the matching prediction
            documents.

    Raises:
        HTTPException: 422 if ``risk_level`` fails the query regex
            validation.
    """
    from app.database.mongo_client import get_db
    from pymongo import DESCENDING

    db = get_db()
    query: dict = {"prediction_type": "refill"}
    if risk_level:
        query["risk_level"] = risk_level

    skip = (page - 1) * page_size
    total = db["predictions"].count_documents(query)
    items = list(
        db["predictions"]
        .find(query, {"_id": 0})
        .sort("risk_score", DESCENDING)
        .skip(skip)
        .limit(page_size)
    )
    return {
        "status": "ok",
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": -(-total // page_size),
        "data": items,
    }


@router.get(
    "/patient/{patient_id}", summary="Personalised recommendations for a patient"
)
def patient_recommendations(patient_id: str):
    """Return the full personalised recommendation set for a patient.

    Combines refill recommendations, alternatives for out-of-stock
    medicines, and a proactive outreach flag.

    Args:
        patient_id: Identifier of the patient to generate recommendations
            for.

    Returns:
        dict: ``{"status": "ok", "data": <recommendations>}`` where
            ``data`` is produced by
            ``RecommendationEngine.get_personalized_recommendations``.

    Raises:
        HTTPException: 500 if the recommendations cannot be generated.
    """
    try:
        data = _engine.get_personalized_recommendations(patient_id)
        return {"status": "ok", "data": data}
    except Exception as exc:
        logger.error("Recommendation error", extra={"error": str(exc)})
        raise HTTPException(status_code=500, detail=str(exc))


@router.get(
    "/patient/{patient_id}/refills", summary="Refill recommendations for a patient"
)
def patient_refill_recommendations(patient_id: str):
    """Return refill-specific recommendations for a patient.

    Recommendations are validated for stock availability and expiry before
    being returned.

    Args:
        patient_id: Identifier of the patient to generate refill
            recommendations for.

    Returns:
        dict: ``{"status": "ok", "count": <int>, "data": [...]}`` with the
            refill recommendations produced by
            ``RecommendationEngine.generate_refill_recommendations``.

    Raises:
        HTTPException: 500 if the recommendations cannot be generated.
    """
    try:
        recs = _engine.generate_refill_recommendations(patient_id)
        return {"status": "ok", "count": len(recs), "data": recs}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/alternatives/{product_id}", summary="Find in-stock alternatives")
def find_alternatives(product_id: str):
    """Return in-stock alternatives for an out-of-stock or risky product.

    Returns up to 5 non-expired, in-stock alternatives in the same
    therapeutic category as ``product_id``.

    Args:
        product_id: Identifier of the product to find alternatives for.

    Returns:
        dict: ``{"status": "ok", "count": <int>, "data": [...]}`` with up
            to 5 alternative products from
            ``RecommendationEngine.find_alternatives``.

    Raises:
        HTTPException: 500 if the alternatives cannot be computed.
    """
    try:
        alts = _engine.find_alternatives(product_id)
        return {"status": "ok", "count": len(alts), "data": alts}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/predict/batch", summary="Trigger batch refill prediction")
def batch_predict():
    """Trigger the batch refill prediction pipeline for all eligible patients.

    Runs the full batch prediction pipeline across all patient-medicine
    pairs with 2 or more orders and persists the resulting predictions.

    Returns:
        dict: ``{"status": "ok", "data": <summary>}`` where ``data`` is a
            summary of predictions stored, produced by
            ``RefillPredictionService.batch_predict_all_patients``.

    Raises:
        HTTPException: 500 if the batch prediction run fails.
    """
    try:
        summary = _pred.batch_predict_all_patients()
        return {"status": "ok", "data": summary}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/predict/single", summary="Predict refill for one patient+medicine")
def predict_single(
    patient_id: str = Query(...),
    medicine_name: str = Query(...),
):
    """Generate and store a refill prediction for one patient-medicine pair.

    Args:
        patient_id: Identifier of the patient to generate the prediction
            for.
        medicine_name: Name of the medicine to generate the prediction for.

    Returns:
        dict: ``{"status": "ok", "data": <prediction>}`` where ``data`` is
            the stored prediction produced by
            ``RefillPredictionService.generate_prediction``.

    Raises:
        HTTPException: 500 if the prediction cannot be generated.
    """
    try:
        pred = _pred.generate_prediction(patient_id, medicine_name)
        return {"status": "ok", "data": pred}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))