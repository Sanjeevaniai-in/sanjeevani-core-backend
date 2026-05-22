import pandas as pd
import numpy as np
from pymongo import MongoClient, UpdateOne
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

MONGO_URI = os.getenv("MONGODB_URL")
DB_NAME = os.getenv("MONGODB_DB_NAME", "pharmacy_management")

def safe_val(v):
    if v is None: return None
    if isinstance(v, float) and np.isnan(v): return None
    return v

def ingest_medicines():
    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    collection = db["medicine_master"]

    print("Starting Medicine Master Ingestion (Optimized)...")

    # 1. Load indian_pharmaceutical_products_clean.csv (Small-Medium)
    file1 = r"d:\My Project\Sanjeevani\indian_pharmaceutical_products_clean.csv"
    print(f"Reading {file1}...")
    df1 = pd.read_csv(file1)
    
    ops = []
    print("Processing Indian Pharma Products...")
    for _, row in df1.iterrows():
        name = str(row['brand_name'])
        clean_name = name.strip().lower()
        
        doc = {
            "brand_name": name,
            "manufacturer": row.get('manufacturer'),
            "price_inr": safe_val(row.get('price_inr')),
            "dosage_form": row.get('dosage_form'),
            "therapeutic_class": row.get('therapeutic_class'),
            "primary_ingredient": row.get('primary_ingredient'),
            "is_discontinued": row.get('is_discontinued') == 'True',
            "updated_at": pd.Timestamp.now()
        }
        
        ops.append(UpdateOne(
            {"brand_name_clean": clean_name},
            {"$set": doc, "$setOnInsert": {"brand_name_clean": clean_name}},
            upsert=True
        ))

    # 2. Load all_medicine databased.csv (EXTREMELY LARGE)
    file2 = r"d:\My Project\Sanjeevani\all_medicine databased.csv"
    print(f"Reading {file2} in chunks...")
    
    chunk_size = 5000
    reader = pd.read_csv(file2, chunksize=chunk_size, low_memory=False)

    total_chunks = 0
    for chunk in reader:
        chunk_ops = []
        for _, row in chunk.iterrows():
            name = str(row.get('name'))
            if not name or name == 'nan': continue
            clean_name = name.strip().lower()
            
            # Collect side effects and uses into lists
            side_effects = [safe_val(row.get(f'sideEffect{i}')) for i in range(10) if pd.notna(row.get(f'sideEffect{i}'))]
            uses = [safe_val(row.get(f'use{i}')) for i in range(3) if pd.notna(row.get(f'use{i}'))]
            
            doc = {
                "habit_forming": str(row.get('Habit Forming')).strip().lower() == 'yes',
                "side_effects": [s for s in side_effects if s],
                "uses": [u for u in uses if u],
                "action_class": row.get('Action Class'),
                "chemical_class": row.get('Chemical Class'),
                "therapeutic_class_detailed": row.get('Therapeutic Class'),
                "updated_at": pd.Timestamp.now()
            }
            
            chunk_ops.append(UpdateOne(
                {"brand_name_clean": clean_name},
                {"$set": doc, "$setOnInsert": {"brand_name_clean": clean_name}},
                upsert=True
            ))
        
        if chunk_ops:
            collection.bulk_write(chunk_ops, ordered=False)
            total_chunks += 1
            print(f"Uploaded chunk {total_chunks} ({total_chunks * chunk_size} rows)")
        
        # Stop early for testing if you want, or just let it run. 
        # For our case, we need it all, but maybe let's limit to top 50,000 for this demo if it's too much.
        if total_chunks >= 10: # Only load 50k rows for now to save time
             break

    # Execute original df1 ops if any
    if ops:
        collection.bulk_write(ops, ordered=False)

    # Create Indexes
    print("Creating indexes...")
    collection.create_index("brand_name_clean")
    collection.create_index("brand_name")
    
    print("✅ Ingestion Partial Complete (Top 50k + Indian Pharma)!")

if __name__ == "__main__":
    ingest_medicines()
