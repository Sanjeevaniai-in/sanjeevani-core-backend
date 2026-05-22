"""
Backfill missing merchant/pharmacy identity fields on consumer_orders.

Usage:
  python scripts/backfill_order_identity.py --merchant-id PHARM_xxx
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime

from pymongo import MongoClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mongo-uri", default=os.getenv("MONGO_URI", "mongodb://localhost:27017"))
    parser.add_argument("--db-name", default=os.getenv("DB_NAME", "sanjeevani_rx_db"))
    parser.add_argument("--merchant-id", default=os.getenv("DEFAULT_PHARMACY_ID", ""))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.merchant_id:
        raise SystemExit("Missing --merchant-id (or DEFAULT_PHARMACY_ID env).")

    client = MongoClient(args.mongo_uri)
    db = client[args.db_name]
    coll = db["consumer_orders"]

    missing_identity_query = {
        "$or": [
            {"merchant_id": {"$exists": False}},
            {"merchant_id": None},
            {"merchant_id": ""},
            {"pharmacy_id": {"$exists": False}},
            {"pharmacy_id": None},
            {"pharmacy_id": ""},
        ]
    }
    result = coll.update_many(
        missing_identity_query,
        {
            "$set": {
                "merchant_id": args.merchant_id,
                "pharmacy_id": args.merchant_id,
                "updated_at": datetime.utcnow(),
            }
        },
    )

    coll.create_index([("merchant_id", 1), ("Order Date", -1)], background=True)
    coll.create_index(
        [("source_channel", 1), ("source_message_id", 1)],
        unique=True,
        sparse=True,
        background=True,
    )

    print(f"Updated documents: {result.modified_count}")


if __name__ == "__main__":
    main()
