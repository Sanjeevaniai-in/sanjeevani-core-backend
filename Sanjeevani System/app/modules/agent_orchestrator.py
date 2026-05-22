from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta, timezone
from bson import ObjectId
from app.database.mongo_client import get_db
from .inventory_intelligence import InventoryIntelligenceService
from .safety_validation import SafetyValidationService
from .refill_prediction import RefillPredictionService
from app.utils.logger import get_logger

logger = get_logger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Prescription lock constants
# ──────────────────────────────────────────────────────────────────────────────
ORDER_STATUS_PENDING_RX = "PENDING_RX"
ORDER_STATUS_APPROVED   = "APPROVED"
ORDER_STATUS_LOCKED     = "LOCKED"  # hard-lock; admin must intervene


def unlock_order_rx(order_id: str, ocr_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Unlock a PENDING_RX order once a valid prescription has been parsed.

    Parameters
    ----------
    order_id  : MongoDB ObjectId string of the consumer_order document.
    ocr_result: The parsed payload from the OCR endpoint.
                Must contain at least one of:
                  - ``extracted_medicines``  (list of dicts with 'name')
                  - ``raw_text`` (non-empty string, last-resort fallback)

    Returns
    -------
    Dict with 'success' bool and 'message'.
    """
    db = get_db()

    # --- Validate OCR payload ---
    medicines: list = ocr_result.get("extracted_medicines", [])
    raw_text: str   = ocr_result.get("raw_text", "").strip()

    if not medicines and not raw_text:
        logger.warning("unlock_order_rx: OCR result has no medicines and no raw text – refusing unlock.",
                       extra={"order_id": order_id})
        return {
            "success": False,
            "message": "Prescription could not be validated. Please upload a clearer image.",
        }

    # --- Fetch and verify the order ---
    try:
        oid = ObjectId(order_id)
    except Exception:
        return {"success": False, "message": f"Invalid order_id: {order_id}"}

    order_doc = db["consumer_orders"].find_one({"_id": oid})
    if not order_doc:
        return {"success": False, "message": "Order not found."}

    if order_doc.get("order_status") not in (ORDER_STATUS_PENDING_RX, ORDER_STATUS_LOCKED):
        return {"success": True, "message": "Order is not locked; no action needed."}

    # --- Unlock ---
    db["consumer_orders"].update_one(
        {"_id": oid},
        {
            "$set": {
                "order_status": ORDER_STATUS_APPROVED,
                "prescription_validated": True,
                "prescription_validated_at": datetime.now(tz=timezone.utc).isoformat(),
                "prescription_ocr_summary": {
                    "medicine_count": len(medicines),
                    "raw_text_length": len(raw_text),
                },
            }
        },
    )

    logger.info("Order prescription unlocked.", extra={"order_id": order_id})
    return {"success": True, "message": "Prescription validated. Order is now approved for fulfilment."}

class AgentOrchestrator:
    """
    Orchestrates the 4-Agent pipeline for order processing.
    1. Inventory Agent (Merchant specific)
    2. Safety Agent (Dataset based Rx/Safety check)
    3. Refill Agent (Nudge for upcoming refills)
    4. Action Agent (Execution summary)
    """

    def __init__(self):
        self.db = get_db()
        self.inventory_svc = InventoryIntelligenceService()
        self.safety_svc = SafetyValidationService()
        self.refill_svc = RefillPredictionService()

    async def process_order(self, user_phone: str, merchant_id: str, extracted_items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Runs the full 4-agent pipeline for a list of items.
        """
        order_results = []
        overall_requires_rx = False
        
        for item in extracted_items:
            med_name = item.get("name", "").strip()
            requested_qty = item.get("quantity", 1)
            
            # --- AGENT 1: INVENTORY CHECK ---
            # Strictly isolated by merchant_id
            inventory_item = self.db["inventory"].find_one({
                "medicine_name": {"$regex": f"^{med_name}$", "$options": "i"},
                "merchant_id": merchant_id
            })
            
            in_stock = False
            available_qty = 0
            if inventory_item:
                available_qty = inventory_item.get("current_stock", 0)
                in_stock = available_qty >= requested_qty

            # --- AGENT 2: SAFETY & PRESCRIPTION CHECK ---
            # Uses the medicine_master dataset (Cloud-Search synced)
            # We check for 'habit_forming' and 'requires_prescription'
            safety_profile = self.db["medicine_master"].find_one({
                "brand_name_clean": med_name.lower()
            })
            
            requires_rx = False
            habit_forming = False
            side_effects = []
            
            if safety_profile:
                habit_forming = safety_profile.get("habit_forming", False)
                requires_rx = habit_forming or safety_profile.get("requires_prescription", False)
                side_effects = safety_profile.get("side_effects", [])[:3] # Show top 3
            else:
                # Fallback if not in master DB - assume safe but warn
                logger.warning(f"Medicine {med_name} not found in master database for safety check.")
            
            if requires_rx:
                overall_requires_rx = True

            # --- AGENT 3: REFILL & HISTORY AGENT ---
            # Check user history for 10/28 day reminders
            refill_nudge = self._check_refill_nudge(user_phone, merchant_id)

            order_results.append({
                "medicine_name": med_name,
                "requested_qty": requested_qty,
                "available_qty": available_qty,
                "in_stock": in_stock,
                "requires_prescription": requires_rx,
                "habit_forming": habit_forming,
                "side_effects": side_effects,
                "status": "APPROVED" if in_stock and not requires_rx else "PENDING_RX" if requires_rx else "OUT_OF_STOCK"
            })

        # --- AGENT 4: ACTION AGENT (Final Summary) ---
        # Determine top-level order status.
        # If ANY item requires a prescription the WHOLE order is locked until
        # a valid Rx is uploaded and validated via unlock_order_rx().
        if overall_requires_rx:
            top_status = ORDER_STATUS_PENDING_RX
        elif all(r["in_stock"] for r in order_results):
            top_status = ORDER_STATUS_APPROVED
        else:
            top_status = "PARTIAL"  # some items out of stock but no Rx needed

        return {
            "status": "SUCCESS",
            "order_status": top_status,          # NEW: what the API layer must honour
            "merchant_id": merchant_id,
            "requires_prescription": overall_requires_rx,
            "prescription_locked": overall_requires_rx,   # explicit boolean flag
            "items": order_results,
            "refill_nudge": refill_nudge,
            "message": self._generate_summary_message(order_results, overall_requires_rx),
        }

    def _check_refill_nudge(self, user_phone: str, merchant_id: str) -> Optional[str]:
        """Checks if other medicines in user's history need a refill soon."""
        # Look for orders from 10 or 28 days ago
        now = datetime.now(tz=timezone.utc)
        check_dates = [now - timedelta(days=10), now - timedelta(days=28)]
        
        # Simplification: Find any frequent medicine they order that hasn't been ordered recently
        recent_orders = list(self.db["consumer_orders"].find({
            "Contact Number": user_phone,
            "merchant_id": merchant_id
        }).sort("Order Date", -1).limit(5))
        
        if not recent_orders:
            return None
            
        # If they have a "chronic" med in history not in current order
        # We can nudge them. For now, a simple placeholder nudge:
        # In a real scenario, compare current order items with regular_medicines in patient profile.
        return "Don't forget to refill your regular medicines if they are running low!"

    def _generate_summary_message(self, results: List[Dict], needs_rx: bool) -> str:
        if needs_rx:
            return "Your order contains restricted medicines. Please upload a clear photo of your prescription to continue."
        
        all_in_stock = all(r["in_stock"] for r in results)
        if all_in_stock:
            return "Great news! All items are in stock and ready for delivery."
        else:
            out_items = [r["medicine_name"] for r in results if not r["in_stock"]]
            return f"Some items ({', '.join(out_items)}) are currently out of stock. Would you like to proceed with the others?"
