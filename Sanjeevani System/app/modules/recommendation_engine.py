"""
app/modules/recommendation_engine.py
─────────────────────────────────────────────────────────────────────────────
Recommendation engine — never recommends expired or out-of-stock items.

Public API
──────────
    from app.modules.recommendation_engine import RecommendationEngine
    eng = RecommendationEngine()
    recs = eng.get_personalized_recommendations("P001")
    alts = eng.find_alternatives("Metformin 500mg")
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.database.mongo_client import get_db
from app.modules.patient_context import PatientContextService
from app.modules.safety_validation import SafetyValidationService
from app.utils.logger import get_logger

logger = get_logger(__name__)

_MAX_RECS_PER_PATIENT = 10


class RecommendationEngine:
    """Rule-based personalised medicine recommendation engine."""

    def __init__(self) -> None:
        self._db = None
        self.patient_ctx = PatientContextService()
        self.safety_svc = SafetyValidationService()

    @property
    def db(self):
        if self._db is None:
            self._db = get_db()
        return self._db

    # ──────────────────────────────────────────────────────────────────────
    # 1. generate_refill_recommendations
    # ──────────────────────────────────────────────────────────────────────

    def generate_refill_recommendations(self, patient_id: str) -> List[Dict[str, Any]]:
        """
        Return a list of refill recommendations for a patient's regular meds.

        Only includes items that:
        - are in stock (current_stock > 0)
        - are not expired
        - have a refill risk score > 20
        """
        profile = self.patient_ctx.get_patient_profile(patient_id)
        if not profile:
            return []

        meds = profile.get("regular_medicines") or []
        if not meds:
            return []

        recommendations: List[Dict[str, Any]] = []

        for med in meds:
            if not self._is_available(med):
                logger.debug("Skipping unavailable med", extra={"med": med})
                continue
            if not self._is_not_expired(med):
                logger.debug("Skipping expired med", extra={"med": med})
                continue

            risk = self.patient_ctx.generate_refill_risk_score(patient_id, med)
            if risk["risk_score"] < 20:
                continue

            days_info = self.patient_ctx.calculate_days_remaining(patient_id, med)

            rec = {
                "type": "refill",
                "patient_id": patient_id,
                "medicine_name": med,
                "risk_score": risk["risk_score"],
                "risk_level": risk["risk_level"],
                "days_remaining": days_info.get("days_remaining"),
                "explanation": risk.get("explanation", ""),
                "action": "Contact patient for refill",
                "urgency_rank": self._urgency_rank(risk["risk_level"]),
            }
            safety = self.check_recommendation_safety(rec)
            rec["safety"] = safety
            recommendations.append(rec)

        return self.rank_recommendations_by_urgency(recommendations)

    # ──────────────────────────────────────────────────────────────────────
    # 2. find_alternatives
    # ──────────────────────────────────────────────────────────────────────

    def find_alternatives(self, product_id: str) -> List[Dict[str, Any]]:
        """
        Safety-First rule-based system to find alternatives with the same generic name / salt.
        """
        source = self.db["products"].find_one(
            {
                "$or": [
                    {"product_id": product_id},
                    {"product_name": product_id},
                    {"Medicine Name": product_id},
                    {"Product ID": product_id},
                ]
            }
        )
        if not source:
            return []

        name = str(source.get("product_name") or source.get("Medicine Name") or "")
        desc = str(source.get("description") or source.get("descriptions") or "")

        # 1. Rule-based salt extraction
        text = (name + " " + desc).lower()
        known_salts = [
            "paracetamol",
            "omega-3",
            "cetirizin",
            "diclo",
            "hyaluron",
            "magnesium",
            "vitamin b",
            "vitamin d",
            "ibuprofen",
            "minoxidil",
            "panthenol",
            "ramipril",
            "loperamid",
            "macrogol",
            "salicylsäure",
            "b-vitamin",
        ]

        salt = ""
        for s in known_salts:
            if s in text:
                salt = s
                break

        if not salt:
            import re

            words = [w.lower() for w in re.findall(r"[a-zA-Z]{4,}", name)]
            if words:
                salt = words[0]
            else:
                return []

        # 2. Search products containing the same salt
        candidates = list(
            self.db["products"].find(
                {
                    "$or": [
                        {"product_name": {"$regex": salt, "$options": "i"}},
                        {"description": {"$regex": salt, "$options": "i"}},
                        {"descriptions": {"$regex": salt, "$options": "i"}},
                        {"Medicine Name": {"$regex": salt, "$options": "i"}},
                        {"Generic Name": {"$regex": salt, "$options": "i"}},
                    ]
                }
            )
        )

        alternatives = []
        for prod in candidates:
            c_name = str(prod.get("product_name") or prod.get("Medicine Name") or "")
            if c_name.lower() == name.lower():
                continue

            if not self._is_available(c_name):
                continue
            if not self._is_not_expired(c_name):
                continue

            inv = self.db["inventory"].find_one(
                {"$or": [{"medicine_name": c_name}, {"product_name": c_name}]}
            )

            alternatives.append(
                {
                    "product_id": str(
                        prod.get("product_id") or prod.get("Product ID", "")
                    ),
                    "medicine_name": c_name,
                    "salt_matched": salt,
                    "unit_price": prod.get("price_rec") or prod.get("Unit Price"),
                    "current_stock": inv.get("current_stock") if inv else None,
                    "is_same_generic": True,
                }
            )

        alternatives.sort(
            key=lambda x: float(x.get("current_stock") or 0), reverse=True
        )
        return alternatives[:5]

    # ──────────────────────────────────────────────────────────────────────
    # 3. check_recommendation_safety
    # ──────────────────────────────────────────────────────────────────────

    def check_recommendation_safety(
        self, recommendation: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Perform lightweight safety checks on a recommendation dict that
        contains at least ``patient_id`` and ``medicine_name``.

        Returns ``{"is_safe": bool, "warnings": list[str]}``.
        """
        pid = recommendation.get("patient_id", "")
        med = recommendation.get("medicine_name", "")
        if not pid or not med:
            return {
                "is_safe": False,
                "warnings": ["Missing patient_id or medicine_name"],
            }

        warnings: List[str] = []

        # Expiry
        if not self._is_not_expired(med):
            return {"is_safe": False, "warnings": [f"{med} is expired."]}

        # Stock
        if not self._is_available(med):
            return {"is_safe": False, "warnings": [f"{med} is out of stock."]}

        # Prescription reminder
        prod = self.db["products"].find_one({"Medicine Name": med})
        if prod:
            rx_raw = (
                prod.get("Requires Prescription")
                or prod.get("Prescription Required")
                or ""
            )
            if str(rx_raw).strip().lower() in ("yes", "y", "true", "1"):
                warnings.append(
                    f"{med} requires a prescription — confirm before dispensing."
                )

        return {"is_safe": True, "warnings": warnings}

    # ──────────────────────────────────────────────────────────────────────
    # 4. get_personalized_recommendations
    # ──────────────────────────────────────────────────────────────────────

    def get_personalized_recommendations(self, patient_id: str) -> Dict[str, Any]:
        """
        Return a comprehensive set of personalised recommendations:

        - ``refill_recommendations``  – meds due for refill
        - ``alternatives``            – alternatives for out-of-stock items
        - ``proactive_outreach``      – bool; should proactively contact patient?
        - ``preferred_channel``       – WhatsApp / SMS / Phone
        """
        profile = self.patient_ctx.get_patient_profile(patient_id)
        if not profile:
            return {
                "patient_id": patient_id,
                "refill_recommendations": [],
                "alternatives": [],
                "proactive_outreach": False,
                "preferred_channel": None,
            }

        refill_recs = self.generate_refill_recommendations(patient_id)

        # Find alternatives for any out-of-stock regular meds
        alt_map: Dict[str, List] = {}
        for med in profile.get("regular_medicines") or []:
            if not self._is_available(med):
                alts = self.find_alternatives(med)
                if alts:
                    alt_map[med] = alts

        # Proactive outreach if any critical/high risk refills
        high_risk_recs = [
            r for r in refill_recs if r.get("risk_level") in ("critical", "high")
        ]
        proactive = len(high_risk_recs) > 0

        return {
            "patient_id": patient_id,
            "patient_name": profile.get("name"),
            "preferred_channel": profile.get("preferred_channel"),
            "refill_recommendations": refill_recs[:_MAX_RECS_PER_PATIENT],
            "alternatives": alt_map,
            "proactive_outreach": proactive,
            "high_risk_count": len(high_risk_recs),
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        }

    # ──────────────────────────────────────────────────────────────────────
    # 5. rank_recommendations_by_urgency
    # ──────────────────────────────────────────────────────────────────────

    def rank_recommendations_by_urgency(
        self, recommendations: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Sort recommendations:  critical → high → medium → low.
        Within the same level, higher risk_score ranks first.
        """
        order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        return sorted(
            recommendations,
            key=lambda r: (
                order.get(r.get("risk_level", "low"), 3),
                -float(r.get("risk_score", 0)),
            ),
        )

    # ──────────────────────────────────────────────────────────────────────
    # Private helpers
    # ──────────────────────────────────────────────────────────────────────

    def _is_available(self, medicine_name: str) -> bool:
        """Return True if current_stock > 0 in inventory."""
        inv = self.db["inventory"].find_one(
            {
                "$or": [
                    {"medicine_name": medicine_name},
                    {"product_name": medicine_name},
                ]
            },
            {"current_stock": 1},
        )
        if not inv:
            return True  # not tracked → assume available
        return float(inv.get("current_stock") or 0) > 0

    def _is_not_expired(self, medicine_name: str) -> bool:
        """Return True if product is NOT expired."""
        inv = self.db["inventory"].find_one(
            {
                "$or": [
                    {"medicine_name": medicine_name},
                    {"product_name": medicine_name},
                ]
            },
            {"expiry_date": 1},
        )
        if not inv:
            return True
        exp_raw = inv.get("expiry_date")
        if not exp_raw:
            return True
        try:
            if isinstance(exp_raw, str):
                exp_dt = datetime.fromisoformat(exp_raw.replace("/", "-"))
            elif isinstance(exp_raw, datetime):
                exp_dt = exp_raw
            else:
                return True
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
            return exp_dt > datetime.now(tz=timezone.utc)
        except (ValueError, TypeError):
            return True

    @staticmethod
    def _urgency_rank(risk_level: str) -> int:
        return {"critical": 1, "high": 2, "medium": 3, "low": 4}.get(risk_level, 4)
