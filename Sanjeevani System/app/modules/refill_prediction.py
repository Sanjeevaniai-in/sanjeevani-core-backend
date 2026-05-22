"""
app/modules/refill_prediction.py
─────────────────────────────────────────────────────────────────────────────
Statistical refill prediction engine.
Stores results in ``predictions`` collection and generates alerts for
high-risk patients in the ``alerts`` collection.

Public API
──────────
    from app.modules.refill_prediction import RefillPredictionService
    svc = RefillPredictionService()
    pred = svc.generate_prediction("P001", "Metformin 500mg")
    svc.batch_predict_all_patients()
"""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.database.mongo_client import get_db
from app.modules.patient_context import PatientContextService
from app.utils.logger import get_logger

logger = get_logger(__name__)

_HIGH_RISK_THRESHOLD = 65  # risk_score >= this → create alert


class RefillPredictionService:
    """Statistical refill prediction engine (no ML models)."""

    def __init__(self) -> None:
        self._db = None
        self.patient_ctx = PatientContextService()

    @property
    def db(self):
        if self._db is None:
            self._db = get_db()
        return self._db

    # ──────────────────────────────────────────────────────────────────────
    # 1. calculate_purchase_intervals
    # ──────────────────────────────────────────────────────────────────────

    def calculate_purchase_intervals(
        self, patient_id: str, product_id: str
    ) -> Dict[str, Any]:
        """
        Return all inter-purchase intervals (in days) for a patient+medicine.

        Result
        ──────
        ``intervals``       – list of gap sizes in days
        ``mean_days``       – mean interval
        ``std_days``        – standard deviation (0 if single order)
        ``min_days``        – minimum observed interval
        ``max_days``        – maximum observed interval
        ``order_count``     – total orders considered
        """
        orders = self._fetch_orders(patient_id, product_id)
        dates = self._extract_sorted_dates(orders)
        intervals = self._intervals(dates)

        if not intervals:
            return {
                "intervals": [],
                "mean_days": 0.0,
                "std_days": 0.0,
                "min_days": 0.0,
                "max_days": 0.0,
                "order_count": len(orders),
            }

        return {
            "intervals": intervals,
            "mean_days": round(statistics.mean(intervals), 2),
            "std_days": round(
                statistics.stdev(intervals) if len(intervals) > 1 else 0.0, 2
            ),
            "min_days": min(intervals),
            "max_days": max(intervals),
            "order_count": len(orders),
        }

    # ──────────────────────────────────────────────────────────────────────
    # 2. get_avg_consumption_rate
    # ──────────────────────────────────────────────────────────────────────

    def get_avg_consumption_rate(self, patient_id: str, product_id: str) -> float:
        """Units consumed per day (0.0 if not computable)."""
        data = self.patient_ctx.estimate_daily_consumption(patient_id, product_id)
        return data.get("daily_consumption", 0.0)

    # ──────────────────────────────────────────────────────────────────────
    # 3. predict_refill_date
    # ──────────────────────────────────────────────────────────────────────

    def predict_refill_date(
        self, patient_id: str, product_id: str
    ) -> Optional[datetime]:
        """
        Predict the next refill date using: (Quantity / Daily Dosage) based on "Dosage Frequency".
        """
        orders = self._fetch_orders(patient_id, product_id)
        if not orders:
            return None

        last_order = orders[-1]

        # Parse date
        raw_date = last_order.get("purchase_date")
        if isinstance(raw_date, (int, float)):
            base_date = datetime(1899, 12, 30) + timedelta(days=float(raw_date))
        elif isinstance(raw_date, str):
            try:
                base_date = datetime.fromisoformat(str(raw_date))
            except ValueError:
                base_date = datetime.now(timezone.utc)
        elif isinstance(raw_date, datetime):
            base_date = raw_date
        else:
            base_date = datetime.now(timezone.utc)

        if base_date.tzinfo is None:
            base_date = base_date.replace(tzinfo=timezone.utc)

        # Quantity and dosage frequency
        try:
            quantity = float(last_order.get("quantity", 1.0))
        except (ValueError, TypeError):
            quantity = 1.0

        freq_str = str(last_order.get("dosage_frequency", "once")).lower()
        if "three times" in freq_str:
            daily_dosage = 3.0
        elif "twice" in freq_str:
            daily_dosage = 2.0
        elif "once" in freq_str:
            daily_dosage = 1.0
        else:
            daily_dosage = 1.0  # fallback like "as needed"

        # Determine multiplier from package size
        product_doc = self.db.products.find_one({"product_name": product_id})
        multiplier = 1.0
        if product_doc:
            size_str = str(product_doc.get("package_size", ""))
            import re

            match = re.search(r"(\d+)\s*st", size_str, re.IGNORECASE)
            if match:
                multiplier = float(match.group(1))
            else:
                multiplier = 30.0  # assume 30 pieces if no stück specified
        else:
            multiplier = 30.0

        if quantity >= 10:
            multiplier = 1.0  # assume quantity already means units

        total_units = quantity * multiplier
        days_to_refill = total_units / daily_dosage

        return base_date + timedelta(days=round(days_to_refill))

    # ──────────────────────────────────────────────────────────────────────
    # 4. calculate_confidence_score
    # ──────────────────────────────────────────────────────────────────────

    def calculate_confidence_score(self, order_count: int, variability: float) -> float:
        """
        Return a 0.0–1.0 confidence score.

        Confidence increases with more orders and decreases with high
        interval variability (coefficient of variation).

        Parameters
        ──────────
        order_count : total orders used
        variability : coefficient of variation (std/mean) of intervals
        """
        # Base score from order count (saturates at ~20 orders)
        base = min(order_count / 20.0, 1.0)
        # Penalty from variability (CV capped at 1.0)
        penalty = min(variability, 1.0)
        score = base * (1.0 - 0.5 * penalty)
        return round(max(0.0, min(score, 1.0)), 4)

    # ──────────────────────────────────────────────────────────────────────
    # 5. generate_prediction (single patient × medicine)
    # ──────────────────────────────────────────────────────────────────────

    def generate_prediction(self, patient_id: str, product_id: str) -> Dict[str, Any]:
        """
        Generate and **store** a refill prediction for one patient+medicine.

        Returns the stored prediction document (without ``_id``).
        """
        iv_data = self.calculate_purchase_intervals(patient_id, product_id)
        intervals = iv_data["intervals"]
        order_count = iv_data["order_count"]

        cv = (
            (statistics.stdev(intervals) / statistics.mean(intervals))
            if len(intervals) > 1 and statistics.mean(intervals) > 0
            else 0.0
        )
        confidence = self.calculate_confidence_score(order_count, cv)
        refill_date = self.predict_refill_date(patient_id, product_id)
        risk_info = self.patient_ctx.generate_refill_risk_score(patient_id, product_id)
        days_rem = self.patient_ctx.calculate_days_remaining(patient_id, product_id)

        # Fetch patient name for readability
        patient = self.db["patients"].find_one({"patient_id": patient_id}, {"name": 1})
        patient_name = patient.get("name", patient_id) if patient else patient_id

        doc = {
            "prediction_type": "refill",
            "patient_id": patient_id,
            "patient_name": patient_name,
            "medicine_name": product_id,
            "product_id": product_id,
            "predicted_refill_date": refill_date.isoformat() if refill_date else None,
            "predicted_value": days_rem.get("days_remaining", 0),
            "confidence_score": confidence,
            "recommended_quantity": self._recommend_quantity(patient_id, product_id),
            "risk_score": risk_info.get("risk_score", 0),
            "risk_level": risk_info.get("risk_level", "low"),
            "explanation": risk_info.get("explanation", ""),
            "based_on_orders": order_count,
            "feature_importances": {
                "order_count": min(order_count / 20, 1.0),
                "interval_regularity": round(1 - min(cv, 1.0), 4),
                "days_remaining_factor": min(
                    max(30 - (days_rem.get("days_remaining") or 30), 0) / 30, 1.0
                ),
            },
            "model_version": "statistical-v1",
            "generated_at": datetime.now(tz=timezone.utc),
            "is_actioned": False,
        }

        # Upsert by patient + medicine (keep freshest prediction)
        self.db["predictions"].update_one(
            {
                "prediction_type": "refill",
                "patient_id": patient_id,
                "medicine_name": product_id,
            },
            {"$set": doc},
            upsert=True,
        )
        logger.debug(
            "Stored prediction",
            extra={
                "patient": patient_id,
                "medicine": product_id,
                "risk": doc["risk_level"],
            },
        )

        # Generate alert if high risk
        if risk_info.get("risk_score", 0) >= _HIGH_RISK_THRESHOLD:
            self._create_refill_alert(doc)

        return doc

    # ──────────────────────────────────────────────────────────────────────
    # 6. batch_predict_all_patients
    # ──────────────────────────────────────────────────────────────────────

    def batch_predict_all_patients(self) -> Dict[str, Any]:
        """
        Iterate over all (patient, medicine) pairs with at least 2 orders
        and store predictions.

        Returns a summary dict with counts.
        """
        logger.info("Starting batch refill prediction…")

        pipeline = [
            {
                "$group": {
                    "_id": {
                        "patient_id": "$patient_id",
                        "product_name": "$product_name",
                    },
                    "order_count": {"$sum": 1},
                }
            },
            {"$match": {"order_count": {"$gte": 1}}},
        ]

        pairs = list(self.db["consumer_orders"].aggregate(pipeline, allowDiskUse=True))
        logger.info("Patient-medicine pairs to predict", extra={"count": len(pairs)})

        success = failed = high_risk = 0
        for pair in pairs:
            pid = str(pair["_id"].get("patient_id") or "")
            med = str(pair["_id"].get("product_name") or "")
            if not pid or not med:
                continue
            try:
                pred = self.generate_prediction(pid, med)
                success += 1
                if pred.get("risk_score", 0) >= _HIGH_RISK_THRESHOLD:
                    high_risk += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Prediction failed",
                    extra={"patient": pid, "medicine": med, "error": str(exc)},
                )
                failed += 1

        summary = {
            "total_pairs": len(pairs),
            "predictions_ok": success,
            "failed": failed,
            "high_risk": high_risk,
            "completed_at": datetime.now(tz=timezone.utc).isoformat(),
        }
        logger.info("Batch prediction complete", extra=summary)
        return summary

    # ──────────────────────────────────────────────────────────────────────
    # Private helpers
    # ──────────────────────────────────────────────────────────────────────

    def _fetch_orders(self, patient_id: str, product_id: str) -> List[Dict[str, Any]]:
        return list(
            self.db["consumer_orders"]
            .find(
                {
                    "patient_id": patient_id,
                    "product_name": product_id,
                }
            )
            .sort("purchase_date", 1)
        )

    @staticmethod
    def _extract_sorted_dates(orders: List[Dict]) -> List[datetime]:
        dates: List[datetime] = []
        for o in orders:
            raw = o.get("purchase_date")
            if isinstance(raw, (int, float)):
                dates.append(datetime(1899, 12, 30) + timedelta(days=float(raw)))
            elif isinstance(raw, datetime):
                dates.append(raw)
            elif isinstance(raw, str):
                try:
                    dates.append(datetime.fromisoformat(raw))
                except ValueError:
                    pass
        return sorted(dates)

    @staticmethod
    def _intervals(dates: List[datetime]) -> List[float]:
        if len(dates) < 2:
            return []
        return [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]

    @staticmethod
    def _last_quantity(orders: List[Dict]) -> float:
        for o in reversed(orders):
            q = o.get("Quantity Ordered") or o.get("Quantity")
            if q is not None:
                try:
                    return float(q)
                except (TypeError, ValueError):
                    pass
        return 0.0

    def _recommend_quantity(self, patient_id: str, product_id: str) -> Optional[float]:
        """Recommend qty based on avg order quantity, rounded to nearest 5."""
        orders = self._fetch_orders(patient_id, product_id)
        qtys = []
        for o in orders:
            q = o.get("Quantity Ordered") or o.get("Quantity")
            if q is not None:
                try:
                    qtys.append(float(q))
                except (TypeError, ValueError):
                    pass
        if not qtys:
            return None
        avg = statistics.mean(qtys)
        return round(avg / 5) * 5  # round to nearest 5

    def _create_refill_alert(self, pred: Dict[str, Any]) -> None:
        """Insert / upsert a high-risk refill alert into the alerts collection."""
        alert = {
            "alert_type": "refill_due",
            "severity": "high" if pred["risk_score"] >= 85 else "medium",
            "title": f"Refill Due: {pred['medicine_name']}",
            "message": (
                f"Patient {pred.get('patient_name', pred['patient_id'])} "
                f"is due for a refill of {pred['medicine_name']}. "
                f"Risk score: {pred['risk_score']}/100. "
                f"{pred.get('explanation', '')}"
            ),
            "patient_id": pred["patient_id"],
            "patient_name": pred.get("patient_name"),
            "medicine_name": pred["medicine_name"],
            "is_resolved": False,
            "auto_actioned": False,
            "created_at": datetime.now(tz=timezone.utc),
            "updated_at": datetime.now(tz=timezone.utc),
        }
        self.db["alerts"].update_one(
            {
                "alert_type": "refill_due",
                "patient_id": pred["patient_id"],
                "medicine_name": pred["medicine_name"],
                "is_resolved": False,
            },
            {"$set": alert},
            upsert=True,
        )
