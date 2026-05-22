import os
import time
import uuid
import json
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, HTTPException, Depends, Request, UploadFile, File, Form
from pydantic import BaseModel
from groq import Groq
import groq

from app.database.mongo_client import get_db
from app.config import settings
from app.utils.logger import get_logger
from app.modules.safety_validation import SafetyValidationService
from app.modules.inventory_intelligence import InventoryIntelligenceService
from app.modules.context_intelligence import ContextIntelligenceService

router = APIRouter(prefix="/chat", tags=["Chatbot"])
logger = get_logger(__name__)
safety_svc = SafetyValidationService()
inventory_svc = InventoryIntelligenceService()
context_svc = ContextIntelligenceService()

# Initialize Groq client
if settings.GROQ_API_KEY:
    groq_client = Groq(api_key=settings.GROQ_API_KEY)
else:
    groq_client = None

class ChatState(str, Enum):
    ONBOARDING_LANGUAGE = "ONBOARDING_LANGUAGE"
    ONBOARDING_NAME = "ONBOARDING_NAME"
    ONBOARDING_GENDER = "ONBOARDING_GENDER"
    ONBOARDING_AGE = "ONBOARDING_AGE"
    GREETING = "GREETING"
    COLLECT_QUANTITY = "COLLECT_QUANTITY"
    CONFIRM_ORDER = "CONFIRM_ORDER"
    COLLECT_ADDRESS = "COLLECT_ADDRESS"
    FINALIZING = "FINALIZING"

class ChatRequest(BaseModel):
    message: str
    phone: Optional[str] = None
    session_id: Optional[str] = None
    merchant_id: Optional[str] = "samaypowade9@gmail.com"
    button_id: Optional[str] = None # New: if triggered via button

class ChatResponse(BaseModel):
    text: str
    session_id: str
    state: str # For debugging/UI logic
    buttons: Optional[List[Dict[str, str]]] = []
    extracted_data: Optional[Dict[str, Any]] = {}

def generate_session_id():
    return str(uuid.uuid4())

def get_session_state(session_id: str):
    db = get_db()
    session = db["chat_sessions"].find_one({"session_id": session_id})
    if session:
        return session.get("state", ChatState.ONBOARDING_LANGUAGE), session.get("temp_data", {})
    return ChatState.ONBOARDING_LANGUAGE, {}

def save_session_state(session_id: str, state: ChatState, temp_data: Dict[str, Any]):
    db = get_db()
    db["chat_sessions"].update_one(
        {"session_id": session_id},
        {"$set": {
            "state": state,
            "temp_data": temp_data,
            "updated_at": datetime.utcnow()
        }}
    )

@router.get("/sessions")
def get_sessions(phone: str = "", merchant_id: str = "samaypowade9@gmail.com"):
    db = get_db()
    query = {"merchant_id": merchant_id}
    if phone:
        query["phone"] = phone
    
    sessions_cursor = db["chat_sessions"].find(query).sort("updated_at", -1)
    sessions = []
    for s in sessions_cursor:
        sessions.append({
            "session_id": s["session_id"],
            "title": s.get("title", "Chat Request"),
            "updated_at": s.get("updated_at")
        })
    return sessions

@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, merchant_id: str = "samaypowade9@gmail.com"):
    db = get_db()
    session = db["chat_sessions"].find_one({"session_id": session_id, "merchant_id": merchant_id})
    if session:
        db["chat_sessions"].delete_one({"session_id": session_id})
        db["chat_history"].delete_many({"session_id": session_id})
    return {"status": "ok"}

@router.get("/history/{session_id}")
def get_history(session_id: str, merchant_id: str = "samaypowade9@gmail.com"):
    db = get_db()
    history = list(db["chat_history"].find({"session_id": session_id, "merchant_id": merchant_id}).sort("timestamp", 1))
    for h in history:
        h.pop("_id", None)
    return history

@router.post("", response_model=ChatResponse)
def process_chat(request: ChatRequest):
    db = get_db()
    session_id = request.session_id
    if not session_id:
        session_id = generate_session_id()
        # Initialize session
        db["chat_sessions"].insert_one({
            "session_id": session_id,
            "merchant_id": request.merchant_id or "samaypowade9@gmail.com",
            "phone": request.phone,
            "title": f"Chat {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        })
    
    # Save user message
    db["chat_history"].insert_one({
        "session_id": session_id,
        "merchant_id": request.merchant_id or "samaypowade9@gmail.com",
        "role": "user",
        "text": request.message,
        "timestamp": datetime.utcnow()
    })
    
    # Update session
    db["chat_sessions"].update_one(
        {"session_id": session_id},
        {"$set": {"updated_at": datetime.utcnow()}}
    )

    if not groq_client:
        bot_response = "I am currently running in offline mode because the AI key is not configured. I can still help you browse manually!"
        db["chat_history"].insert_one({
            "session_id": session_id,
            "merchant_id": request.merchant_id or "samaypowade9@gmail.com",
            "role": "bot",
            "text": bot_response,
            "timestamp": datetime.utcnow()
        })
        return {"text": bot_response, "session_id": session_id}

    # 3. Pre-process Orchestration (Check agents before AI)
    current_state, temp_data = get_session_state(session_id)
    
    # Try to find medicine in current message to give agents context
    possible_med = None
    import re
    # Use broader candidate selection: any word 3+ chars
    candidates = re.findall(r'\b[A-Za-z]{3,20}\b', request.message)
    for word in candidates:
        # Check against master DB (exact or prefix)
        match = db["medicine_master"].find_one({"brand_name": {"$regex": f"^{word}", "$options": "i"}})
        if match:
            possible_med = match["brand_name"]
            break
    
    # If no word matched, try matching the whole string (for multi-word meds)
    if not possible_med:
        match = db["medicine_master"].find_one({"brand_name": {"$regex": f".*{request.message.strip()}.*", "$options": "i"}})
        if match: possible_med = match["brand_name"]
    
    agent_insights = ""
    if possible_med:
        # Safety
        safety = safety_svc.validate_medicine(possible_med)
        if safety.get("is_habit_forming"):
            agent_insights += f"\n- SAFETY ALERT: {possible_med} is habit-forming. Prescription REQURED."
        
        # Inventory
        low_stock = inventory_svc.check_low_stock(request.merchant_id or "samaypowade9@gmail.com")
        if any(i['Medicine Name'].lower() == possible_med.lower() for i in low_stock):
            agent_insights += f"\n- INVENTORY ALERT: {possible_med} is low in stock."
            
        # Context
        patient_id = request.phone or temp_data.get("name", "Guest")
        profile = context_svc.get_patient_profile(patient_id)
        if profile and possible_med in profile.get("active_medicines", []):
            agent_insights += f"\n- CONTEXT: User has ordered {possible_med} before. This is a potential refill."

    # 4. Prepare Structured AI context
    STRUCTURED_SYSTEM_PROMPT = f"""You are SanjeevaniRxAI, a professional pharmacy assistant.
Current User State: {current_state}
Current Stored Data: {json.dumps(temp_data)}
Agent Insights for current query: {agent_insights}

Your goal is to guide the user through a safe and easy medicine ordering process, exactly like a WhatsApp pharmacist.

⚠️ STRICT SCOPE CONTROL:
- You are a specialized Medical & Pharmacy Assistant.
- **DO NOT** answer questions about politics, general knowledge, sports, celebrities, or anything outside of medicines, health, symptoms, and pharmacy services.
- If a user asks an off-topic question, politely say: "I apologize, but I am specialized only in medicines and healthcare assistance. How can I help you with your prescription or order today?"

⚠️ PRIORITY RULE:
- If the user asks for a medicine or shows an intent to order, **IMMEDIATELY** handle the medicine request (detect medicine, quantity, and check stock/safety).
- **DO NOT** block the order for Onboarding (Name/Age/Gender) if the user is already asking for a medicine. You can collect their details *after* helping them with the medicine.

CRITICAL: CONTEXT RETENTION
- If 'medicine_name' or 'quantity' are in 'Current Stored Data', do NOT forget them.
- If Onboarding (Language/Name/Gender/Age) just finished, and a medicine was already mentioned, IMMEDIATELY move to CONFIRM_ORDER or COLLECT_QUANTITY.

STATE-SPECIFIC INSTRUCTIONS:
- ONBOARDING_LANGUAGE: Ask "Namaste! Please choose your preferred language" with buttons [English, Hindi, Marathi].
- ONBOARDING_NAME: Ask for their full name.
- ONBOARDING_GENDER: Ask for gender [Male, Female, Other].
- ONBOARDING_AGE: Ask for age.
- GREETING: Professional welcome. Ask how to help with medicines (Only if no medicine is pending).
- CONFIRM_ORDER: Summarize the order (Med, Qty, Price) and ask to confirm.

AGENT CAPABILITIES:
- Safety Agent: Checks for habit-forming or prescription needs.
- Inventory Agent: Checks stock availability & predicts stock-outs.
- Context Agent: Recognizes if this is a repeat order or refill.

OUTPUT FORMAT: Your response MUST be a single JSON object:
{{
  "reply": "Friendly text response to the user",
  "intent": "GREETING | ORDER_MEDICINE | PROVIDE_INFO | CONFIRM | CANCEL | TRACK_ORDER",
  "new_state": "The next ChatState based on conversation progress",
  "buttons": [{{"id": "btn_id", "title": "Button Text"}}],
  "extracted_data": {{"medicine_name": "...", "quantity": 1, "name": "...", "language": "...", "gender": "...", "age": 0}}
}}
"""
    # Fetch history for context
    history_cursor = db["chat_history"].find({"session_id": session_id}).sort("timestamp", 1).limit(6)
    messages = [{"role": "system", "content": STRUCTURED_SYSTEM_PROMPT}]
    
    for h in history_cursor:
        role = "assistant" if h["role"] == "bot" else "user"
        messages.append({"role": role, "content": h["text"]})
    
    # Add the current message
    messages.append({"role": "user", "content": request.message})
        
    try:
        completion = groq_client.chat.completions.create(
            model=settings.GROQ_MODEL,
            messages=messages,
            temperature=0.2,
            response_format={"type": "json_object"}
        )
        ai_response = json.loads(completion.choices[0].message.content)
        bot_text = ai_response.get("reply", "")
        new_state = ai_response.get("new_state", current_state)
        buttons = ai_response.get("buttons", [])
        extracted = ai_response.get("extracted_data", {})
        
        # 5. [DEPRECATED HARDCODED AGENTS] - Now moved to System Prompt for better AI flow
        # Logic is now handled by the LLM using the 'agent_insights' injected above.

        # 6. Update Temp Data
        if extracted:
            temp_data.update({k: v for k, v in extracted.items() if v is not None})
            
        # 7. Special Case: Order Finalization
        placed_order_id = None
        if ai_response.get("intent") == "CONFIRM" and current_state == ChatState.CONFIRM_ORDER:
            # Place real order
            med_name = temp_data.get("medicine_name", "Unknown")
            qty = temp_data.get("quantity", 1)
            
            # Final Master check
            master_match = db["medicine_master"].find_one({"brand_name": {"$regex": f".*{med_name}.*", "$options": "i"}})
            if master_match: med_name = master_match.get("brand_name", med_name)
            
            placed_order_id = f"RX-{int(time.time())}"
            merchant_id = request.merchant_id or "samaypowade9@gmail.com"
            patient_name = temp_data.get("name") or "Guest User"
            
            new_order = {
                "Order ID": placed_order_id,
                "Patient Name": patient_name,
                "Medicine Name": med_name,
                "Quantity": int(qty),
                "Quantity Ordered": int(qty), # Compatibility with dashboard
                "Total Amount": float(250 * int(qty)), # Ensure float
                "Order Status": "Pending",
                "Order Date": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
                "merchant_id": merchant_id,
                "Order Channel": "Chatbot",
                "Gender": temp_data.get("gender"),
                "Age": int(temp_data.get("age", 0)) if temp_data.get("age") else None,
                "Payment Method": "Online",
            }
            db["consumer_orders"].insert_one(new_order)
            
            # --- Sync with Dashboard (Patients & Analytics) ---
            patient_id = f"PT-{patient_name}".replace(" ", "-").upper()
            db["patients"].update_one(
                {"merchant_id": merchant_id, "patient_id": patient_id},
                {
                    "$set": {
                        "name": patient_name,
                        "merchant_id": merchant_id,
                        "last_order_id": placed_order_id,
                        "last_order_date": datetime.utcnow(),
                        "latest_medicine": med_name,
                        "last_channel": "Chatbot",
                        "updated_at": datetime.utcnow(),
                        "Gender": temp_data.get("gender"),
                        "Age": int(temp_data.get("age", 0)) if temp_data.get("age") else None,
                    },
                    "$setOnInsert": {"created_at": datetime.utcnow()},
                    "$inc": {"orders_count": 1},
                },
                upsert=True,
            )
            
            bot_text = (
                f"✅ **Order Confirmed Successfully!**\n\n"
                f"📦 **Order Details:**\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🆔 **Order ID:** `#{placed_order_id}`\n"
                f"💊 **Medicine:** {med_name}\n"
                f"🔢 **Quantity:** {qty} Units\n"
                f"👤 **Patient:** {patient_name}\n"
                f"🏠 **Status:** Pending Dispatch\n"
                f"━━━━━━━━━━━━━━━━━━\n\n"
                f"Our pharmacist has been notified and will prepare your order shortly. Thank you for choosing SanjeevaniRxAI!"
            )
            new_state = ChatState.GREETING
            temp_data = {"name": temp_data.get("name"), "language": temp_data.get("language")} # Keep core info
            buttons = [{"id": "track", "title": "📍 Track Order"}, {"id": "new", "title": "➕ New Order"}]

        # 8. Save Session State
        save_session_state(session_id, new_state, temp_data)
        
    except Exception as e:
        logger.error(f"Chat Processing Error: {e}")
        bot_text = "I encountered an error processing your request. Let's try again."
        new_state = current_state
        buttons = []

    # 8. Save bot response
    db["chat_history"].insert_one({
        "session_id": session_id,
        "merchant_id": request.merchant_id or "samaypowade9@gmail.com",
        "role": "bot",
        "text": bot_text,
        "timestamp": datetime.utcnow()
    })
    
    return {"text": bot_text, "session_id": session_id, "state": new_state, "buttons": buttons}


@router.post("/upload-prescription")
async def upload_prescription(
    file: UploadFile = File(...),
    phone: str = Form(None),
    session_id: str = Form(None),
    merchant_id: str = Form("samaypowade9@gmail.com")
):
    db = get_db()
    if not session_id:
        session_id = generate_session_id()
        db["chat_sessions"].insert_one({
            "session_id": session_id,
            "merchant_id": merchant_id or "samaypowade9@gmail.com",
            "phone": phone,
            "title": f"Prescription Upload {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        })
        
    # Save file temporarily
    file_path = f"uploads/{file.filename}"
    os.makedirs("uploads", exist_ok=True)
    with open(file_path, "wb") as buffer:
        buffer.write(await file.read())
        
    # Process with Safety Agent
    result = await safety_svc.process_prescription_file(file_path, groq_client)
    
    if not result.get("success"):
        message = f"❌ **Verification Failed:** {result.get('error')}"
    else:
        meds = result.get("matched_medicines", [])
        med_list = "\n".join([f"- {m['name']} ({m.get('dosage','')})" for m in meds])
        message = (f"✅ **Prescription Verified Successfully!**\n\n"
                   f"**Doctor:** {result.get('doctor', 'Not Found')}\n"
                   f"**Medicines Extracted:**\n{med_list}\n\n"
                   f"We have generated a pending order for these items.")

    # Save bot response
    db["chat_history"].insert_one({
        "session_id": session_id,
        "merchant_id": merchant_id or "samaypowade9@gmail.com",
        "role": "bot",
        "text": message,
        "timestamp": datetime.utcnow()
    })
               
    return {"message": message, "session_id": session_id, "data": result}
