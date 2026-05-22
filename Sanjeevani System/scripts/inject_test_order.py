
import pymongo
import time
from datetime import datetime
import os

def main():
    uri = "mongodb+srv://samaypowade9:samay2005@cluster0.jvwb3d8.mongodb.net/?appName=Cluster0"
    client = pymongo.MongoClient(uri)
    db = client["sanjeevani_rx_db"]
    
    # Find the pharmacy
    pharmacy = db.users.find_one({"pharmacy_name": {"$exists": True}})
    if not pharmacy:
        print("No pharmacy found in database.")
        return
    
    pharmacy_id = pharmacy.get("pharmacy_id")
    pharmacy_name = pharmacy.get("pharmacy_name")
    print(f"Found Pharmacy: {pharmacy_name} (ID: {pharmacy_id})")
    
    # Insert a test order for Paracetamol
    order_id = f"TEST-{int(time.time())}"
    new_order = {
        "Order ID": order_id,
        "Patient Name": "Samay Powade",
        "Medicine Name": "Paracetamol",
        "Quantity": 20,
        "Total Amount": 5000,
        "Order Status": "Pending",
        "Order Channel": "WHATSAPP",
        "Order Date": datetime.utcnow(),
        "merchant_id": pharmacy_id,
        "pharmacy_id": pharmacy_id,
        "source_channel": "app",
        "source_provider": "delivery_app",
        "Payment Method": "Manual Entry",
        "is_unmatched": False
    }
    
    db.consumer_orders.insert_one(new_order)
    print(f"✅ Successfully placed order {order_id} for {pharmacy_name}")

if __name__ == "__main__":
    main()
