from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import requests as req

from app.database.mongo_client import get_db
from app.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class RefillCandidate:
    patient_name: str
    contact_number: str
    medicine_name: str
    days_since_last_order: int
    language: str = "english"
    patient_id: Optional[str] = None
    merchant_id: Optional[str] = None


class RefillOutreachService:
    """Create refill alerts and send outreach over WhatsApp + in-app notification."""

    def __init__(self) -> None:
        self.db = get_db()

    def run_demo_outreach(
        self,
        *,
        merchant_id: str,
        demo_file_path: Optional[str] = None,
    ) -> dict[str, Any]:
        records = self._load_demo_records(demo_file_path)
        candidates = [
            RefillCandidate(
                patient_name=str(r.get("patient_name") or "Customer"),
                contact_number=str(r.get("contact_number") or ""),
                medicine_name=str(r.get("medicine_name") or "Medicine"),
                days_since_last_order=int(r.get("days_since_last_order") or 10),
                language=str(r.get("language") or "english"),
                patient_id=r.get("patient_id"),
                merchant_id=merchant_id,
            )
            for r in records
        ]
        return self._dispatch_candidates(candidates=candidates, merchant_id=merchant_id, source="demo_file")

    def run_live_outreach(
        self,
        *,
        merchant_id: str,
        reminder_days: Iterable[int] = (10, 28),
    ) -> dict[str, Any]:
        now = datetime.now(tz=timezone.utc)
        orders_coll = self.db["consumer_orders"]
        candidates: list[RefillCandidate] = []
        seen: set[tuple[str, str]] = set()

        for days in reminder_days:
            target_start = (now - timedelta(days=int(days))).replace(hour=0, minute=0, second=0, microsecond=0)
            target_end = target_start + timedelta(days=1)
            old_orders = list(
                orders_coll.find(
                    {
                        "merchant_id": merchant_id,
                        "Order Date": {"$gte": target_start, "$lt": target_end},
                        "Order Status": {"$nin": ["Cancelled", "Rejected"]},
                    }
                )
            )

            for order in old_orders:
                contact = str(order.get("Contact Number") or "").strip()
                medicine = str(order.get("Medicine Name") or "").strip()
                patient_name = str(order.get("Patient Name") or "Customer").strip()
                if not contact or not medicine:
                    continue
                dedupe_key = (contact, medicine.lower())
                if dedupe_key in seen:
                    continue

                reordered = orders_coll.find_one(
                    {
                        "merchant_id": merchant_id,
                        "Contact Number": contact,
                        "Medicine Name": {"$regex": f"^{medicine}$", "$options": "i"},
                        "Order Date": {"$gt": target_end},
                        "Order Status": {"$nin": ["Cancelled", "Rejected"]},
                    },
                    {"Order ID": 1},
                )
                if reordered:
                    continue

                seen.add(dedupe_key)
                candidates.append(
                    RefillCandidate(
                        patient_name=patient_name,
                        contact_number=contact,
                        medicine_name=medicine,
                        days_since_last_order=int(days),
                        language=self._resolve_patient_language(contact_number=contact, patient_name=patient_name),
                        patient_id=order.get("Patient ID"),
                        merchant_id=merchant_id,
                    )
                )

        return self._dispatch_candidates(candidates=candidates, merchant_id=merchant_id, source="live_orders")

    def _dispatch_candidates(
        self,
        *,
        candidates: list[RefillCandidate],
        merchant_id: str,
        source: str,
    ) -> dict[str, Any]:
        sent_whatsapp = 0
        created_app_notifications = 0
        alerts_upserted = 0

        for candidate in candidates:
            message = self._build_refill_message(
                language=candidate.language,
                patient_name=candidate.patient_name,
                medicine_name=candidate.medicine_name,
                days_since=candidate.days_since_last_order,
            )

            if self._send_whatsapp(candidate.contact_number, message):
                sent_whatsapp += 1

            self._create_app_notification(
                merchant_id=merchant_id,
                candidate=candidate,
                message=message,
            )
            created_app_notifications += 1

            self._upsert_refill_alert(
                merchant_id=merchant_id,
                candidate=candidate,
                message=message,
            )
            alerts_upserted += 1

        return {
            "status": "ok",
            "source": source,
            "total_candidates": len(candidates),
            "whatsapp_sent": sent_whatsapp,
            "app_notifications_created": created_app_notifications,
            "refill_alerts_upserted": alerts_upserted,
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        }

    def _build_refill_message(
        self,
        *,
        language: str,
        patient_name: str,
        medicine_name: str,
        days_since: int,
    ) -> str:
        lang = (language or "english").strip().lower()
        if "hind" in lang:
            return (
                f"Namaste {patient_name}, aapki {medicine_name} ko {days_since} din ho gaye hain. "
                "Refill khatam ho sakta hai. Refill order karna hai to reply karein."
            )
        if "mara" in lang:
            return (
                f"Namaskar {patient_name}, tumchya {medicine_name} la {days_since} divas zale. "
                "Refill sampat asel. Order karaycha asel tar reply kara."
            )
        return (
            f"Hi {patient_name}, it has been {days_since} days since your {medicine_name} order. "
            "Your refill may be running out. Reply to place your refill."
        )

    def _send_whatsapp(self, contact_number: str, message: str) -> bool:
        token = os.getenv("WHATSAPP_TOKEN", "").strip()
        phone_number_id = os.getenv("PHONE_NUMBER_ID", "").strip()
        contact = str(contact_number or "").strip().replace("whatsapp:", "")
        if not token or not phone_number_id or not contact:
            return False

        url = f"https://graph.facebook.com/v19.0/{phone_number_id}/messages"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        payload = {"messaging_product": "whatsapp", "to": contact, "text": {"body": message}}

        try:
            resp = req.post(url, headers=headers, json=payload, timeout=10)
            if resp.status_code in (200, 201):
                return True
            logger.warning(f"Refill WhatsApp send failed [{resp.status_code}]: {resp.text}")
            return False
        except Exception as exc:
            logger.error(f"Refill WhatsApp send exception: {exc}")
            return False

    def _create_app_notification(
        self,
        *,
        merchant_id: str,
        candidate: RefillCandidate,
        message: str,
    ) -> None:
        now = datetime.now(tz=timezone.utc)
        self.db["app_notifications"].update_one(
            {
                "merchant_id": merchant_id,
                "contact_number": candidate.contact_number,
                "medicine_name": candidate.medicine_name,
                "type": "refill_due",
                "is_read": False,
            },
            {
                "$set": {
                    "merchant_id": merchant_id,
                    "type": "refill_due",
                    "title": "Refill Reminder",
                    "message": message,
                    "patient_name": candidate.patient_name,
                    "patient_id": candidate.patient_id,
                    "contact_number": candidate.contact_number,
                    "medicine_name": candidate.medicine_name,
                    "language": candidate.language,
                    "is_read": False,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )

    def _upsert_refill_alert(
        self,
        *,
        merchant_id: str,
        candidate: RefillCandidate,
        message: str,
    ) -> None:
        now = datetime.now(tz=timezone.utc)
        self.db["alerts"].update_one(
            {
                "merchant_id": merchant_id,
                "alert_type": "refill_due",
                "patient_id": candidate.patient_id or candidate.patient_name,
                "medicine_name": candidate.medicine_name,
                "is_resolved": False,
            },
            {
                "$set": {
                    "merchant_id": merchant_id,
                    "alert_type": "refill_due",
                    "severity": "high" if candidate.days_since_last_order >= 28 else "medium",
                    "title": f"Refill reminder for {candidate.patient_name}",
                    "message": message,
                    "patient_id": candidate.patient_id or candidate.patient_name,
                    "medicine_name": candidate.medicine_name,
                    "channel": "whatsapp+app",
                    "auto_actioned": True,
                    "is_resolved": False,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )

    def _resolve_patient_language(self, *, contact_number: str, patient_name: str) -> str:
        patient_doc = self.db["patients"].find_one(
            {
                "$or": [
                    {"contact_number": contact_number},
                    {"name": {"$regex": f"^{patient_name}$", "$options": "i"}},
                ]
            },
            {"language": 1},
        )
        if patient_doc and patient_doc.get("language"):
            return str(patient_doc["language"])
        return "english"

    def _load_demo_records(self, demo_file_path: Optional[str]) -> list[dict[str, Any]]:
        default_path = Path(__file__).resolve().parents[2] / "scripts" / "refill_demo_notifications.json"
        file_path = Path(demo_file_path) if demo_file_path else default_path
        if not file_path.exists():
            logger.warning(f"Refill demo file not found at {file_path}")
            return []

        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
            logger.warning("Refill demo file is not a list. Ignoring.")
            return []
        except Exception as exc:
            logger.error(f"Failed to parse refill demo file: {exc}")
            return []
