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
    """
    Return stored predictions (refill type) with optional risk-level filter.
    Sorted by risk_score descending.
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
    """
    Full personalised recommendation set including refill recs,
    alternatives for out-of-stock meds, and proactive outreach flag.
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
    """Refill-specific recommendations (availability + expiry validated)."""
    try:
        recs = _engine.generate_refill_recommendations(patient_id)
        return {"status": "ok", "count": len(recs), "data": recs}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/alternatives/{product_id}", summary="Find in-stock alternatives")
def find_alternatives(product_id: str):
    """
    Return up to 5 non-expired, in-stock alternatives in the same
    therapeutic category as *product_id*.
    """
    try:
        alts = _engine.find_alternatives(product_id)
        return {"status": "ok", "count": len(alts), "data": alts}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/predict/batch", summary="Trigger batch refill prediction")
def batch_predict():
    """
    Run the full batch prediction pipeline across all patient-medicine pairs
    with ≥ 2 orders. Returns a summary of predictions stored.
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
    """Generate and store a prediction for a specific patient + medicine pair."""
    try:
        pred = _pred.generate_prediction(patient_id, medicine_name)
        return {"status": "ok", "data": pred}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
