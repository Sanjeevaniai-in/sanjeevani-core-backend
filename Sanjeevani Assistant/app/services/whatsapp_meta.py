import json
import httpx
from ..core.config import META_ACCESS_TOKEN, META_PHONE_NUMBER_ID
from ..core.logger import logger

def get_meta_headers():
    return {
        "Authorization": f"Bearer {META_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

def get_meta_url():
    return f"https://graph.facebook.com/v17.0/{META_PHONE_NUMBER_ID}/messages"

def send_whatsapp_text_meta(to_number: str, text: str):
    """Sends a plain text message via Meta Cloud API"""
    if not META_ACCESS_TOKEN or not META_PHONE_NUMBER_ID:
        logger.warning("Meta credentials missing, message not sent.")
        return

    # Meta expects number without '+'
    if to_number.startswith("+"):
        to_number = to_number[1:]
    # If it was saved with 'whatsapp:' prefix, strip it
    if "whatsapp:" in to_number:
        to_number = to_number.split(":")[1].replace("+", "")

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_number,
        "type": "text",
        "text": {"preview_url": False, "body": text},
    }

    try:
        response = httpx.post(get_meta_url(), headers=get_meta_headers(), json=payload)
        response.raise_for_status()
        logger.info(f"✅ Meta Text sent to {to_number}")
    except Exception as e:
        logger.error(f"❌ Meta Send Text Error: {e}")


def send_whatsapp_buttons_meta(to_number: str, body_text: str, buttons: list):
    """Sends interactive buttons via Meta Cloud API"""
    if not META_ACCESS_TOKEN or not META_PHONE_NUMBER_ID:
        logger.warning("Meta credentials missing, message not sent.")
        return

    # Meta expects number without '+', strip prefixes
    if to_number.startswith("+"):
        to_number = to_number[1:]
    if "whatsapp:" in to_number:
        to_number = to_number.split(":")[1].replace("+", "")

    formatted_buttons = []
    for btn in buttons:
        formatted_buttons.append({
            "type": "reply",
            "reply": {
                "id": btn["id"],
                "title": btn["title"][:20] # Meta enforces max 20 chars for button title
            }
        })

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_number,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body_text},
            "action": {"buttons": formatted_buttons},
        },
    }

    try:
        response = httpx.post(get_meta_url(), headers=get_meta_headers(), json=payload)
        response.raise_for_status()
        logger.info(f"✅ Meta Buttons sent to {to_number}")
    except Exception as e:
        logger.error(f"❌ Meta Send Buttons Error: {e}")


def send_whatsapp_list_meta(to_number: str, body_text: str, button_text: str, items: list, title: str = None):
    """Sends an interactive list (bottom sheet) via Meta Cloud API"""
    if not META_ACCESS_TOKEN or not META_PHONE_NUMBER_ID:
        logger.warning("Meta credentials missing, message not sent.")
        return

    if to_number.startswith("+"):
        to_number = to_number[1:]
    if "whatsapp:" in to_number:
        to_number = to_number.split(":")[1].replace("+", "")

    rows = []
    for item in items:
        rows.append({
            "id": item["id"],
            "title": item["title"][:24], # Meta limit: 24 chars
            "description": item.get("description", "")[:72] # Meta limit: 72 chars
        })

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_number,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": body_text},
            "action": {
                "button": button_text[:20],
                "sections": [
                    {
                        "title": title[:24] if title else "Select Option",
                        "rows": rows
                    }
                ]
            }
        }
    }
    
    if title:
        payload["interactive"]["header"] = {"type": "text", "text": title[:60]}

    try:
        response = httpx.post(get_meta_url(), headers=get_meta_headers(), json=payload)
        response.raise_for_status()
        logger.info(f"✅ Meta List sent to {to_number}")
    except Exception as e:
        logger.error(f"❌ Meta Send List Error: {e}")
