"""
app/modules/patient_context.py
─────────────────────────────────────────────────────────────────────────────
Statistical / rule-based patient context service.
No ML or LLM — pure aggregation over ``consumer_orders``.

Public API
──────────
    from app.modules.patient_context import PatientContextService
    svc = PatientContextService()
    profile   = svc.get_patient_profile("P001")
    risk      = svc.generate_refill_risk_score("P001", "Metformin 500mg")
    adherence = svc.get_adherence_pattern("P001")
"""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.database.mongo_client import get_db
from app.utils.logger import get_logger

logger = get_logger(__name__)


class PatientContextService:
    """All methods are stateless read-only operations over MongoDB."""

    def __init__(self) -> None:
        self._db = None

    @property
    def db(self):
        if self._db is None:
            self._db = get_db()
        return self._db

    # ──────────────────────────────────────────────────────────────────────
    # 1. get_patient_profile
    # ──────────────────────────────────────────────────────────────────────

    def get_patient_profile(self, patient_id: str) -> Optional[Dict[str, Any]]:
        """
        Return the patient document enriched with derived stats.

        Returns ``None`` when the patient is not found.
        """
        coll = self.db["patients"]
        patient = coll.find_one({"patient_id": patient_id})
        if not patient:
            # Fallback: try to build a lightweight profile from orders
            patient = self._build_profile_from_orders(patient_id)
            if not patient:
                logger.warning("Patient not found", extra={"patient_id": patient_id})
                return None

        patient.pop("_id", None)

        # Attach live stats
        patient["adherence_pattern"] = self.get_adherence_pattern(patient_id)
        patient["active_medicines"] = self._get_active_medicines(patient_id)
        return patient

    def _build_profile_from_orders(self, patient_id: str) -> Optional[Dict[str, Any]]:
        """Lightweight fallback: build a minimal profile from order history."""
        orders = list(
            self.db["consumer_orders"]
            .find({"$or": [{"Patient ID": patient_id}, {"Patient Name": patient_id}]})
            .sort("Order Date", -1)
            .limit(200)
        )
        if not orders:
            return None
        first = orders[0]
        medicines = list(
            {o.get("Medicine Name") for o in orders if o.get("Medicine Name")}
        )
        return {
            "patient_id": patient_id,
            "name": first.get("Patient Name", patient_id),
            "age": first.get("Age"),
            "gender": first.get("Gender"),
            "contact_number": first.get("Contact Number"),
            "regular_medicines": medicines,
            "total_orders": len(orders),
        }

    # ──────────────────────────────────────────────────────────────────────
    # 2. calculate_usage_frequency
    # ──────────────────────────────────────────────────────────────────────

    def calculate_usage_frequency(
        self, patient_id: str, product_id: str
    ) -> Dict[str, Any]:
        """
        Return monthly purchase frequency for a given product.

        Result keys
        ───────────
        ``orders_total``      – total orders found
        ``months_active``     – span of activity in months
        ``orders_per_month``  – average orders per calendar month
        ``dates``             – sorted list of purchase dates (ISO strings)
        """
        orders = self._get_patient_product_orders(patient_id, product_id)
        if not orders:
            return {
                "orders_total": 0,
                "months_active": 0,
                "orders_per_month": 0.0,
                "dates": [],
            }

        dates = self._extract_dates(orders)
        if len(dates) < 2:
            return {
                "orders_total": len(orders),
                "months_active": 1,
                "orders_per_month": float(len(orders)),
                "dates": [d.isoformat() for d in dates],
            }

        span_days = (dates[-1] - dates[0]).days or 1
        months_active = max(span_days / 30.44, 1)
        return {
            "orders_total": len(orders),
            "months_active": round(months_active, 2),
            "orders_per_month": round(len(orders) / months_active, 3),
            "dates": [d.isoformat() for d in dates],
        }

    # ──────────────────────────────────────────────────────────────────────
    # 3. estimate_daily_consumption
    # ──────────────────────────────────────────────────────────────────────

    def estimate_daily_consumption(
        self, patient_id: str, product_id: str
    ) -> Dict[str, Any]:
        """
        Estimate units consumed per day based on quantity ordered and
        the interval between consecutive orders.

        Returns
        ───────
        ``daily_consumption``  – estimated units per day
        ``avg_qty_per_order``  – average quantity per order
        ``avg_interval_days``  – average days between orders
        """
        orders = self._get_patient_product_orders(patient_id, product_id)
        if not orders:
            return {
                "daily_consumption": 0.0,
                "avg_qty_per_order": 0.0,
                "avg_interval_days": 0.0,
            }

        quantities: List[float] = []
        for o in orders:
            q = o.get("Quantity Ordered") or o.get("Quantity")
            if q is not None:
                try:
                    quantities.append(float(q))
                except (TypeError, ValueError):
                    pass

        avg_qty = statistics.mean(quantities) if quantities else 0.0

        dates = self._extract_dates(orders)
        intervals = self._intervals_days(dates)
        avg_interval = statistics.mean(intervals) if intervals else 30.0

        daily = avg_qty / avg_interval if avg_interval > 0 else 0.0
        return {
            "daily_consumption": round(daily, 4),
            "avg_qty_per_order": round(avg_qty, 2),
            "avg_interval_days": round(avg_interval, 2),
        }

    # ──────────────────────────────────────────────────────────────────────
    # 4. calculate_days_remaining
    # ──────────────────────────────────────────────────────────────────────

    def calculate_days_remaining(
        self, patient_id: str, product_id: str
    ) -> Dict[str, Any]:
        """
        Estimate how many days of medicine remain based on the most
        recent order quantity and estimated daily consumption.

        Returns
        ───────
        ``days_remaining``      – estimated days left  (-1 if unknown)
        ``last_order_date``     – ISO date of last order
        ``last_quantity``       – quantity in last order
        ``daily_consumption``   – units/day estimate
        """
        orders = self._get_patient_product_orders(patient_id, product_id)
        if not orders:
            return {
                "days_remaining": -1,
                "last_order_date": None,
                "last_quantity": None,
                "daily_consumption": 0.0,
            }

        dated = [o for o in orders if self._get_date(o)]
        if not dated:
            return {
                "days_remaining": -1,
                "last_order_date": None,
                "last_quantity": None,
                "daily_consumption": 0.0,
            }

        dated.sort(key=lambda o: self._get_date(o))
        last_order = dated[-1]
        last_date = self._get_date(last_order)
        last_qty = last_order.get("Quantity Ordered") or last_order.get("Quantity") or 0

        consumption = self.estimate_daily_consumption(patient_id, product_id)
        daily = consumption["daily_consumption"]

        if daily <= 0:
            # Default: assume 30-day supply per order
            days_rem = (
                30
                - (
                    datetime.now(tz=timezone.utc)
                    - last_date.replace(tzinfo=timezone.utc)
                ).days
            )
        else:
            days_from_last_order = (
                datetime.now(tz=timezone.utc) - last_date.replace(tzinfo=timezone.utc)
            ).days
            days_rem = float(last_qty) / daily - days_from_last_order

        return {
            "days_remaining": round(max(days_rem, 0), 1),
            "last_order_date": last_date.isoformat(),
            "last_quantity": float(last_qty) if last_qty else None,
            "daily_consumption": daily,
        }

    # ──────────────────────────────────────────────────────────────────────
    # 5. generate_refill_risk_score
    # ──────────────────────────────────────────────────────────────────────

    def generate_refill_risk_score(
        self, patient_id: str, product_id: str
    ) -> Dict[str, Any]:
        """
        Produce a 0–100 refill risk score (higher → more urgent).

        Scoring components
        ──────────────────
        • Days remaining   (0–40 pts): <7 d → 40, <14 d → 30, <30 d → 20, else 0
        • Chronic status   (0–20 pts): Is Chronic == "Yes" → 20
        • Adherence gap    (0–20 pts): missed last refill window → up to 20
        • Order regularity (0–20 pts): high variability in intervals → higher risk
        """
        days_info = self.calculate_days_remaining(patient_id, product_id)
        days_rem = days_info.get("days_remaining", 30)
        adherence = self.get_adherence_pattern(patient_id)

        score = 0
        explanation_parts: List[str] = []

        # Component 1: Days remaining
        if days_rem <= 7:
            score += 40
            explanation_parts.append(f"Only {days_rem}d supply left (critical)")
        elif days_rem <= 14:
            score += 30
            explanation_parts.append(f"Only {days_rem}d supply left (urgent)")
        elif days_rem <= 30:
            score += 20
            explanation_parts.append(f"{days_rem}d supply left (moderate)")
        else:
            explanation_parts.append(f"{days_rem}d supply remaining (low risk)")

        # Component 2: Chronic status
        orders = self._get_patient_product_orders(patient_id, product_id)
        is_chronic = any(
            str(o.get("Is Chronic", "")).strip().lower() == "yes" for o in orders
        )
        if is_chronic:
            score += 20
            explanation_parts.append("Chronic medication (+20)")

        # Component 3: Adherence gap
        adherence_rate = adherence.get("adherence_rate", 1.0)
        if adherence_rate < 0.5:
            score += 20
            explanation_parts.append("Low adherence rate <50% (+20)")
        elif adherence_rate < 0.75:
            score += 10
            explanation_parts.append("Moderate adherence gap (+10)")

        # Component 4: Order interval variability
        intervals = self._intervals_days(self._extract_dates(orders) if orders else [])
        if len(intervals) >= 2:
            cv = statistics.stdev(intervals) / (statistics.mean(intervals) or 1)
            if cv > 0.5:
                score += 20
                explanation_parts.append("High interval variability (+20)")
            elif cv > 0.25:
                score += 10
                explanation_parts.append("Moderate interval variability (+10)")

        score = min(score, 100)
        return {
            "patient_id": patient_id,
            "product_id": product_id,
            "risk_score": score,
            "risk_level": self._risk_level(score),
            "explanation": "; ".join(explanation_parts),
            "days_remaining": days_rem,
            "is_chronic": is_chronic,
            "adherence_rate": adherence_rate,
        }

    # ──────────────────────────────────────────────────────────────────────
    # 6. get_adherence_pattern
    # ──────────────────────────────────────────────────────────────────────

    def get_adherence_pattern(self, patient_id: str) -> Dict[str, Any]:
        """
        Assess how consistently a patient collects refills on time.

        Approach
        ────────
        For each medicine, check whether consecutive order gaps are within
        125% of the expected interval.  Average across all medicines.

        Returns
        ───────
        ``adherence_rate``     – 0.0 – 1.0
        ``on_time_count``      – refills collected on time
        ``late_count``         – refills collected late
        ``missed_count``       – estimated missed refills
        ``medicines_checked``  – list of medicine names analysed
        """
        orders = list(
            self.db["consumer_orders"]
            .find(
                {"$or": [{"Patient ID": patient_id}, {"Patient Name": patient_id}]},
                {
                    "Medicine Name": 1,
                    "Order Date": 1,
                    "Refill Due Date": 1,
                    "Order Status": 1,
                },
            )
            .sort("Order Date", 1)
        )
        if not orders:
            return {
                "adherence_rate": 1.0,
                "on_time_count": 0,
                "late_count": 0,
                "missed_count": 0,
                "medicines_checked": [],
            }

        medicines = list(
            {o.get("Medicine Name") for o in orders if o.get("Medicine Name")}
        )
        on_time = late = missed = 0

        for med in medicines:
            med_orders = [o for o in orders if o.get("Medicine Name") == med]
            dates = self._extract_dates(med_orders)
            intervals = self._intervals_days(dates)
            if not intervals:
                continue
            expected = statistics.mean(intervals)
            for iv in intervals:
                if iv <= expected * 1.10:
                    on_time += 1
                elif iv <= expected * 1.25:
                    late += 1
                else:
                    missed += 1

        total = on_time + late + missed or 1
        return {
            "adherence_rate": round(on_time / total, 4),
            "on_time_count": on_time,
            "late_count": late,
            "missed_count": missed,
            "medicines_checked": medicines,
        }

    # ──────────────────────────────────────────────────────────────────────
    # Private helpers
    # ──────────────────────────────────────────────────────────────────────

    def _get_patient_product_orders(
        self, patient_id: str, product_id: str
    ) -> List[Dict[str, Any]]:
        """Fetch all orders for a patient+product combination."""
        return list(
            self.db["consumer_orders"]
            .find(
                {
                    "$or": [{"Patient ID": patient_id}, {"Patient Name": patient_id}],
                    "$or": [{"Medicine Name": product_id}, {"Product ID": product_id}],
                }
            )
            .sort("Order Date", 1)
        )

    def _get_active_medicines(self, patient_id: str) -> List[str]:
        """Return distinct medicine names from last 6 months of orders."""
        since = datetime.now(tz=timezone.utc) - timedelta(days=180)
        pipeline = [
            {
                "$match": {
                    "$or": [{"Patient ID": patient_id}, {"Patient Name": patient_id}],
                    "Order Date": {"$gte": since},
                }
            },
            {"$group": {"_id": "$Medicine Name"}},
        ]
        return [
            r["_id"] for r in self.db["consumer_orders"].aggregate(pipeline) if r["_id"]
        ]

    @staticmethod
    def _get_date(order: Dict[str, Any]) -> Optional[datetime]:
        raw = order.get("Order Date") or order.get("Purchase Date") or order.get("Date")
        if isinstance(raw, datetime):
            return raw
        if isinstance(raw, str):
            try:
                return datetime.fromisoformat(raw)
            except ValueError:
                return None
        return None

    @staticmethod
    def _extract_dates(orders: List[Dict[str, Any]]) -> List[datetime]:
        dates = []
        for o in orders:
            raw = o.get("Order Date") or o.get("Purchase Date") or o.get("Date")
            if isinstance(raw, datetime):
                dates.append(raw)
            elif isinstance(raw, str):
                try:
                    dates.append(datetime.fromisoformat(raw))
                except ValueError:
                    pass
        return sorted(dates)

    @staticmethod
    def _intervals_days(dates: List[datetime]) -> List[float]:
        if len(dates) < 2:
            return []
        return [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]

    @staticmethod
    def _risk_level(score: int) -> str:
        if score >= 75:
            return "critical"
        if score >= 50:
            return "high"
        if score >= 25:
            return "medium"
        return "low"
