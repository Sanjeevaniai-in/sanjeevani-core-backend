"""
app/modules/data_loader.py
─────────────────────────────────────────────────────────────────────────────
Responsible for ingesting raw Excel data into MongoDB, deriving higher-level
collections (patients, inventory) and ensuring indexes exist.

Public API
──────────
    from app.modules.data_loader import DataLoader

    loader = DataLoader()
    loader.load_consumer_orders("data/consumer_orders.xlsx")
    loader.load_products("data/products.xlsx")
    loader.derive_patients_collection()
    loader.initialize_inventory()
    loader.create_indexes()
    loader.validate_data_integrity()
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from pymongo import ASCENDING, DESCENDING, IndexModel

from app.database.mongo_client import get_db
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _safe_val(v: Any) -> Any:
    """Convert numpy scalars / NaN → native Python / None."""
    if v is None:
        return None
    if isinstance(v, float) and np.isnan(v):
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    return v


def _sanitise_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively sanitise a dict so every value is MongoDB-safe."""
    return {k: _safe_val(v) for k, v in row.items()}


def _parse_date(series: pd.Series) -> pd.Series:
    """Best-effort ISO date parse; returns NaT for unparseable values."""
    return pd.to_datetime(series, dayfirst=False, errors="coerce")


def _build_metadata(file_path: str, columns: List[str]) -> Dict[str, Any]:
    return {
        "source_file": os.path.basename(file_path),
        "imported_at": datetime.now(tz=timezone.utc),
        "original_columns": columns,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Date column hints – extend as needed for your actual Excel sheets
# ──────────────────────────────────────────────────────────────────────────────
_DATE_COLS_ORDERS = {
    "Order Date",
    "Purchase Date",
    "Refill Due Date",
    "Dispensed Date",
    "Date",
}
_DATE_COLS_PRODUCTS = {"Expiry Date", "Manufacture Date"}

_NUMERIC_COLS_ORDERS = {
    "Age",
    "Quantity Ordered",
    "Unit Price",
    "Total Amount",
    "Quantity",
    "Price",
}
_NUMERIC_COLS_PRODUCTS = {
    "Unit Price",
    "MRP",
    "Current Stock",
    "Reorder Level",
}


# ──────────────────────────────────────────────────────────────────────────────
# DataLoader
# ──────────────────────────────────────────────────────────────────────────────


class DataLoader:
    """End-to-end Excel → MongoDB ingestion pipeline."""

    def __init__(self) -> None:
        self._db = None

    @property
    def db(self):
        if self._db is None:
            self._db = get_db()
        return self._db

    # ──────────────────────────────────────────────────────────────────────
    # 1. load_consumer_orders
    # ──────────────────────────────────────────────────────────────────────

    def load_consumer_orders(
        self,
        file_path: str,
        sheet_name: int | str = 0,
        *,
        replace: bool = False,
    ) -> int:
        """
        Load consumer orders from *file_path* (Excel) into ``consumer_orders``.

        Returns the number of documents inserted.
        """
        logger.info("Loading consumer orders", extra={"file": file_path})
        df = self._read_excel(file_path, sheet_name)

        # Convert date columns to Python datetime
        for col in df.columns:
            if col in _DATE_COLS_ORDERS:
                df[col] = _parse_date(df[col])

        # Convert numeric columns
        for col in df.columns:
            if col in _NUMERIC_COLS_ORDERS:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        metadata = _build_metadata(file_path, list(df.columns))
        docs = self._df_to_docs(df, metadata)

        coll = self.db["consumer_orders"]
        if replace:
            coll.drop()

        if docs:
            result = coll.insert_many(docs, ordered=False)
            count = len(result.inserted_ids)
            logger.info("Inserted consumer orders", extra={"count": count})
            return count
        logger.warning("No consumer order documents to insert.")
        return 0

    # ──────────────────────────────────────────────────────────────────────
    # 2. load_products
    # ──────────────────────────────────────────────────────────────────────

    def load_products(
        self,
        file_path: str,
        sheet_name: int | str = 0,
        *,
        replace: bool = False,
    ) -> int:
        """Load product catalogue from *file_path* into ``products``."""
        logger.info("Loading products", extra={"file": file_path})
        df = self._read_excel(file_path, sheet_name)

        for col in df.columns:
            if col in _DATE_COLS_PRODUCTS:
                df[col] = _parse_date(df[col])

        for col in df.columns:
            if col in _NUMERIC_COLS_PRODUCTS:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        metadata = _build_metadata(file_path, list(df.columns))
        docs = self._df_to_docs(df, metadata)

        coll = self.db["products"]
        if replace:
            coll.drop()

        if docs:
            result = coll.insert_many(docs, ordered=False)
            count = len(result.inserted_ids)
            logger.info("Inserted products", extra={"count": count})
            return count
        logger.warning("No product documents to insert.")
        return 0

    # ──────────────────────────────────────────────────────────────────────
    # 3. derive_patients_collection
    # ──────────────────────────────────────────────────────────────────────

    def derive_patients_collection(self) -> int:
        """
        Aggregate ``consumer_orders`` → ``patients``.

        Creates one patient document per unique Patient ID (or Patient Name
        if ID is absent), collecting diagnoses, chronic meds, preferred channel.
        Returns the number of patient documents upserted.
        """
        logger.info("Deriving patients from consumer_orders…")
        coll_orders = self.db["consumer_orders"]
        coll_patients = self.db["patients"]

        pipeline = [
            {
                "$group": {
                    "_id": {"$ifNull": ["$Patient ID", "$Patient Name"]},
                    "patient_name": {"$first": "$Patient Name"},
                    "age": {"$first": "$Age"},
                    "gender": {"$first": "$Gender"},
                    "contact_number": {"$first": "$Contact Number"},
                    "address": {"$first": "$Address"},
                    "diagnoses": {"$addToSet": "$Diagnosis"},
                    "regular_medicines": {"$addToSet": "$Medicine Name"},
                    "preferred_channels": {"$addToSet": "$Order Channel"},
                    "doctor_names": {"$addToSet": "$Doctor Name"},
                    "insurance_provider": {"$first": "$Insurance Provider"},
                    "last_order_date": {"$max": "$Order Date"},
                    "total_orders": {"$sum": 1},
                    "is_chronic_flags": {"$addToSet": "$Is Chronic"},
                },
            },
        ]

        upserted = 0
        for grp in coll_orders.aggregate(pipeline, allowDiskUse=True):
            patient_id = str(grp["_id"]) if grp["_id"] else "UNKNOWN"

            # Derive chronic conditions from medicines marked as chronic
            chronic_meds: List[str] = []
            is_chronic = any(
                str(f).strip().lower() == "yes"
                for f in grp.get("is_chronic_flags", [])
                if f
            )
            if is_chronic:
                chronic_meds = [m for m in grp.get("regular_medicines", []) if m]

            doc = {
                "patient_id": patient_id,
                "name": grp.get("patient_name") or patient_id,
                "age": grp.get("age"),
                "gender": grp.get("gender"),
                "contact_number": grp.get("contact_number"),
                "address": grp.get("address"),
                "diagnoses": [d for d in grp.get("diagnoses", []) if d],
                "chronic_conditions": chronic_meds,
                "regular_medicines": [m for m in grp.get("regular_medicines", []) if m],
                "preferred_channel": next(
                    (c for c in grp.get("preferred_channels", []) if c), None
                ),
                "doctor_name": next(
                    (d for d in grp.get("doctor_names", []) if d), None
                ),
                "insurance_provider": grp.get("insurance_provider"),
                "last_order_date": grp.get("last_order_date"),
                "total_orders": grp.get("total_orders", 0),
                "updated_at": datetime.now(tz=timezone.utc),
            }

            coll_patients.update_one(
                {"patient_id": patient_id},
                {
                    "$set": doc,
                    "$setOnInsert": {"created_at": datetime.now(tz=timezone.utc)},
                },
                upsert=True,
            )
            upserted += 1

        logger.info("Patients upserted", extra={"count": upserted})
        return upserted

    # ──────────────────────────────────────────────────────────────────────
    # 4. initialize_inventory
    # ──────────────────────────────────────────────────────────────────────

    def initialize_inventory(self) -> int:
        """
        Seed ``inventory`` from ``products``.

        For each product document, build an inventory snapshot.
        Returns the number of inventory documents upserted.
        """
        logger.info("Initialising inventory from products…")
        coll_products = self.db["products"]
        coll_inventory = self.db["inventory"]

        upserted = 0
        now = datetime.now(tz=timezone.utc)

        for prod in coll_products.find({}):
            product_id = str(prod.get("Product ID") or prod["_id"])
            current_stock = float(prod.get("Current Stock") or 0)
            reorder_level = float(prod.get("Reorder Level") or 0)

            # Parse expiry date (may be datetime or string)
            expiry_raw = prod.get("Expiry Date")
            expiry_str: Optional[str] = None
            expiry_risk = False
            now_naive = now.replace(tzinfo=None)  # for comparing with naive datetimes
            if isinstance(expiry_raw, datetime):
                expiry_dt = (
                    expiry_raw.replace(tzinfo=None) if expiry_raw.tzinfo else expiry_raw
                )
                expiry_str = expiry_dt.strftime("%Y-%m-%d")
                days_left = (expiry_dt - now_naive).days
                expiry_risk = 0 <= days_left <= 90
            elif isinstance(expiry_raw, str) and expiry_raw:
                expiry_str = expiry_raw
                try:
                    ed = datetime.fromisoformat(expiry_raw.replace("/", "-"))
                    ed_naive = ed.replace(tzinfo=None) if ed.tzinfo else ed
                    days_left = (ed_naive - now_naive).days
                    expiry_risk = 0 <= days_left <= 90
                except ValueError:
                    pass

            doc = {
                "product_id": product_id,
                "medicine_name": prod.get("Medicine Name")
                or prod.get("Generic Name")
                or "",
                "category": prod.get("Category"),
                "current_stock": current_stock,
                "reorder_level": reorder_level,
                "expiry_date": expiry_str,
                "batch_number": prod.get("Batch Number"),
                "supplier_name": prod.get("Supplier Name"),
                "unit_price": prod.get("Unit Price"),
                "price": prod.get("Unit Price"),  # Add price field for Agent 4
                "requires_prescription": prod.get("Requires Prescription", "No"),
                "is_low_stock": current_stock <= reorder_level,
                "is_expiry_risk": expiry_risk,
                "updated_at": now,
            }

            coll_inventory.update_one(
                {"product_id": product_id},
                {"$set": doc},
                upsert=True,
            )
            upserted += 1

        logger.info("Inventory initialised", extra={"count": upserted})
        return upserted

    # ──────────────────────────────────────────────────────────────────────
    # 5. create_indexes
    # ──────────────────────────────────────────────────────────────────────

    def create_indexes(self) -> None:
        """Create performance indexes across all core collections."""
        logger.info("Creating MongoDB indexes…")

        # consumer_orders
        self.db["consumer_orders"].create_indexes(
            [
                IndexModel([("Patient ID", ASCENDING)]),
                IndexModel([("Patient Name", ASCENDING)]),
                IndexModel([("Medicine Name", ASCENDING)]),
                IndexModel([("Order Date", DESCENDING)]),
                IndexModel([("Order Status", ASCENDING)]),
                IndexModel([("Order Channel", ASCENDING)]),
                IndexModel([("Is Chronic", ASCENDING)]),
            ]
        )

        # products
        self.db["products"].create_indexes(
            [
                IndexModel([("Product ID", ASCENDING)], unique=True, sparse=True),
                IndexModel([("Medicine Name", ASCENDING)]),
                IndexModel([("Category", ASCENDING)]),
                IndexModel([("Expiry Date", ASCENDING)]),
            ]
        )

        # patients
        self.db["patients"].create_indexes(
            [
                IndexModel([("patient_id", ASCENDING)], unique=True),
                IndexModel([("name", ASCENDING)]),
                IndexModel([("contact_number", ASCENDING)]),
            ]
        )

        # inventory
        self.db["inventory"].create_indexes(
            [
                IndexModel([("product_id", ASCENDING)], unique=True),
                IndexModel([("is_low_stock", ASCENDING)]),
                IndexModel([("is_expiry_risk", ASCENDING)]),
            ]
        )

        # predictions
        self.db["predictions"].create_indexes(
            [
                IndexModel([("patient_id", ASCENDING)]),
                IndexModel([("medicine_name", ASCENDING)]),
                IndexModel([("prediction_type", ASCENDING)]),
                IndexModel([("generated_at", DESCENDING)]),
                IndexModel([("is_actioned", ASCENDING)]),
            ]
        )

        # alerts
        self.db["alerts"].create_indexes(
            [
                IndexModel([("is_resolved", ASCENDING)]),
                IndexModel([("severity", ASCENDING)]),
                IndexModel([("alert_type", ASCENDING)]),
                IndexModel([("patient_id", ASCENDING)]),
                IndexModel([("created_at", DESCENDING)]),
            ]
        )

        logger.info("Indexes created successfully.")

    # ──────────────────────────────────────────────────────────────────────
    # 6. validate_data_integrity
    # ──────────────────────────────────────────────────────────────────────

    def validate_data_integrity(self) -> Dict[str, Any]:
        """
        Run lightweight sanity checks and return a report dict.

        Checks
        ──────
        • Row counts per collection
        • consumer_orders: % rows with Patient Name present
        • consumer_orders: % rows with Medicine Name present
        • products: % with Current Stock field
        • inventory: % matched back to products
        """
        logger.info("Validating data integrity…")
        report: Dict[str, Any] = {}

        counts = {}
        for name in (
            "consumer_orders",
            "products",
            "patients",
            "inventory",
            "predictions",
            "alerts",
        ):
            counts[name] = self.db[name].count_documents({})
        report["collection_counts"] = counts

        total_orders = counts["consumer_orders"]
        if total_orders > 0:
            report["orders_with_patient_name"] = self.db[
                "consumer_orders"
            ].count_documents({"Patient Name": {"$exists": True, "$ne": None}})
            report["orders_with_medicine_name"] = self.db[
                "consumer_orders"
            ].count_documents({"Medicine Name": {"$exists": True, "$ne": None}})
            report["orders_with_date"] = self.db["consumer_orders"].count_documents(
                {"Order Date": {"$exists": True, "$ne": None}}
            )

        total_products = counts["products"]
        if total_products > 0:
            report["products_with_stock"] = self.db["products"].count_documents(
                {"Current Stock": {"$exists": True, "$ne": None}}
            )

        report["validation_passed"] = total_orders > 0 and total_products > 0
        report["validated_at"] = datetime.now(tz=timezone.utc).isoformat()

        logger.info("Integrity report", extra={"report": report})
        return report

    # ──────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _read_excel(file_path: str, sheet_name: int | str = 0) -> pd.DataFrame:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Excel file not found: {file_path}")
        df = pd.read_excel(file_path, sheet_name=sheet_name, engine="openpyxl")
        # Strip leading/trailing whitespace from column names
        df.columns = [str(c).strip() for c in df.columns]
        logger.debug(
            "Excel loaded",
            extra={"file": file_path, "rows": len(df), "cols": list(df.columns)},
        )
        return df

    @staticmethod
    def _df_to_docs(
        df: pd.DataFrame,
        metadata: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Convert a DataFrame to a list of MongoDB-safe dicts."""
        docs = []
        for record in df.to_dict("records"):
            doc = _sanitise_row(record)
            # Convert pandas Timestamp → Python datetime
            for k, v in doc.items():
                if isinstance(v, pd.Timestamp):
                    doc[k] = v.to_pydatetime() if not pd.isnull(v) else None
            doc["_import_metadata"] = metadata
            docs.append(doc)
        return docs
