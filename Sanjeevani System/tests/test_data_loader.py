"""
tests/test_data_loader.py
─────────────────────────────────────────────────────────────────────────────
Tests for DataLoader:
  - Row counts match source Excel
  - Original column names are preserved in MongoDB docs
  - _import_metadata is attached
  - Date columns are converted to datetime
  - Patients derived correctly
  - Inventory seeded correctly
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime

import pandas as pd
import pytest

# ── Optional: use a test / in-memory Mongo via mongomock ───────────────────
try:
    import mongomock

    USE_MONGOMOCK = True
except ImportError:
    USE_MONGOMOCK = False


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def sample_orders_xlsx(tmp_path_factory):
    """Write a minimal Excel orders file and return its path."""
    tmp_dir = tmp_path_factory.mktemp("data")
    path = str(tmp_dir / "orders.xlsx")

    df = pd.DataFrame(
        {
            "Patient ID": ["P001", "P001", "P002"],
            "Patient Name": ["Alice Smith", "Alice Smith", "Bob Jones"],
            "Age": [45, 45, 62],
            "Gender": ["Female", "Female", "Male"],
            "Order ID": ["ORD-001", "ORD-002", "ORD-003"],
            "Order Date": ["2024-01-15", "2024-02-15", "2024-01-20"],
            "Medicine Name": [
                "Metformin 500mg",
                "Metformin 500mg",
                "Atorvastatin 10mg",
            ],
            "Medicine Category": ["Antidiabetic", "Antidiabetic", "Statin"],
            "Quantity Ordered": [60.0, 60.0, 30.0],
            "Unit Price": [5.5, 5.5, 8.0],
            "Total Amount": [330.0, 330.0, 240.0],
            "Order Status": ["Fulfilled", "Fulfilled", "Fulfilled"],
            "Order Channel": ["WhatsApp", "WhatsApp", "SMS"],
            "Is Chronic": ["Yes", "Yes", "Yes"],
            "Diagnosis": ["Type 2 Diabetes", "Type 2 Diabetes", "Hyperlipidemia"],
            "Doctor Name": ["Dr. Mehta", "Dr. Mehta", "Dr. Kumar"],
            "Refill Due Date": ["2024-03-15", "2024-04-15", "2024-02-20"],
            "Prescription Required": ["Yes", "Yes", "No"],
            "Dispensed By": ["Riya", "Riya", "Sam"],
            "Payment Method": ["Cash", "UPI", "Insurance"],
            "Insurance Provider": ["None", "None", "StarHealth"],
            "Notes": ["", "", ""],
        }
    )
    df.to_excel(path, index=False, engine="openpyxl")
    return path


@pytest.fixture(scope="module")
def sample_products_xlsx(tmp_path_factory):
    """Write a minimal Excel products file and return its path."""
    tmp_dir = tmp_path_factory.mktemp("data")
    path = str(tmp_dir / "products.xlsx")

    df = pd.DataFrame(
        {
            "Product ID": ["MED001", "MED002"],
            "Medicine Name": ["Metformin 500mg", "Atorvastatin 10mg"],
            "Generic Name": ["Metformin", "Atorvastatin"],
            "Brand Name": ["Glucophage", "Lipitor"],
            "Manufacturer": ["Sun Pharma", "Pfizer"],
            "Category": ["Antidiabetic", "Statin"],
            "Form": ["Tablet", "Tablet"],
            "Strength": ["500mg", "10mg"],
            "Unit Price": [5.5, 8.0],
            "MRP": [6.0, 9.0],
            "Current Stock": [500.0, 200.0],
            "Reorder Level": [100.0, 50.0],
            "Expiry Date": ["2026-12-31", "2025-06-30"],
            "Batch Number": ["B2024A", "B2024B"],
            "Supplier Name": ["MedSupply Co", "PharmaDistrib"],
            "Requires Prescription": ["Yes", "No"],
            "Controlled Substance": ["No", "No"],
        }
    )
    df.to_excel(path, index=False, engine="openpyxl")
    return path


@pytest.fixture(scope="module")
def loader(sample_orders_xlsx, sample_products_xlsx):
    """Return a DataLoader connected to real (or mocked) MongoDB."""
    if USE_MONGOMOCK:
        import mongomock
        from unittest.mock import patch

        mock_client = mongomock.MongoClient()
        with patch("app.database.mongo_client.get_client", return_value=mock_client):
            from app.modules.data_loader import DataLoader

            ldr = DataLoader()
            ldr.load_consumer_orders(sample_orders_xlsx, replace=True)
            ldr.load_products(sample_products_xlsx, replace=True)
            return ldr
    else:
        from app.modules.data_loader import DataLoader

        ldr = DataLoader()
        ldr.load_consumer_orders(sample_orders_xlsx, replace=True)
        ldr.load_products(sample_products_xlsx, replace=True)
        return ldr


# ──────────────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────────────


class TestColumnPreservation:
    """Column names must be preserved exactly as-is in MongoDB."""

    EXPECTED_ORDER_COLS = {
        "Patient ID",
        "Patient Name",
        "Age",
        "Gender",
        "Order ID",
        "Order Date",
        "Medicine Name",
        "Quantity Ordered",
        "Unit Price",
        "Total Amount",
        "Order Status",
        "Order Channel",
        "Is Chronic",
        "Diagnosis",
        "Medicine Category",
    }
    EXPECTED_PRODUCT_COLS = {
        "Product ID",
        "Medicine Name",
        "Generic Name",
        "Brand Name",
        "Category",
        "Form",
        "Strength",
        "Unit Price",
        "MRP",
        "Current Stock",
        "Reorder Level",
        "Expiry Date",
        "Batch Number",
        "Requires Prescription",
    }

    def test_order_columns_preserved(self, loader):
        from app.database.mongo_client import get_db

        doc = get_db()["consumer_orders"].find_one({})
        assert doc is not None, "No orders found in DB"
        for col in self.EXPECTED_ORDER_COLS:
            assert col in doc, f"Column '{col}' missing from consumer_orders document"

    def test_product_columns_preserved(self, loader):
        from app.database.mongo_client import get_db

        doc = get_db()["products"].find_one({})
        assert doc is not None, "No products found in DB"
        for col in self.EXPECTED_PRODUCT_COLS:
            assert col in doc, f"Column '{col}' missing from products document"


class TestRowCounts:
    """Verify MongoDB row counts match the source Excel row counts."""

    def test_orders_row_count(self, loader, sample_orders_xlsx):
        from app.database.mongo_client import get_db

        excel_rows = len(pd.read_excel(sample_orders_xlsx, engine="openpyxl"))
        db_count = get_db()["consumer_orders"].count_documents({})
        assert (
            db_count == excel_rows
        ), f"Expected {excel_rows} orders in DB, found {db_count}"

    def test_products_row_count(self, loader, sample_products_xlsx):
        from app.database.mongo_client import get_db

        excel_rows = len(pd.read_excel(sample_products_xlsx, engine="openpyxl"))
        db_count = get_db()["products"].count_documents({})
        assert (
            db_count == excel_rows
        ), f"Expected {excel_rows} products in DB, found {db_count}"


class TestImportMetadata:
    """Every document must carry ``_import_metadata``."""

    def test_orders_have_metadata(self, loader):
        from app.database.mongo_client import get_db

        count = get_db()["consumer_orders"].count_documents(
            {"_import_metadata": {"$exists": True}}
        )
        total = get_db()["consumer_orders"].count_documents({})
        assert count == total, "Some order docs are missing _import_metadata"

    def test_metadata_has_required_keys(self, loader):
        from app.database.mongo_client import get_db

        doc = get_db()["consumer_orders"].find_one({})
        meta = doc.get("_import_metadata", {})
        assert "source_file" in meta
        assert "imported_at" in meta
        assert "original_columns" in meta
        assert isinstance(meta["original_columns"], list)

    def test_products_have_metadata(self, loader):
        from app.database.mongo_client import get_db

        count = get_db()["products"].count_documents(
            {"_import_metadata": {"$exists": True}}
        )
        total = get_db()["products"].count_documents({})
        assert count == total


class TestDateConversion:
    """Order Date must be stored as Python datetime (ISODate in MongoDB)."""

    def test_order_date_is_datetime(self, loader):
        from app.database.mongo_client import get_db

        doc = get_db()["consumer_orders"].find_one({"Order Date": {"$exists": True}})
        assert doc is not None
        od = doc.get("Order Date")
        assert isinstance(
            od, datetime
        ), f"Order Date should be datetime, got {type(od).__name__}: {od!r}"


class TestDerivedCollections:
    """Derived patients and inventory collections."""

    def test_patients_derived(self, loader):
        from app.database.mongo_client import get_db

        loader.derive_patients_collection()
        count = get_db()["patients"].count_documents({})
        assert count >= 2, "Expected at least 2 distinct patients"

    def test_inventory_seeded(self, loader):
        from app.database.mongo_client import get_db

        loader.initialize_inventory()
        count = get_db()["inventory"].count_documents({})
        assert count >= 2, "Expected at least 2 inventory documents"

    def test_inventory_has_low_stock_flag(self, loader):
        from app.database.mongo_client import get_db

        doc = get_db()["inventory"].find_one({})
        assert "is_low_stock" in doc


class TestDataIntegrity:
    """validate_data_integrity() must return passed=True for valid data."""

    def test_integrity_passed(self, loader):
        report = loader.validate_data_integrity()
        assert report.get("validation_passed") is True

    def test_integrity_counts_positive(self, loader):
        report = loader.validate_data_integrity()
        counts = report.get("collection_counts", {})
        assert counts.get("consumer_orders", 0) > 0
        assert counts.get("products", 0) > 0
