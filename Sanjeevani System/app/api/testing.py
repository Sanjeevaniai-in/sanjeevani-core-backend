from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime, timedelta, timezone
from typing import Dict, Any
import random

from app.database.mongo_client import get_db
from app.utils.security import get_current_user
from app.utils.logger import get_logger

router = APIRouter(prefix="/testing", tags=["Testing"])
logger = get_logger(__name__)

@router.post("/generate-data", summary="Generate realistic test data for the dashboard")
def generate_test_data(user: dict = Depends(get_current_user)) -> Dict[str, Any]:
    """
    Seeds realistic testing data:
    - Sets a few products to Low Stock
    - Sets a few products to Expiry Risk
    - Generates 5 active Refill Alerts
    - Generates historical dummy orders for time-series charts
    """
    db = get_db()
    merchant_id = user.get("merchant_id")
    
    if not merchant_id:
        raise HTTPException(status_code=400, detail="Merchant ID required.")

    logger.info(f"Generating test data for merchant: {merchant_id}")

    # 1. Products - Low Stock & Expiry
    # Find up to 10 products to modify
    products = list(db["products"].find({"merchant_id": merchant_id}).limit(10))
    inventory = list(db["inventory"].find({"merchant_id": merchant_id}).limit(10))
    
    docs_to_update = inventory if inventory else products
    
    if len(docs_to_update) >= 8:
        now = datetime.now(timezone.utc)
        
        # Mark 5 as Low Stock
        for doc in docs_to_update[:5]:
            db[("inventory" if inventory else "products")].update_one(
                {"_id": doc["_id"]},
                {"$set": {
                    "Current Stock": random.randint(1, 5),
                    "Reorder Level": 10,
                    "is_low_stock": True
                }}
            )
            
        # Mark 3 as Expiry Risk
        for doc in docs_to_update[5:8]:
            exp_date = now + timedelta(days=random.randint(5, 25))
            db[("inventory" if inventory else "products")].update_one(
                {"_id": doc["_id"]},
                {"$set": {
                    "Expiry Date": exp_date.isoformat(),
                    "is_expiry_risk": True
                }}
            )

    # 2. Refill Alerts
    for i in range(5):
        db["alerts"].update_one(
            {"merchant_id": merchant_id, "patient_id": f"TEST-PT-{i}"},
            {"$set": {
                "alert_type": "refill_due",
                "is_resolved": False,
                "message": f"Refill due for Patient TEST-PT-{i}",
                "created_at": datetime.now(timezone.utc)
            }},
            upsert=True
        )

    # 3. Generate Historical Orders (Last 7 Days)
    now = datetime.now(timezone.utc)
    for day_offset in range(7):
        order_date = now - timedelta(days=day_offset)
        # Create 2-5 orders per day
        for order_idx in range(random.randint(2, 5)):
            order_id = f"TEST-ORD-{day_offset}-{order_idx}"
            db["consumer_orders"].update_one(
                {"Order ID": order_id, "merchant_id": merchant_id},
                {"$set": {
                    "Order ID": order_id,
                    "Patient Name": "Test Customer",
                    "Medicine Name": "Test Medicine",
                    "Quantity": 1,
                    "Total Amount": random.randint(100, 500),
                    "Order Status": "Completed",
                    "Order Channel": "Web Console",
                    "Order Date": order_date,
                    "merchant_id": merchant_id,
                    "Payment Method": "Card",
                }},
                upsert=True
            )

    return {"status": "success", "message": "Test data generated successfully. Please refresh the dashboard."}
