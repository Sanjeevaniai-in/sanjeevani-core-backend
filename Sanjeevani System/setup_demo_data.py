"""
Demo Data Setup for Refill Reminder System
─────────────────────────────────────────────────────────────────────────────
Creates realistic demo orders to show automatic refill reminder detection
"""

from datetime import datetime, timedelta, timezone
from pymongo import MongoClient
import os
from dotenv import load_dotenv

load_dotenv()

# MongoDB connection
MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
client = MongoClient(MONGODB_URL)
db = client.pharmacy_db

# Demo users
demo_users = [
    {
        "user_id": "+919876543210",
        "name": "Rahul Sharma",
        "age": 35,
        "gender": "Male",
        "language": "Hindi",
        "created_at": datetime.now(timezone.utc)
    },
    {
        "user_id": "+919876543211",
        "name": "Priya Patel",
        "age": 28,
        "gender": "Female",
        "language": "English",
        "created_at": datetime.now(timezone.utc)
    },
    {
        "user_id": "+919876543212",
        "name": "Amit Kumar",
        "age": 42,
        "gender": "Male",
        "language": "Hindi",
        "created_at": datetime.now(timezone.utc)
    },
    {
        "user_id": "+919876543213",
        "name": "Sneha Reddy",
        "age": 31,
        "gender": "Female",
        "language": "English",
        "created_at": datetime.now(timezone.utc)
    }
]

# Demo orders - strategically dated to trigger reminders
today = datetime.now(timezone.utc)

demo_orders = [
    # Order 1: Should trigger reminder (5 days remaining)
    {
        "order_id": f"ORD{(today - timedelta(days=25)).strftime('%Y%m%d')}001",
        "user_id": "+919876543210",
        "medicine_name": "Paracetamol 650mg",
        "product_id": "MED001",
        "quantity": 30,
        "price": 25.0,
        "total_amount": 25.0,
        "order_status": "delivered",
        "created_at": today - timedelta(days=25),
        "updated_at": today - timedelta(days=25)
    },
    
    # Order 2: Should trigger reminder (6 days remaining)
    {
        "order_id": f"ORD{(today - timedelta(days=24)).strftime('%Y%m%d')}002",
        "user_id": "+919876543211",
        "medicine_name": "Vitamin D3 60K",
        "product_id": "MED002",
        "quantity": 4,
        "price": 120.0,
        "total_amount": 120.0,
        "order_status": "delivered",
        "created_at": today - timedelta(days=24),
        "updated_at": today - timedelta(days=24)
    },
    
    # Order 3: Should trigger reminder (4 days remaining)
    {
        "order_id": f"ORD{(today - timedelta(days=26)).strftime('%Y%m%d')}003",
        "user_id": "+919876543212",
        "medicine_name": "Metformin 500mg",
        "product_id": "MED003",
        "quantity": 60,
        "price": 45.0,
        "total_amount": 45.0,
        "order_status": "delivered",
        "created_at": today - timedelta(days=26),
        "updated_at": today - timedelta(days=26)
    },
    
    # Order 4: Too early for reminder (15 days remaining)
    {
        "order_id": f"ORD{(today - timedelta(days=15)).strftime('%Y%m%d')}004",
        "user_id": "+919876543213",
        "medicine_name": "Crocin Advance",
        "product_id": "MED004",
        "quantity": 30,
        "price": 35.0,
        "total_amount": 35.0,
        "order_status": "delivered",
        "created_at": today - timedelta(days=15),
        "updated_at": today - timedelta(days=15)
    },
    
    # Order 5: Already ran out (should have been reminded)
    {
        "order_id": f"ORD{(today - timedelta(days=35)).strftime('%Y%m%d')}005",
        "user_id": "+919876543210",
        "medicine_name": "Aspirin 75mg",
        "product_id": "MED005",
        "quantity": 30,
        "price": 15.0,
        "total_amount": 15.0,
        "order_status": "delivered",
        "created_at": today - timedelta(days=35),
        "updated_at": today - timedelta(days=35)
    }
]

def setup_demo_data():
    """Setup demo data for refill reminder demonstration"""
    
    print("🚀 Setting up demo data for Refill Reminder System...")
    print("=" * 60)
    
    # Clear existing demo data
    print("\n1️⃣ Clearing existing demo data...")
    db.users.delete_many({"user_id": {"$in": [u["user_id"] for u in demo_users]}})
    db.orders.delete_many({"user_id": {"$in": [u["user_id"] for u in demo_users]}})
    db.refill_reminders.delete_many({"user_id": {"$in": [u["user_id"] for u in demo_users]}})
    print("   ✅ Cleared old demo data")
    
    # Insert demo users
    print("\n2️⃣ Creating demo users...")
    for user in demo_users:
        db.users.update_one(
            {"user_id": user["user_id"]},
            {"$set": user},
            upsert=True
        )
        print(f"   ✅ Created user: {user['name']} ({user['user_id']})")
    
    # Insert demo orders
    print("\n3️⃣ Creating demo orders...")
    for order in demo_orders:
        db.orders.insert_one(order)
        
        # Calculate days remaining
        order_date = order["created_at"]
        quantity = order["quantity"]
        days_supply = quantity  # Assuming 1 tablet per day
        run_out_date = order_date + timedelta(days=days_supply)
        days_remaining = (run_out_date - today).days
        
        status = "🔴 URGENT" if days_remaining < 3 else "🟡 REMINDER NEEDED" if 3 <= days_remaining <= 7 else "🟢 OK"
        
        print(f"   ✅ Order: {order['medicine_name']}")
        print(f"      User: {order['user_id']}")
        print(f"      Ordered: {order_date.strftime('%Y-%m-%d')}")
        print(f"      Quantity: {quantity} tablets")
        print(f"      Days Remaining: {days_remaining} days")
        print(f"      Status: {status}")
        print()
    
    print("=" * 60)
    print("✅ Demo data setup complete!")
    print("\n📊 Summary:")
    print(f"   • Users created: {len(demo_users)}")
    print(f"   • Orders created: {len(demo_orders)}")
    print(f"   • Orders needing reminder: 3")
    print(f"   • Orders too early: 1")
    print(f"   • Orders already expired: 1")
    
    print("\n🎯 Next Steps:")
    print("   1. Start your server: python -m uvicorn app.main:app --reload")
    print("   2. Open dashboard: http://localhost:8000")
    print("   3. Trigger reminder check:")
    print("      curl -X POST http://localhost:8000/api/v1/api/refill-reminders/check")
    print("   4. View results in UI: http://localhost:8000/refill-dashboard")
    
    return {
        "users_created": len(demo_users),
        "orders_created": len(demo_orders),
        "status": "success"
    }

if __name__ == "__main__":
    result = setup_demo_data()
    print(f"\n✨ Setup completed: {result}")
