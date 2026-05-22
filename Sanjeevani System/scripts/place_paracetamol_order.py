
import pymongo
import time
from datetime import datetime
import random

def main():
    uri = "mongodb+srv://samaypowade9:samay2005@cluster0.jvwb3d8.mongodb.net/?appName=Cluster0"
    client = pymongo.MongoClient(uri)
    db = client["sanjeevani_rx_db"]
    
    # Merchant ID found from debug script
    merchant_id = "PHARM_69ca9aee5b539eb0d62facd9"
    
    # Generate a unique hash-like Order ID (6 digits)
    order_hash = "".join([str(random.randint(0, 9)) for _ in range(6)])
    order_id = f"ORD{datetime.now().strftime('%Y%m%d%H%M')}{order_hash}"
    
    quantity = 35
    price = 250
    total = quantity * price
    
    new_order = {
        "Order ID": order_id,
        "Patient Name": "Samay Powade",
        "Medicine Name": "Paracetamol",
        "Quantity": quantity,
        "Total Amount": total,
        "Order Status": "Pending",
        "Order Channel": "WhatsApp",
        "Order Date": datetime.utcnow(),
        "merchant_id": merchant_id,
        "pharmacy_id": merchant_id,
        "Payment Method": "Unpaid",
        "Contact Number": "whatsapp:+919764096358",
        "source_channel": "whatsapp",
        "source_provider": "meta",
        "source_message_id": f"wamid.TEST{int(time.time())}",
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        # Fields for frontend display consistency
        "hash": f"#{order_hash}"
    }
    
    db.consumer_orders.insert_one(new_order)
    print("SUCCESS: Successfully placed new order!")
    print(f"   ID: {order_id}")
    print(f"   Patient: Samay Powade")
    print(f"   Medication: Paracetamol ({quantity} Units)")
    print(f"   Value: INR {total:,}")
    print(f"   Status: PENDING")

if __name__ == "__main__":
    main()
