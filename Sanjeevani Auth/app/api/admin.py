"""
app/api/admin.py — /admin
Sanjeevani Super Admin endpoint.
Returns all registered pharmacies with their full profile + WhatsApp config status.
NO AUTH for now (temporary) — add JWT later.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db.mongo import get_db

router = APIRouter(prefix="/admin", tags=["Admin"])


# ─── Models ──────────────────────────────────────────────────────────────────

class WhatsAppSetupRequest(BaseModel):
    phone_number_id: str
    access_token: str
    display_number: str
    bot_name: Optional[str] = "Sanjeevani WhatsApp Bot"


class WhatsAppSetupResponse(BaseModel):
    status: str
    message: str
    pharmacy_id: str


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _mask_token(token: str) -> str:
    """Never expose full token — show last 6 chars only."""
    if not token or len(token) < 6:
        return "****"
    return f"****{token[-6:]}"


def _serialize_user(u: dict) -> dict:
    """Convert MongoDB user doc → clean JSON-serializable dict."""
    wa_token = u.get("whatsapp_meta_access_token", "")
    return {
        # Identity
        "email": u.get("email", ""),
        "name": u.get("name", ""),
        "pharmacy_id": u.get("pharmacy_id", ""),
        "picture": u.get("picture", ""),
        # Pharmacy
        "pharmacy_name": u.get("pharmacy_name", ""),
        "owner_name": u.get("owner_name", ""),
        "license_number": u.get("license_number", ""),
        "store_type": u.get("store_type", ""),
        "phone_number": u.get("phone_number", ""),
        "address": u.get("address", ""),
        "whatsapp": u.get("whatsapp", ""),
        # Account
        "global_role": u.get("global_role", "user"),
        "subscription_plan": u.get("subscription_plan", "free"),
        "is_active": u.get("is_active", True),
        "created_at": u.get("created_at", "").isoformat() if isinstance(u.get("created_at"), datetime) else str(u.get("created_at", "")),
        "last_login": u.get("last_login", "").isoformat() if isinstance(u.get("last_login"), datetime) else str(u.get("last_login", "")),
        # WhatsApp Config
        "whatsapp_enabled": u.get("whatsapp_enabled", False),
        "whatsapp_display_number": u.get("whatsapp_display_number", ""),
        "whatsapp_bot_name": u.get("whatsapp_bot_name", ""),
        "whatsapp_meta_phone_number_id": u.get("whatsapp_meta_phone_number_id", ""),
        "whatsapp_meta_access_token_masked": _mask_token(wa_token) if wa_token else "",
        "whatsapp_configured_at": u.get("whatsapp_configured_at", "").isoformat() if isinstance(u.get("whatsapp_configured_at"), datetime) else str(u.get("whatsapp_configured_at", "")),
        "whatsapp_configured_by": u.get("whatsapp_configured_by", ""),
    }


# ─── Routes ──────────────────────────────────────────────────────────────────

@router.get("/pharmacies", summary="List all registered pharmacies")
async def list_pharmacies():
    """
    Return every pharmacy (user) registered in the system.
    Includes WhatsApp config status, subscription plan, activity.
    Token is NEVER returned — masked as ****XXXXXX.
    """
    db = get_db()
    users = await db["users"].find(
        {},
        {"_id": 0, "hashed_password": 0}  # never return password hash
    ).sort("created_at", -1).to_list(length=500)

    pharmacies = [_serialize_user(u) for u in users]

    total = len(pharmacies)
    wa_enabled = sum(1 for p in pharmacies if p["whatsapp_enabled"])
    active = sum(1 for p in pharmacies if p["is_active"])

    return {
        "status": "ok",
        "stats": {
            "total_pharmacies": total,
            "whatsapp_enabled": wa_enabled,
            "active_accounts": active,
            "inactive_accounts": total - active,
        },
        "data": pharmacies,
    }


@router.get("/pharmacies/{pharmacy_id}", summary="Get single pharmacy detail")
async def get_pharmacy(pharmacy_id: str):
    """Get full detail of a single pharmacy by pharmacy_id."""
    db = get_db()
    user = await db["users"].find_one(
        {"pharmacy_id": pharmacy_id},
        {"_id": 0, "hashed_password": 0}
    )
    if not user:
        raise HTTPException(status_code=404, detail=f"Pharmacy '{pharmacy_id}' not found.")
    return {"status": "ok", "data": _serialize_user(user)}


@router.post("/pharmacies/{pharmacy_id}/whatsapp", summary="Setup WhatsApp for a pharmacy")
async def setup_whatsapp(pharmacy_id: str, body: WhatsAppSetupRequest):
    """
    Store Meta WhatsApp credentials for a pharmacy.
    Token is stored as-is (add encryption in production).
    """
    db = get_db()
    user = await db["users"].find_one({"pharmacy_id": pharmacy_id})
    if not user:
        raise HTTPException(status_code=404, detail=f"Pharmacy '{pharmacy_id}' not found.")

    await db["users"].update_one(
        {"pharmacy_id": pharmacy_id},
        {
            "$set": {
                "whatsapp_meta_phone_number_id": body.phone_number_id.strip(),
                "whatsapp_meta_access_token": body.access_token.strip(),
                "whatsapp_display_number": body.display_number.strip(),
                "whatsapp_bot_name": body.bot_name or "Sanjeevani WhatsApp Bot",
                "whatsapp_enabled": True,
                "whatsapp_configured_at": datetime.utcnow(),
                "whatsapp_configured_by": "admin",
            }
        }
    )
    return {
        "status": "ok",
        "message": f"WhatsApp configured successfully for pharmacy {pharmacy_id}",
        "pharmacy_id": pharmacy_id,
        "token_masked": _mask_token(body.access_token),
    }


@router.delete("/pharmacies/{pharmacy_id}/whatsapp", summary="Remove WhatsApp config")
async def remove_whatsapp(pharmacy_id: str):
    """Clear WhatsApp credentials and disable the bot for a pharmacy."""
    db = get_db()
    user = await db["users"].find_one({"pharmacy_id": pharmacy_id})
    if not user:
        raise HTTPException(status_code=404, detail=f"Pharmacy '{pharmacy_id}' not found.")

    await db["users"].update_one(
        {"pharmacy_id": pharmacy_id},
        {
            "$unset": {
                "whatsapp_meta_phone_number_id": "",
                "whatsapp_meta_access_token": "",
                "whatsapp_display_number": "",
                "whatsapp_bot_name": "",
                "whatsapp_configured_at": "",
                "whatsapp_configured_by": "",
            },
            "$set": {"whatsapp_enabled": False}
        }
    )
    return {"status": "ok", "message": f"WhatsApp removed for pharmacy {pharmacy_id}"}


@router.patch("/pharmacies/{pharmacy_id}/toggle", summary="Toggle pharmacy active status")
async def toggle_pharmacy_status(pharmacy_id: str):
    """Toggle is_active for a pharmacy account."""
    db = get_db()
    user = await db["users"].find_one({"pharmacy_id": pharmacy_id})
    if not user:
        raise HTTPException(status_code=404, detail=f"Pharmacy '{pharmacy_id}' not found.")
    new_status = not user.get("is_active", True)
    await db["users"].update_one(
        {"pharmacy_id": pharmacy_id},
        {"$set": {"is_active": new_status, "updated_at": datetime.utcnow()}}
    )
    return {"status": "ok", "is_active": new_status, "pharmacy_id": pharmacy_id}


@router.get("/stats", summary="Admin dashboard stats")
async def admin_stats():
    """Quick stats for the admin dashboard header."""
    db = get_db()
    total = await db["users"].count_documents({})
    active = await db["users"].count_documents({"is_active": True})
    wa_enabled = await db["users"].count_documents({"whatsapp_enabled": True})
    pro_users = await db["users"].count_documents({"subscription_plan": {"$in": ["pro", "ultra", "enterprise"]}})
    return {
        "status": "ok",
        "total_pharmacies": total,
        "active_pharmacies": active,
        "whatsapp_bots_live": wa_enabled,
        "paid_subscribers": pro_users,
    }
