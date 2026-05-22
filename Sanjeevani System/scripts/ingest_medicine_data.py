import pandas as pd
import pymongo
from pymongo import UpdateOne, ASCENDING, TEXT
import os
import sys
from datetime import datetime

# Add app to path to use mongo_client if needed, or just define it here
MONGO_URI = "mongodb+srv://samaypowade9:samay2005@cluster0.jvwb3d8.mongodb.net/?appName=Cluster0"
DB_NAME = "pharmacy_management"

def get_db():
    client = pymongo.MongoClient(MONGO_URI)
    return client[DB_NAME]

def ingest_medicines(file_path, collection_name, name_col, mapping=None):
    db = get_db()
    coll = db[collection_name]
    
    print(f"Reading {file_path}...")
    try:
        df = pd.read_csv(file_path, low_memory=False)
    except Exception as e:
        print(f"Error reading CSV: {e}")
        return

    if mapping:
        df = df.rename(columns=mapping)
    
    print(f"Ingesting {len(df)} records into {collection_name}...")
    
    batch_size = 5000
    ops = []
    count = 0
    
    for _, row in df.iterrows():
        doc = row.to_dict()
        # Clean up NaN
        doc = {k: (v if not pd.isna(v) else None) for k, v in doc.items()}
        
        # Use name as a unique-ish key for upsert in medicine master
        ops.append(UpdateOne({name_col: doc[name_col]}, {"$set": doc}, upsert=True))
        
        if len(ops) >= batch_size:
            coll.bulk_write(ops)
            ops = []
            count += batch_size
            print(f"Processed {count}...")

    if ops:
        coll.bulk_write(ops)
        print(f"Processed {count + len(ops)}.")

    # Create Indexes
    print(f"Creating indexes for {collection_name}...")
    coll.create_index([(name_col, TEXT)])
    coll.create_index([(name_col, ASCENDING)])
    print("Done.")

if __name__ == "__main__":
    # Indian Pharmaceutical Products
    ingest_medicines(
        "d:/My Project/Sanjeevani/indian_pharmaceutical_products_clean.csv",
        "medicine_master",
        "brand_name"
    )
    
    # All Medicine Databased (Substitutes & Side Effects)
    ingest_medicines(
        "d:/My Project/Sanjeevani/all_medicine databased.csv",
        "medicine_substitutes",
        "name"
    )
