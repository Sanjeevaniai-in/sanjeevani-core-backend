import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = "sanjeevani_auth"

async def seed_pharmacies():
    client = AsyncIOMotorClient(MONGO_URI)
    db = client[DB_NAME]
    
    # Test Pharmacies in Nanded area (matching the app's default viewport)
    pharmacies = [
        {
            "email": "owner1@sanjeevani.com",
            "name": "Sanjeevani Central Owner",
            "pharmacy_name": "Sanjeevani Central",
            "pharmacy_id": "PHARM_69ca9aee5b539eb0d62facd9",
            "global_role": "pharmacy_owner",
            "subscription_plan": "ultra",
            "is_active": True,
            "lat": 19.1380,
            "lng": 77.3180,
            "address": "Opposite Railway Station, Nanded",
            "phone_number": "919876543210",
            "whatsapp": "919876543210",
            "telegram": "sanjeevani_admin",
            "created_at": datetime.now(timezone.utc)
        },
        {
            "email": "owner2@medplus.com",
            "name": "MedPlus Nanded Manager",
            "pharmacy_name": "MedPlus Nanded",
            "pharmacy_id": "PHARM_medplus_001",
            "global_role": "pharmacy_owner",
            "subscription_plan": "pro",
            "is_active": True,
            "lat": 19.1430,
            "lng": 77.3250,
            "address": "Vazirabad, Nanded",
            "phone_number": "919123456789",
            "whatsapp": "919123456789",
            "created_at": datetime.now(timezone.utc)
        },
        {
            "email": "owner3@apollo.com",
            "name": "Apollo Hub Admin",
            "pharmacy_name": "Apollo HealthHub",
            "pharmacy_id": "PHARM_apollo_001",
            "global_role": "pharmacy_owner",
            "subscription_plan": "ultra",
            "is_active": True,
            "lat": 19.1340,
            "lng": 77.3320,
            "address": "Airport Road, Nanded",
            "phone_number": "911234567890",
            "telegram": "apollo_support",
            "created_at": datetime.now(timezone.utc)
        }
    ]
    
    print(f"🚀 Seeding {len(pharmacies)} pharmacies into {DB_NAME}.users...")
    
    for p in pharmacies:
        result = await db["users"].update_one(
            {"email": p["email"]},
            {"$set": p},
            upsert=True
        )
        if result.upserted_id:
            print(f"✅ Created: {p['pharmacy_name']}")
        else:
            print(f"🔄 Updated: {p['pharmacy_name']}")
            
    print("\n✨ Seeding complete! You can now verify the map at these coordinates.")
    client.close()

if __name__ == "__main__":
    asyncio.run(seed_pharmacies())
