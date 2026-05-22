"""
app/modules/safety_validation.py
─────────────────────────────────────────────────────────────────────────────
Order safety validation service — rule-based, no ML.

Rules enforced
──────────────
1. Prescription check    – flag if Rx required and not flagged as provided.
2. Expiry check          – reject orders for expired products.
3. Availability check    – reject if stock < requested quantity.
4. Duplicate-recent check – warn if same product ordered within *days* window.
5. Quantity validation   – flag unusual quantities (>3× patient's historical avg).

Public API
──────────
    from app.modules.safety_validation import SafetyValidationService
    svc = SafetyValidationService()
    result = svc.validate_order("P001", "Metformin 500mg", 60)
"""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.database.mongo_client import get_db
from app.utils.logger import get_logger
from app.utils.ocr_service import extract_text_from_image, verify_prescription_with_llm

logger = get_logger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Validation result helpers
# ──────────────────────────────────────────────────────────────────────────────


def _ok(check: str, message: str = "") -> Dict[str, Any]:
    return {"check": check, "passed": True, "message": message or f"{check}: OK"}


def _fail(check: str, message: str, severity: str = "error") -> Dict[str, Any]:
    return {"check": check, "passed": False, "severity": severity, "message": message}


def _warn(check: str, message: str) -> Dict[str, Any]:
    return {"check": check, "passed": True, "severity": "warning", "message": message}


class SafetyValidationService:
    """Rule-based order safety validator."""

    def __init__(self) -> None:
        self._db = None

    @property
    def db(self):
        if self._db is None:
            self._db = get_db()
        return self._db

    # ──────────────────────────────────────────────────────────────────────
    # 1. validate_order  (orchestrator)
    # ──────────────────────────────────────────────────────────────────────

    def validate_order(
        self,
        patient_id: str,
        product_id: str,
        quantity: float,
        merchant_id: str,
        *,
        prescription_provided: bool = False,
    ) -> Dict[str, Any]:
        """
        Run all safety checks and return a combined validation result.

        Returns
        ───────
        ``is_valid``   – False if any error-level check failed
        ``can_process``– True when order may proceed (no blocking failures)
        ``checks``     – list of individual check results
        ``summary``    – human-readable overall message
        """
        checks: List[Dict[str, Any]] = []

        rx_check = self.check_prescription_required(
            product_id, merchant_id, provided=prescription_provided
        )
        expiry_check = self.check_expiry(product_id, merchant_id)
        avail_check = self.check_availability(product_id, quantity, merchant_id)
        duplicate_chk = self.check_duplicate_recent(patient_id, product_id, merchant_id)
        qty_check = self.validate_quantity(patient_id, product_id, quantity, merchant_id)

        checks.extend([rx_check, expiry_check, avail_check, duplicate_chk, qty_check])

        # Blocking failures: expiry + availability are hard blocks
        blocking_failures = [
            c for c in checks if not c["passed"] and c.get("severity") == "error"
        ]
        warnings = [c for c in checks if c.get("severity") == "warning"]

        is_valid = len(blocking_failures) == 0
        can_process = is_valid

        summary_parts = [c["message"] for c in blocking_failures + warnings]
        summary = (
            "Order is safe to process."
            if is_valid and not warnings
            else (
                "Order blocked: " + "; ".join(c["message"] for c in blocking_failures)
                if blocking_failures
                else "Order valid with warnings: "
                + "; ".join(c["message"] for c in warnings)
            )
        )

        logger.info(
            "Order validation",
            extra={
                "patient_id": patient_id,
                "product_id": product_id,
                "quantity": quantity,
                "is_valid": is_valid,
            },
        )
        return {
            "is_valid": is_valid,
            "can_process": can_process,
            "checks": checks,
            "summary": summary,
            "validated_at": datetime.now(tz=timezone.utc).isoformat(),
        }

    # ──────────────────────────────────────────────────────────────────────
    # 2. check_prescription_required
    # ──────────────────────────────────────────────────────────────────────

    def check_prescription_required(
        self, product_id: str, merchant_id: str, *, provided: bool = False
    ) -> Dict[str, Any]:
        """
        Check whether *product_id* requires a prescription.
        NOW: Also checks master medicine database for 'Habit Forming' or 'Prescription' status.
        """
        product = self._get_product(product_id, merchant_id)
        
        # Check Master Dataset for extra safety
        master_data = self.db["medicine_master"].find_one({
            "$or": [{"brand_name": {"$regex": f"^{product_id}$", "$options": "i"}}, {"product_id": product_id}]
        })
        
        requires = False
        if product:
            req_raw = product.get("Requires Prescription") or product.get("Prescription Required") or ""
            requires = str(req_raw).strip().lower() in ("yes", "true", "1", "y")
        
        habit_forming = False
        if master_data:
            habit_raw = master_data.get("Habit Forming") or ""
            habit_forming = str(habit_raw).strip().lower() in ("yes", "true", "1", "y")
            if habit_forming:
                requires = True # Force prescription for habit forming drugs

        if requires and not provided:
            msg = f"'{product_id}' requires a prescription"
            if habit_forming:
                msg += " (Warning: Habit Forming medication)"
            return _warn("prescription", msg + " — confirm with pharmacist.")
            
        return _ok("prescription", "Prescription check passed.")

    def validate_medicine(self, medicine_name: str) -> Dict[str, Any]:
        """
        Quick check for a medicine's safety profile by name.
        Used by the chatbot for real-time alerts.
        """
        db = self.db
        master_data = db["medicine_master"].find_one({
            "brand_name": {"$regex": f"^{medicine_name}$", "$options": "i"}
        })
        
        if not master_data:
            return {"is_habit_forming": False, "requires_prescription": False}
            
        habit_raw = master_data.get("Habit Forming") or ""
        is_habit = str(habit_raw).strip().lower() in ("yes", "true", "1", "y")
        
        # Prescription status from master DB
        type_raw = master_data.get("Type") or ""
        requires_rx = is_habit or "prescription" in str(type_raw).lower()
        
        return {
            "medicine_name": medicine_name,
            "is_habit_forming": is_habit,
            "requires_prescription": requires_rx,
            "therapeutic_class": master_data.get("Therapeutic Class"),
            "action_class": master_data.get("Action Class")
        }

    # ──────────────────────────────────────────────────────────────────────
    # NEW: process_prescription_file
    # ──────────────────────────────────────────────────────────────────────

    async def process_prescription_file(self, file_path: str, llm_client) -> Dict[str, Any]:
        """
        Process an uploaded prescription: OCR -> LLM Extract -> In-DB Match.
        """
        text = extract_text_from_image(file_path)
        if not text:
            return {"success": False, "error": "OCR failed to extract text."}
            
        # Refine with LLM
        verification = await verify_prescription_with_llm(text, [], llm_client)
        
        if not verification.get("is_valid_prescription"):
            return {
                "success": False, 
                "error": "This does not look like a valid prescription.",
                "details": verification.get("warnings", [])
            }
            
        # Match medicines against master DB
        found_medicines = []
        for med in verification.get("medicines", []):
            name = med.get("name")
            master_entry = self.db["medicine_master"].find_one({"brand_name": {"$regex": f"^{name}$", "$options": "i"}})
            if master_entry:
                med["is_in_database"] = True
                med["therapeutic_class"] = master_entry.get("Therapeutic Class")
                med["habit_forming"] = master_entry.get("Habit Forming")
            else:
                med["is_in_database"] = False
            found_medicines.append(med)
            
        return {
            "success": True,
            "verification": verification,
            "matched_medicines": found_medicines,
            "doctor": verification.get("doctor_name")
        }

    # ──────────────────────────────────────────────────────────────────────
    # 3. check_expiry
    # ──────────────────────────────────────────────────────────────────────

    def check_expiry(self, product_id: str, merchant_id: str) -> Dict[str, Any]:
        """
        Return an error if the product is already expired.
        Return a warning if it expires within 30 days.
        """
        inventory = self._get_inventory(product_id, merchant_id)
        if not inventory:
            return _ok("expiry", "Expiry not checked — product not in inventory.")

        expiry_raw = inventory.get("expiry_date")
        if not expiry_raw:
            return _ok("expiry", "No expiry date recorded.")

        try:
            if isinstance(expiry_raw, str):
                exp_dt = datetime.fromisoformat(expiry_raw.replace("/", "-"))
            elif isinstance(expiry_raw, datetime):
                exp_dt = expiry_raw
            else:
                return _ok("expiry", "Cannot parse expiry date format.")

            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)

            now = datetime.now(tz=timezone.utc)
            if exp_dt <= now:
                return _fail(
                    "expiry",
                    f"Product expired on {expiry_raw} — cannot dispense.",
                    severity="error",
                )
            days_left = (exp_dt - now).days
            if days_left <= 30:
                return _warn(
                    "expiry",
                    f"Product expires in {days_left} days ({expiry_raw}) — advise patient.",
                )
            return _ok("expiry", f"Product valid until {expiry_raw}.")

        except (ValueError, TypeError):
            return _ok("expiry", "Could not parse expiry date.")

    # ──────────────────────────────────────────────────────────────────────
    # 4. check_availability
    # ──────────────────────────────────────────────────────────────────────

    def check_availability(self, product_id: str, quantity: float, merchant_id: str) -> Dict[str, Any]:
        """
        Hard fail if available stock < requested quantity.
        Warning if stock will drop below reorder level after this order.
        """
        inventory = self._get_inventory(product_id, merchant_id)
        if not inventory:
            return _warn(
                "availability", "Product not found in inventory — verify manually."
            )

        stock = float(inventory.get("current_stock") or 0)
        reorder = float(inventory.get("reorder_level") or 0)

        if stock < quantity:
            return _fail(
                "availability",
                f"Insufficient stock: {stock} units available, {quantity} requested.",
                severity="error",
            )
        if stock - quantity <= reorder:
            return _warn(
                "availability",
                f"Dispensing {quantity} units will bring stock to "
                f"{stock - quantity} (≤ reorder level {reorder}).",
            )
        return _ok("availability", f"Stock sufficient: {stock} units available.")

    # ──────────────────────────────────────────────────────────────────────
    # 5. check_duplicate_recent
    # ──────────────────────────────────────────────────────────────────────

    def check_duplicate_recent(
        self, patient_id: str, product_id: str, merchant_id: str, days: int = 30
    ) -> Dict[str, Any]:
        """
        Warn if the same patient ordered the same product within *days* days.

        For chronic patients this is expected; for acute products it may
        indicate dispensing error or misuse.
        """
        since = datetime.now(tz=timezone.utc) - timedelta(days=days)
        recent = self.db["consumer_orders"].find_one(
            {
                "$or": [{"Patient ID": patient_id}, {"Patient Name": patient_id}],
                "Medicine Name": product_id,
                "Order Date": {"$gte": since},
                "Order Status": {"$ne": "Cancelled"},
                "merchant_id": merchant_id,
            }
        )
        if recent:
            order_date = recent.get("Order Date")
            date_str = (
                order_date.strftime("%Y-%m-%d")
                if isinstance(order_date, datetime)
                else str(order_date)
            )
            return _warn(
                "duplicate_check",
                f"Patient already ordered '{product_id}' on {date_str} "
                f"(within {days} days) — confirm it is not a duplicate.",
            )
        return _ok(
            "duplicate_check", f"No recent duplicate order in the last {days} days."
        )

    # ──────────────────────────────────────────────────────────────────────
    # 6. validate_quantity
    # ──────────────────────────────────────────────────────────────────────

    def validate_quantity(
        self, patient_id: str, product_id: str, quantity: float, merchant_id: str
    ) -> Dict[str, Any]:
        """
        Flag unusual quantities (>3× patient's historical average for this product).

        If no history exists, accept the quantity without flagging.
        """
        if quantity <= 0:
            return _fail("quantity", "Quantity must be > 0.", severity="error")

        past_orders = list(
            self.db["consumer_orders"].find(
                {
                    "$or": [{"Patient ID": patient_id}, {"Patient Name": patient_id}],
                    "Medicine Name": product_id,
                    "merchant_id": merchant_id,
                },
                {"Quantity Ordered": 1, "Quantity": 1},
            )
        )
        qtys: List[float] = []
        for o in past_orders:
            q = o.get("Quantity Ordered") or o.get("Quantity")
            if q is not None:
                try:
                    qtys.append(float(q))
                except (TypeError, ValueError):
                    pass

        if not qtys:
            return _ok("quantity", "No order history — quantity accepted.")

        avg_qty = statistics.mean(qtys)
        threshold = avg_qty * 3.0

        if quantity > threshold:
            return _warn(
                "quantity",
                f"Quantity {quantity} is unusually high (avg: {avg_qty:.1f}, "
                f"threshold: {threshold:.1f}) — confirm with prescriber.",
            )
        return _ok("quantity", f"Quantity {quantity} is within normal range.")

    # ──────────────────────────────────────────────────────────────────────
    # 7. generate_safety_alerts
    # ──────────────────────────────────────────────────────────────────────

    def generate_safety_alerts(self, merchant_id: str) -> Dict[str, int]:
        """
        Scan all pending (undelivered) orders and create interaction_warning
        alerts for those that fail safety checks.

        Returns ``{"alerts_created": int}``.
        """
        from app.database.mongo_client import get_db

        db = get_db()
        now = datetime.now(tz=timezone.utc)
        count = 0

        pending_orders = list(
            db["consumer_orders"].find(
                {
                    "Order Status": {"$in": ["Pending", "Processing", None]},
                    "merchant_id": merchant_id,
                }
            )
        )

        for order in pending_orders:
            pid = order.get("Patient ID") or order.get("Patient Name") or ""
            med = order.get("Medicine Name") or ""
            qty = float(order.get("Quantity Ordered") or order.get("Quantity") or 1)

            if not pid or not med:
                continue

            result = self.validate_order(pid, med, qty, merchant_id=merchant_id)
            if not result["is_valid"]:
                alert = {
                    "alert_type": "interaction_warning",
                    "severity": "high",
                    "title": f"Safety Flag: {med} for patient {pid}",
                    "message": result["summary"],
                    "patient_id": pid,
                    "medicine_name": med,
                    "is_resolved": False,
                    "merchant_id": merchant_id,
                    "auto_actioned": True,
                    "created_at": now,
                    "updated_at": now,
                }
                db["alerts"].update_one(
                    {
                        "alert_type": "interaction_warning",
                        "patient_id": pid,
                        "medicine_name": med,
                        "is_resolved": False,
                        "merchant_id": merchant_id,
                    },
                    {"$set": alert},
                    upsert=True,
                )
                count += 1

        logger.info("Safety alerts generated", extra={"count": count})
        return {"alerts_created": count}

    # ──────────────────────────────────────────────────────────────────────
    # Private helpers
    # ──────────────────────────────────────────────────────────────────────

    def _get_product(self, product_id: str, merchant_id: str) -> Optional[Dict[str, Any]]:
        return self.db["products"].find_one(
            {
                "$and": [
                    {
                        "$or": [
                            {"Product ID": product_id},
                            {"Medicine Name": product_id},
                            {"Generic Name": product_id},
                        ]
                    },
                    {"merchant_id": merchant_id}
                ]
            }
        )

    def _get_inventory(self, product_id: str) -> Optional[Dict[str, Any]]:
        return self.db["inventory"].find_one(
            {
                "$or": [
                    {"product_id": product_id},
                    {"medicine_name": product_id},
                ]
            }
        )
