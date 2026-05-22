"""
app/api/products.py  –  /api/v1/products
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional
import uuid

from fastapi import APIRouter, HTTPException, Query, Depends, Header
from pymongo import ASCENDING, DESCENDING
from pymongo.collection import ReturnDocument

from pydantic import BaseModel, Field, field_validator
from app.database.mongo_client import get_db
from app.modules.inventory_intelligence import InventoryIntelligenceService
from app.utils.logger import get_logger
from app.utils.security import get_current_user
from app.utils.helpers import build_pagination_response, normalize_list, normalize_record

router = APIRouter(prefix="/products", tags=["Products"])
logger = get_logger(__name__)
_inv = InventoryIntelligenceService()


def _resolve_inventory_name(item: dict) -> str:
    return (
        item.get("medicine_name")
        or item.get("product_name")
        or item.get("Medicine Name")
        or ""
    )


def _resolve_inventory_stock(item: dict) -> float:
    raw = item.get("current_stock")
    if raw is None:
        raw = item.get("Current Stock")
    try:
        return float(raw or 0)
    except (TypeError, ValueError):
        return 0.0


def _resolve_inventory_expiry(item: dict):
    return item.get("expiry_date") or item.get("Expiry Date")


class ProductCreate(BaseModel):
    medicine_name: str
    category: Optional[str] = "General"
    stock: Optional[int] = 0
    generic_name: Optional[str] = None
    brand_name: Optional[str] = None
    supplier_name: Optional[str] = None
    batch_no: Optional[str] = None
    expiry_date: Optional[str] = None
    mrp: Optional[float] = 0.0
    selling_price: Optional[float] = 0.0
    purchase_price: Optional[float] = 0.0
    schedule: Optional[str] = "OTC"
    prescription_required: Optional[bool] = False
    packaging: Optional[Dict[str, Any]] = None
    barcodes: Optional[List[Dict[str, Any]]] = None
    box_price: Optional[float] = None
    strip_price: Optional[float] = None
    unit_price: Optional[float] = None
    box_mrp: Optional[float] = None
    strip_mrp: Optional[float] = None
    unit_mrp: Optional[float] = None
    box_margin_pct: Optional[float] = None
    strip_margin_pct: Optional[float] = None
    unit_margin_pct: Optional[float] = None
    box_to_strip: Optional[int] = Field(default=None, ge=1)
    strip_to_unit: Optional[int] = Field(default=None, ge=1)


class ProductUpdate(BaseModel):
    medicine_name: Optional[str] = None
    category: Optional[str] = None
    generic_name: Optional[str] = None
    brand_name: Optional[str] = None
    supplier_name: Optional[str] = None
    batch_no: Optional[str] = None
    expiry_date: Optional[str] = None
    mrp: Optional[float] = None
    selling_price: Optional[float] = None
    purchase_price: Optional[float] = None
    schedule: Optional[str] = None
    prescription_required: Optional[bool] = None
    packaging: Optional[Dict[str, Any]] = None
    barcodes: Optional[List[Dict[str, Any]]] = None
    box_price: Optional[float] = None
    strip_price: Optional[float] = None
    unit_price: Optional[float] = None
    box_mrp: Optional[float] = None
    strip_mrp: Optional[float] = None
    unit_mrp: Optional[float] = None
    box_margin_pct: Optional[float] = None
    strip_margin_pct: Optional[float] = None
    unit_margin_pct: Optional[float] = None
    box_to_strip: Optional[int] = Field(default=None, ge=1)
    strip_to_unit: Optional[int] = Field(default=None, ge=1)


class PackQuantity(BaseModel):
    box: int = Field(default=0, ge=0)
    strip: int = Field(default=0, ge=0)
    unit: int = Field(default=0, ge=0)


class PackagingLevel(BaseModel):
    level: Literal["unit", "strip", "box"]
    label: Optional[str] = None
    to_base_units: int = Field(..., ge=1)


class StockBatchPayload(BaseModel):
    batch_no: str
    expiry_date: Optional[str] = None
    mfg_date: Optional[str] = None
    qty: PackQuantity
    supplier_id: Optional[str] = None
    purchase_rate_per_base: Optional[float] = None
    mrp_per_base: Optional[float] = None


class StockActionLine(BaseModel):
    product_id: str
    qty: PackQuantity
    batch_no: Optional[str] = None
    expiry_date: Optional[str] = None
    reason_code: Optional[str] = None
    barcode: Optional[str] = None
    supplier_name: Optional[str] = None
    purchase_rate_per_base: Optional[float] = None
    mrp_per_base: Optional[float] = None


class StockActionRequest(BaseModel):
    action: Literal["purchase_in", "sale_out", "return_in", "damage_out", "expiry_out", "adjustment"]
    lines: List[StockActionLine]
    reason_code: str
    reference_type: Optional[str] = None
    reference_id: Optional[str] = None
    notes: Optional[str] = None


class CounterScanRequest(BaseModel):
    session_id: str
    barcode: str
    qty: Optional[PackQuantity] = None


class CounterConfirmRequest(BaseModel):
    session_id: str
    payment_mode: Optional[str] = "cash"
    missed_sale_entry: bool = False
    reason_code: Optional[str] = None
    reference_id: Optional[str] = None


class ReconciliationAdjustLine(BaseModel):
    product_id: str
    physical_qty: PackQuantity
    system_qty: Optional[PackQuantity] = None
    reason_code: str


class ReconciliationAdjustRequest(BaseModel):
    date: str
    lines: List[ReconciliationAdjustLine]


def _ensure_base_packaging() -> Dict[str, Any]:
    return {
        "base_uom": "unit",
        "levels": [{"level": "unit", "label": "Unit", "to_base_units": 1}],
    }


def _convert_expiry_date(value: Optional[str]) -> Optional[str]:
    """Convert MM/YYYY → YYYY-MM-DD (last day of month). Pass-through if already ISO or empty."""
    import calendar, re
    if not value:
        return value
    if re.match(r'^\d{2}/\d{4}$', value):
        month_str, year_str = value.split('/')
        month, year = int(month_str), int(year_str)
        last_day = calendar.monthrange(year, month)[1]
        return f"{year:04d}-{month:02d}-{last_day:02d}"
    return value


def _normalize_packaging(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not raw:
        return _ensure_base_packaging()
    levels = raw.get("levels") or []
    normalized: List[Dict[str, Any]] = []
    for level in levels:
        if not isinstance(level, dict):
            continue
        try:
            parsed = PackagingLevel(**level)
            normalized.append(parsed.model_dump())
        except Exception:
            continue
    if not any(level["level"] == "unit" for level in normalized):
        normalized.insert(0, {"level": "unit", "label": "Unit", "to_base_units": 1})
    normalized = sorted(normalized, key=lambda item: item["to_base_units"])
    return {"base_uom": raw.get("base_uom", "unit"), "levels": normalized}


def _pack_to_base(qty: Dict[str, int], packaging: Dict[str, Any]) -> int:
    levels = {lvl["level"]: int(lvl["to_base_units"]) for lvl in packaging["levels"]}
    return (
        int(qty.get("box", 0)) * int(levels.get("box", 0))
        + int(qty.get("strip", 0)) * int(levels.get("strip", 0))
        + int(qty.get("unit", 0)) * int(levels.get("unit", 1))
    )


def _base_to_pack(base_units: int, packaging: Dict[str, Any]) -> Dict[str, int]:
    levels = sorted(packaging["levels"], key=lambda item: item["to_base_units"], reverse=True)
    remaining = max(int(base_units), 0)
    breakdown = {"box": 0, "strip": 0, "unit": 0}
    for level in levels:
        level_name = level["level"]
        to_base = int(level["to_base_units"])
        if level_name == "unit":
            breakdown["unit"] = remaining
            break
        if to_base > 0:
            count = remaining // to_base
            breakdown[level_name] = count
            remaining -= count * to_base
    return breakdown


def _product_packaging(doc: dict) -> Dict[str, Any]:
    packaging = doc.get("packaging")
    if packaging:
        return _normalize_packaging(packaging)
    box_to_strip = int(doc.get("box_to_strip") or 1)
    strip_to_unit = int(doc.get("strip_to_unit") or 1)
    if box_to_strip > 1 and strip_to_unit > 1:
        return {
            "base_uom": "unit",
            "levels": [
                {"level": "unit", "label": "Unit", "to_base_units": 1},
                {"level": "strip", "label": "Strip", "to_base_units": strip_to_unit},
                {"level": "box", "label": "Box", "to_base_units": box_to_strip * strip_to_unit},
            ],
        }
    return _ensure_base_packaging()


def _build_product_view(doc: dict) -> dict:
    packaging = _product_packaging(doc)
    stock_summary = doc.get("stock_summary") or {}
    available_base_units = int(stock_summary.get("available_base_units", doc.get("Current Stock", doc.get("current_stock", 0)) or 0))
    breakdown = _base_to_pack(available_base_units, packaging)
    doc = dict(doc)
    doc["packaging"] = packaging
    doc["stock_summary"] = {
        "available_base_units": available_base_units,
        "reserved_base_units": int(stock_summary.get("reserved_base_units", 0)),
        "damaged_base_units": int(stock_summary.get("damaged_base_units", 0)),
        "breakdown": breakdown,
    }
    doc["Current Stock"] = available_base_units
    doc["stock_breakdown"] = breakdown
    return normalize_record(doc)


def _get_product_by_identifier(db, product_id: str) -> Optional[dict]:
    return db["products"].find_one(
        {
            "$or": [
                {"Product ID": product_id},
                {"Medicine Name": product_id},
                {"product_id": product_id},
                {"barcodes.code": product_id},
            ]
        }
    )


def _find_product_by_barcode(db, barcode: str) -> Optional[dict]:
    return db["products"].find_one({"barcodes.code": barcode}) or db["barcode_mappings"].find_one({"barcode": barcode})


def _resolve_product_ref(db, barcode: str) -> Optional[dict]:
    mapping = db["barcode_mappings"].find_one({"barcode": barcode})
    if mapping:
        product = _get_product_by_identifier(db, mapping.get("product_id"))
        if product:
            product["_matched_barcode_level"] = mapping.get("level")
            return product
    product = db["products"].find_one({"barcodes.code": barcode})
    if product:
        return product
    return None


def _active_product_query(query: dict) -> dict:
    next_query = dict(query)
    next_query["$and"] = next_query.get("$and", [])
    next_query["$and"].append({"$or": [{"is_deleted": {"$exists": False}}, {"is_deleted": False}]})
    return next_query


def _ledger_exists(db, idempotency_key: str) -> bool:
    return bool(db["inventory_ledger"].find_one({"idempotency_key": idempotency_key}))


def _append_ledger(db, doc: dict) -> None:
    db["inventory_ledger"].insert_one(doc)


def _upsert_batch_stock(db, merchant_id: str, product: dict, line: StockActionLine, base_qty: int, action: str) -> dict:
    batch_no = line.batch_no or f"AUTO-{uuid.uuid4().hex[:8].upper()}"
    expiry_date = line.expiry_date or product.get("Expiry Date") or product.get("expiry_date")
    batch_query = {"merchant_id": merchant_id, "product_id": product.get("Product ID") or product.get("product_id"), "batch_no": batch_no}
    update = {
        "$setOnInsert": {
            "merchant_id": merchant_id,
            "product_id": product.get("Product ID") or product.get("product_id"),
            "batch_no": batch_no,
            "created_at": datetime.utcnow(),
            "available_base_units": 0,
        },
        "$set": {
            "expiry_date": expiry_date,
            "supplier_name": line.supplier_name or product.get("supplier_name") or product.get("Supplier Name"),
            "purchase_rate_per_base": line.purchase_rate_per_base if line.purchase_rate_per_base is not None else product.get("purchase_rate_per_base"),
            "mrp_per_base": line.mrp_per_base if line.mrp_per_base is not None else product.get("mrp_per_base"),
            "updated_at": datetime.utcnow(),
        },
    }
    if action in {"purchase_in", "return_in"}:
        update["$inc"] = {"available_base_units": base_qty}
    else:
        update["$inc"] = {"available_base_units": -base_qty}
    result = db["stock_batches"].find_one_and_update(
        batch_query,
        update,
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    if not result:
        raise HTTPException(status_code=500, detail="Failed to update stock batch")
    return result


def _update_product_stock(db, product_key: str, delta: int) -> None:
    db["products"].update_one(
        {"$or": [{"Product ID": product_key}, {"product_id": product_key}]},
        {
            "$inc": {
                "stock_summary.available_base_units": delta,
                "Current Stock": delta,
                "current_stock": delta,
            },
            "$set": {"last_updated": datetime.utcnow(), "updated_at": datetime.utcnow()},
        },
    )


def _fefo_allocate(db, merchant_id: str, product_key: str, base_qty: int) -> List[dict]:
    remaining = base_qty
    allocations: List[dict] = []
    batches = list(
        db["stock_batches"]
        .find(
            {
                "merchant_id": merchant_id,
                "product_id": product_key,
                "available_base_units": {"$gt": 0},
            }
        )
        .sort([("expiry_date", ASCENDING), ("updated_at", ASCENDING)])
    )
    for batch in batches:
        if remaining <= 0:
            break
        available = int(batch.get("available_base_units", 0))
        if available <= 0:
            continue
        take = min(available, remaining)
        db["stock_batches"].update_one(
            {"_id": batch["_id"]},
            {"$inc": {"available_base_units": -take}, "$set": {"updated_at": datetime.utcnow()}},
        )
        allocations.append({"batch_id": str(batch["_id"]), "batch_no": batch.get("batch_no"), "qty_base_units": take})
        remaining -= take
    if remaining > 0:
        raise HTTPException(status_code=400, detail="INSUFFICIENT_STOCK")
    return allocations


@router.post("/", summary="Add a new product")
def add_product(product: ProductCreate, user: dict = Depends(get_current_user)):
    """Manually add a product to the catalog."""
    db = get_db()
    merchant_id = user["merchant_id"]
    packaging = _normalize_packaging(product.packaging)
    levels = packaging["levels"]

    # Check for duplicates across name, generic name, and barcode mappings
    existing = db["products"].find_one(
        {
            "merchant_id": merchant_id,
            "$or": [
                {"Medicine Name": {"$regex": f"^{product.medicine_name}$", "$options": "i"}},
                {"medicine_name": {"$regex": f"^{product.medicine_name}$", "$options": "i"}},
            ],
        }
    )
    if existing:
        raise HTTPException(
            status_code=400, detail=f"Product '{product.medicine_name}' already exists."
        )

    product_id = f"M-{db['products'].count_documents({'merchant_id': merchant_id}) + 1000}"
    base_stock = int(product.stock or 0)
    stock_breakdown = _base_to_pack(base_stock, packaging)
    barcodes = product.barcodes or []
    if not any(b.get("level") == "unit" for b in barcodes):
        barcodes.append({"code": f"{product_id}-UNIT", "level": "unit", "is_primary": True})

    expiry_stored = _convert_expiry_date(product.expiry_date)
    new_doc = {
        "Medicine Name": product.medicine_name,
        "medicine_name": product.medicine_name,
        "Category": product.category,
        "category": product.category,
        "Current Stock": product.stock,
        "current_stock": product.stock,
        "Generic Name": product.generic_name,
        "generic_name": product.generic_name,
        "Brand Name": product.brand_name,
        "brand_name": product.brand_name,
        "Supplier Name": product.supplier_name,
        "supplier_name": product.supplier_name,
        "Batch Number": product.batch_no,
        "batch_no": product.batch_no,
        "Expiry Date": expiry_stored,
        "expiry_date": expiry_stored,
        "MRP": product.mrp,
        "mrp": product.mrp,
        "Selling Price": product.selling_price,
        "selling_price": product.selling_price,
        "Purchase Price": product.purchase_price,
        "purchase_price": product.purchase_price,
        "Schedule": product.schedule,
        "schedule": product.schedule,
        "Prescription Required": product.prescription_required,
        "prescription_required": product.prescription_required,
        "Product ID": product_id,
        "product_id": product_id,
        "merchant_id": merchant_id,
        "tenant_id": merchant_id,
        "Safety Check": "Validated",
        "packaging": packaging,
        "barcodes": barcodes,
        "stock_summary": {
            "available_base_units": base_stock,
            "reserved_base_units": 0,
            "damaged_base_units": 0,
            "breakdown": stock_breakdown,
        },
        "last_updated": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }

    db["products"].insert_one(new_doc)
    if base_stock > 0:
        auto_batch_no = product.batch_no or f"BATCH-A-{uuid.uuid4().hex[:6].upper()}"
        batch_doc = {
            "merchant_id": merchant_id,
            "tenant_id": merchant_id,
            "product_id": product_id,
            "batch_no": auto_batch_no,
            "expiry_date": expiry_stored,
            "mfg_date": None,
            "available_base_units": base_stock,
            "purchase_rate_per_base": product.purchase_price or 0,
            "mrp_per_base": product.mrp or product.selling_price or 0,
            "supplier_name": product.supplier_name,
            "status": "active",
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        db["stock_batches"].insert_one(batch_doc)
        _append_ledger(
            db,
            {
                "tenant_id": merchant_id,
                "merchant_id": merchant_id,
                "product_id": product_id,
                "batch_no": batch_doc["batch_no"],
                "txn_type": "purchase_in",
                "direction": "in",
                "qty_base_units": base_stock,
                "reason_code": "initial_stock",
                "reference_type": "product_create",
                "reference_id": product_id,
                "idempotency_key": f"seed-{product_id}",
                "actor": {"user_id": user.get("user_id"), "role": user.get("role")},
                "before_qty": 0,
                "after_qty": base_stock,
                "created_at": datetime.utcnow(),
            },
        )
    return {
        "status": "ok",
        "message": "Product added successfully",
        "product_id": new_doc["Product ID"],
    }


@router.post("/bulk", summary="Bulk add products")
def bulk_add_products(products: list[ProductCreate], user: dict = Depends(get_current_user)):
    """Bulk add products to the catalog."""
    db = get_db()
    count = db["products"].count_documents({"merchant_id": user["merchant_id"]})

    new_docs = []
    skipped = []
    for i, p in enumerate(products):
        existing = db["products"].find_one(
            {
                "merchant_id": user["merchant_id"],
                "Medicine Name": {"$regex": f"^{p.medicine_name}$", "$options": "i"},
            }
        )
        if existing:
            skipped.append({"medicine_name": p.medicine_name, "reason": "duplicate"})
            continue
        packaging = _normalize_packaging(p.packaging)
        stock_breakdown = _base_to_pack(int(p.stock or 0), packaging)
        product_id = f"M-{count + 1000 + len(new_docs)}"

        new_docs.append({
            "Medicine Name": p.medicine_name,
            "medicine_name": p.medicine_name,
            "Category": p.category,
            "category": p.category,
            "Current Stock": p.stock,
            "current_stock": p.stock,
            "Generic Name": p.generic_name,
            "generic_name": p.generic_name,
            "Brand Name": p.brand_name,
            "brand_name": p.brand_name,
            "Supplier Name": p.supplier_name,
            "supplier_name": p.supplier_name,
            "Batch Number": p.batch_no,
            "batch_no": p.batch_no,
            "Expiry Date": _convert_expiry_date(p.expiry_date),
            "expiry_date": _convert_expiry_date(p.expiry_date),
            "MRP": p.mrp,
            "mrp": p.mrp,
            "Selling Price": p.selling_price,
            "selling_price": p.selling_price,
            "Purchase Price": p.purchase_price,
            "purchase_price": p.purchase_price,
            "Schedule": p.schedule,
            "schedule": p.schedule,
            "Prescription Required": p.prescription_required,
            "prescription_required": p.prescription_required,
            "Product ID": product_id,
            "product_id": product_id,
            "merchant_id": user["merchant_id"],
            "tenant_id": user["merchant_id"],
            "Safety Check": "Validated",
            "packaging": packaging,
            "barcodes": p.barcodes or [{"code": f"{product_id}-UNIT", "level": "unit", "is_primary": True}],
            "stock_summary": {
                "available_base_units": int(p.stock or 0),
                "reserved_base_units": 0,
                "damaged_base_units": 0,
                "breakdown": stock_breakdown,
            },
            "last_updated": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        })

    if new_docs:
        db["products"].insert_many(new_docs)
        batch_docs = []
        for doc in new_docs:
            if int(doc.get("Current Stock") or 0) > 0:
                batch_docs.append({
                    "merchant_id": user["merchant_id"],
                    "tenant_id": user["merchant_id"],
                    "product_id": doc["product_id"],
                    "batch_no": doc.get("batch_no") or f"BATCH-A-{uuid.uuid4().hex[:6].upper()}",
                    "expiry_date": doc.get("expiry_date"),
                    "supplier_name": doc.get("supplier_name"),
                    "available_base_units": int(doc.get("Current Stock") or 0),
                    "purchase_rate_per_base": doc.get("purchase_price") or 0,
                    "mrp_per_base": doc.get("mrp") or doc.get("selling_price") or 0,
                    "status": "active",
                    "created_at": datetime.utcnow(),
                    "updated_at": datetime.utcnow(),
                })
        if batch_docs:
            db["stock_batches"].insert_many(batch_docs)

    return {
        "status": "ok",
        "added": len(new_docs),
        "skipped": skipped,
        "message": f"Successfully added {len(new_docs)} products. {len(skipped)} skipped (duplicates).",
    }


@router.post("/bulk-import-preview", summary="Dry-run preview for bulk import")
def bulk_import_preview_route(products: list[ProductCreate], user: dict = Depends(get_current_user)):
    """Check which products are new vs duplicates before committing a bulk import."""
    db = get_db()
    new_list, duplicates = [], []
    for p in products:
        if not p.medicine_name:
            continue
        existing = db["products"].find_one({
            "merchant_id": user["merchant_id"],
            "$or": [
                {"Medicine Name": {"$regex": f"^{p.medicine_name}$", "$options": "i"}},
                {"medicine_name": {"$regex": f"^{p.medicine_name}$", "$options": "i"}},
            ]
        })
        if existing:
            duplicates.append({
                "medicine_name": p.medicine_name,
                "existing_product_id": existing.get("Product ID") or existing.get("product_id"),
            })
        else:
            new_list.append({"medicine_name": p.medicine_name})
    return {"status": "ok", "new": new_list, "duplicates": duplicates, "total": len(products)}


@router.get("/", summary="List all products")
def list_products(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=1000),
    sort_by: str = Query(default="Medicine Name"),
    sort_order: str = Query(default="asc", regex="^(asc|desc)$"),
    search: str = Query(default=""),
    category: str = Query(default=""),
    user: dict = Depends(get_current_user),
):
    """Paginated product catalogue with search + category filter."""
    db = get_db()
    query: dict = _active_product_query({"merchant_id": user["merchant_id"]})
    if search:
        rx = {"$regex": f".*{search}.*", "$options": "i"}
        query["$or"] = [
            {"Medicine Name": rx}, {"medicine_name": rx},
            {"Generic Name": rx}, {"generic_name": rx},
            {"Brand Name": rx}, {"brand_name": rx},
            {"barcodes.code": rx},
            {"salt_composition": rx},
        ]
    if category:
        query["$or"] = [
            {"Category": {"$regex": f".*{category}.*", "$options": "i"}},
            {"category": {"$regex": f".*{category}.*", "$options": "i"}},
        ]

    skip = (page - 1) * page_size
    sort_dir = ASCENDING if sort_order == "asc" else DESCENDING
    total = db["products"].count_documents(query)
    items = list(
        db["products"]
        .find(query, {"_id": 0})
        .sort(sort_by, sort_dir)
        .skip(skip)
        .limit(page_size)
    )
    items = [_build_product_view(item) for item in items]
    return {
        "status": "ok",
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": -(-total // page_size),
        "data": items,
    }


@router.get("/low-stock", summary="Low-stock items")
def low_stock(user: dict = Depends(get_current_user)):
    """Return products where current stock is < average weekly sales"""
    db = get_db()

    # Calculate average weekly sales from consumer_orders
    orders = list(db["consumer_orders"].find({"merchant_id": user["merchant_id"]}))
    product_sales = {}
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)

    for o in orders:
        p_name = o.get("product_name") or o.get("Medicine Name")
        if not p_name:
            continue

        try:
            qty = float(o.get("quantity") or o.get("Quantity", 1))
        except (ValueError, TypeError):
            qty = 1.0

        raw_date = o.get("purchase_date") or o.get("Order Date")

        dt = None
        if isinstance(raw_date, (int, float)):
            dt = datetime(1899, 12, 30) + timedelta(days=float(raw_date))
        elif isinstance(raw_date, datetime):
            dt = raw_date
        elif isinstance(raw_date, str):
            try:
                dt = datetime.fromisoformat(str(raw_date))
            except ValueError:
                dt = now
        else:
            dt = now

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        if p_name not in product_sales:
            product_sales[p_name] = {"total": 0, "min_dt": dt, "max_dt": dt}

        product_sales[p_name]["total"] += qty
        if dt < product_sales[p_name]["min_dt"]:
            product_sales[p_name]["min_dt"] = dt
        if dt > product_sales[p_name]["max_dt"]:
            product_sales[p_name]["max_dt"] = dt

    weekly_sales = {}
    for p_name, data in product_sales.items():
        days_span = (data["max_dt"] - data["min_dt"]).days
        weeks = max(days_span / 7.0, 1.0)
        weekly_sales[p_name] = data["total"] / weeks

    low_stock_items = []
    # Try inventory collection first, then products
    items = list(db["inventory"].find({"merchant_id": user["merchant_id"]}))
    if not items:
        # Fallback to products if inventory is empty
        items = list(db["products"].find(_active_product_query({"merchant_id": user["merchant_id"]})))

    for item in items:
        name = _resolve_inventory_name(item)
        if not name:
            continue

        avg_weekly = weekly_sales.get(name, 0)
        stock = _resolve_inventory_stock(item)

        if stock < avg_weekly:
            item["urgency"] = "critical" if stock == 0 else "high"
            item["avg_weekly_sales"] = avg_weekly
            item["medicine_name"] = name
            item["current_stock"] = stock
            item["_id"] = str(item.get("_id", ""))
            low_stock_items.append(item)

    return {"status": "ok", "data": normalize_list(low_stock_items)}


@router.get("/expiry-risk", summary="Expiry risk items")
def expiry_risk(days: int = Query(default=90, ge=1, le=365), user: dict = Depends(get_current_user)):
    """Return products that have more stock than can be sold before expiry based on avg weekly sales."""
    db = get_db()
    orders = list(db["consumer_orders"].find({"merchant_id": user["merchant_id"]}))

    product_sales = {}
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)

    for o in orders:
        p_name = o.get("product_name") or o.get("Medicine Name")
        if not p_name:
            continue

        try:
            qty = float(o.get("quantity") or o.get("Quantity", 1))
        except (ValueError, TypeError):
            qty = 1.0

        raw_date = o.get("purchase_date") or o.get("Order Date")

        dt = None
        if isinstance(raw_date, (int, float)):
            dt = datetime(1899, 12, 30) + timedelta(days=float(raw_date))
        elif isinstance(raw_date, datetime):
            dt = raw_date
        elif isinstance(raw_date, str):
            try:
                dt = datetime.fromisoformat(str(raw_date))
            except ValueError:
                dt = now
        else:
            dt = now

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        if p_name not in product_sales:
            product_sales[p_name] = {"total": 0, "min_dt": dt, "max_dt": dt}

        product_sales[p_name]["total"] += qty
        if dt < product_sales[p_name]["min_dt"]:
            product_sales[p_name]["min_dt"] = dt
        if dt > product_sales[p_name]["max_dt"]:
            product_sales[p_name]["max_dt"] = dt

    weekly_sales = {}
    for p_name, data in product_sales.items():
        days_span = (data["max_dt"] - data["min_dt"]).days
        weeks = max(days_span / 7.0, 1.0)
        weekly_sales[p_name] = data["total"] / weeks

    risk_items = []
    items = list(db["inventory"].find({"merchant_id": user["merchant_id"]}))
    if not items:
        items = list(db["products"].find(_active_product_query({"merchant_id": user["merchant_id"]})))

    for item in items:
        name = _resolve_inventory_name(item)
        if not name:
            continue

        stock = _resolve_inventory_stock(item)
        if stock <= 0:
            continue

        exp_raw = _resolve_inventory_expiry(item)
        if not exp_raw:
            continue

        try:
            if isinstance(exp_raw, str):
                exp_dt = datetime.fromisoformat(exp_raw.replace("/", "-"))
            elif isinstance(exp_raw, datetime):
                exp_dt = exp_raw
            else:
                continue
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)

            days_left = (exp_dt - now).days
            if days_left <= 0:
                item["urgency"] = "critical"
                item["medicine_name"] = name
                item["current_stock"] = stock
                item["_id"] = str(item.get("_id", ""))
                risk_items.append(item)
                continue

            weeks_left = days_left / 7.0
            avg_weekly = weekly_sales.get(name, 0)

            projected_sales = weeks_left * avg_weekly
            if stock > projected_sales:
                item["urgency"] = "high"
                item["projected_sales"] = projected_sales
                item["days_until_expiry"] = days_left
                item["medicine_name"] = name
                item["current_stock"] = stock
                item["_id"] = str(item.get("_id", ""))
                risk_items.append(item)

        except (ValueError, TypeError):
            continue

    return {"status": "ok", "data": normalize_list(risk_items)}


@router.get("/reorder-recommendations", summary="Reorder recommendations")
def reorder_recommendations(user: dict = Depends(get_current_user)):
    """Recommended restocking quantities for all low-stock items."""
    return {"status": "ok", "data": _inv.get_reorder_recommendations()}


@router.get("/movement-patterns", summary="Sales velocity classification")
def movement_patterns(user: dict = Depends(get_current_user)):
    """Classify products as fast / medium / slow / no movement."""
    return {"status": "ok", "data": _inv.analyze_movement_patterns()}


@router.get("/{product_id}", summary="Get single product")
def get_product(product_id: str, user: dict = Depends(get_current_user)):
    """Fetch one product by Product ID or Medicine Name."""
    db = get_db()
    prod = _get_product_by_identifier(db, product_id)
    if not prod:
        raise HTTPException(
            status_code=404, detail=f"Product '{product_id}' not found."
        )
    if prod.get("is_deleted"):
        raise HTTPException(
            status_code=404, detail=f"Product '{product_id}' not found."
        )
    return {"status": "ok", "data": _build_product_view(prod)}


@router.patch("/{product_id}", summary="Update product")
def update_product(
    product_id: str,
    payload: ProductUpdate,
    user: dict = Depends(get_current_user),
):
    db = get_db()
    product = _get_product_by_identifier(db, product_id)
    if not product or product.get("is_deleted"):
        raise HTTPException(status_code=404, detail=f"Product '{product_id}' not found.")

    update_doc: dict = {}
    if payload.medicine_name is not None:
        update_doc["Medicine Name"] = payload.medicine_name
        update_doc["medicine_name"] = payload.medicine_name
    if payload.category is not None:
        update_doc["Category"] = payload.category
        update_doc["category"] = payload.category
    if payload.generic_name is not None:
        update_doc["Generic Name"] = payload.generic_name
        update_doc["generic_name"] = payload.generic_name
    if payload.brand_name is not None:
        update_doc["Brand Name"] = payload.brand_name
        update_doc["brand_name"] = payload.brand_name
    if payload.supplier_name is not None:
        update_doc["Supplier Name"] = payload.supplier_name
        update_doc["supplier_name"] = payload.supplier_name
    if payload.batch_no is not None:
        update_doc["Batch Number"] = payload.batch_no
        update_doc["batch_no"] = payload.batch_no
    if payload.expiry_date is not None:
        stored_exp = _convert_expiry_date(payload.expiry_date)
        update_doc["Expiry Date"] = stored_exp
        update_doc["expiry_date"] = stored_exp
    if payload.mrp is not None:
        update_doc["MRP"] = payload.mrp
        update_doc["mrp"] = payload.mrp
    if payload.selling_price is not None:
        update_doc["Selling Price"] = payload.selling_price
        update_doc["selling_price"] = payload.selling_price
    if payload.purchase_price is not None:
        update_doc["Purchase Price"] = payload.purchase_price
        update_doc["purchase_price"] = payload.purchase_price
    if payload.schedule is not None:
        update_doc["Schedule"] = payload.schedule
        update_doc["schedule"] = payload.schedule
    if payload.prescription_required is not None:
        update_doc["Prescription Required"] = payload.prescription_required
        update_doc["prescription_required"] = payload.prescription_required
    if payload.barcodes is not None:
        update_doc["barcodes"] = payload.barcodes
    packaging = _product_packaging(product)
    if payload.packaging is not None:
        packaging = _normalize_packaging(payload.packaging)
        update_doc["packaging"] = packaging
    current_stock = int(product.get("stock_summary", {}).get("available_base_units", product.get("Current Stock", 0)) or 0)
    update_doc["stock_summary"] = {
        "available_base_units": current_stock,
        "reserved_base_units": int(product.get("stock_summary", {}).get("reserved_base_units", 0)),
        "damaged_base_units": int(product.get("stock_summary", {}).get("damaged_base_units", 0)),
        "breakdown": _base_to_pack(current_stock, packaging),
    }
    update_doc["Current Stock"] = current_stock
    update_doc["current_stock"] = current_stock
    update_doc["stock_breakdown"] = _base_to_pack(current_stock, packaging)
    update_doc["updated_at"] = datetime.utcnow()
    update_doc["last_updated"] = datetime.utcnow()

    db["products"].update_one(
        {"merchant_id": user["merchant_id"], "Product ID": product.get("Product ID") or product.get("product_id")},
        {"$set": update_doc},
    )
    refreshed = _get_product_by_identifier(db, product_id)
    return {"status": "ok", "message": "Product updated successfully", "data": _build_product_view(refreshed or product)}


@router.delete("/{product_id}", summary="Delete product")
def delete_product(product_id: str, user: dict = Depends(get_current_user)):
    db = get_db()
    product = _get_product_by_identifier(db, product_id)
    if not product or product.get("is_deleted"):
        raise HTTPException(status_code=404, detail=f"Product '{product_id}' not found.")
    product_key = product.get("Product ID") or product.get("product_id") or product_id
    db["products"].update_one(
        {"merchant_id": user["merchant_id"], "Product ID": product_key},
        {"$set": {"is_deleted": True, "deleted_at": datetime.utcnow(), "updated_at": datetime.utcnow()}},
    )
    return {"status": "ok", "message": "Product deleted successfully"}


@router.get("/search", summary="Global product search")
def search_products(
    q: Optional[str] = Query(default=None),
    barcode: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    db = get_db()
    query: dict = _active_product_query({"merchant_id": user["merchant_id"]})
    if barcode:
        query["$or"] = [{"barcodes.code": barcode}, {"barcode": barcode}]
    elif q:
        query["$or"] = [
            {"Medicine Name": {"$regex": q, "$options": "i"}},
            {"Generic Name": {"$regex": q, "$options": "i"}},
            {"Brand Name": {"$regex": q, "$options": "i"}},
            {"salt_composition": {"$regex": q, "$options": "i"}},
            {"barcodes.code": {"$regex": q, "$options": "i"}},
        ]
    items = list(db["products"].find(query, {"_id": 0}).limit(limit))
    return {"status": "ok", "data": [_build_product_view(item) for item in items]}


@router.get("/{product_id}/forecast", summary="Demand forecast")
def demand_forecast(product_id: str, days: int = Query(default=30, ge=1, le=365), user: dict = Depends(get_current_user)):
    """SMA-based demand forecast for a product."""
    try:
        data = _inv.forecast_demand(product_id, days=days)
        return {"status": "ok", "data": data}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{product_id}/trend", summary="Demand trend")
def demand_trend(product_id: str, user: dict = Depends(get_current_user)):
    """Monthly demand trend (increasing / stable / decreasing)."""
    try:
        return {"status": "ok", "data": _inv.analyze_demand_trend(product_id)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{product_id}/batches", summary="List stock batches")
def list_batches(product_id: str, user: dict = Depends(get_current_user)):
    db = get_db()
    product = _get_product_by_identifier(db, product_id)
    if product and product.get("is_deleted"):
        raise HTTPException(status_code=404, detail=f"Product '{product_id}' not found.")
    product_key = product.get("Product ID") if product else product_id
    batches = list(
        db["stock_batches"]
        .find(
            {
                "merchant_id": user["merchant_id"],
                "product_id": {"$in": [product_id, product_key]},
            },
            {"_id": 0},
        )
        .sort([("expiry_date", ASCENDING), ("updated_at", ASCENDING)])
    )
    return {"status": "ok", "data": batches}


@router.get("/{product_id}/ledger", summary="Stock ledger")
def product_ledger(
    product_id: str,
    from_date: Optional[str] = Query(default=None),
    to_date: Optional[str] = Query(default=None),
    user: dict = Depends(get_current_user),
):
    db = get_db()
    query: dict = {"tenant_id": user["merchant_id"], "product_id": {"$in": [product_id]}}
    if from_date:
        query["created_at"] = query.get("created_at", {})
        query["created_at"]["$gte"] = datetime.fromisoformat(from_date)
    if to_date:
        query["created_at"] = query.get("created_at", {})
        query["created_at"]["$lte"] = datetime.fromisoformat(to_date)
    items = list(db["inventory_ledger"].find(query, {"_id": 0}).sort("created_at", DESCENDING))
    return {"status": "ok", "data": items}


@router.post("/stock-actions", summary="Apply stock movement")
def stock_actions(
    payload: StockActionRequest,
    user: dict = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    db = get_db()
    idempotency_key = idempotency_key or f"{user['merchant_id']}:{payload.reference_type or payload.action}:{payload.reference_id or uuid.uuid4().hex}"
    if _ledger_exists(db, idempotency_key):
        return {"status": "ok", "message": "Idempotent replay", "idempotency_key": idempotency_key}

    results = []
    for line in payload.lines:
        product = _get_product_by_identifier(db, line.product_id)
        if not product:
            raise HTTPException(status_code=404, detail=f"Product '{line.product_id}' not found")
        if product.get("is_deleted"):
            raise HTTPException(status_code=404, detail=f"Product '{line.product_id}' not found")
        packaging = _product_packaging(product)
        base_qty = _pack_to_base(line.qty.model_dump(), packaging)
        if base_qty <= 0:
            continue
        product_key = product.get("Product ID") or product.get("product_id")
        before_qty = int(product.get("stock_summary", {}).get("available_base_units", product.get("Current Stock", 0)) or 0)
        if payload.action in {"sale_out", "damage_out", "expiry_out"} and before_qty < base_qty:
            raise HTTPException(status_code=400, detail="INSUFFICIENT_STOCK")
        if payload.action in {"sale_out", "damage_out", "expiry_out"}:
            if line.batch_no:
                batch_doc = db["stock_batches"].find_one(
                    {
                        "merchant_id": user["merchant_id"],
                        "product_id": product_key,
                        "batch_no": line.batch_no,
                        "available_base_units": {"$gt": 0},
                    }
                )
                if not batch_doc:
                    raise HTTPException(status_code=404, detail=f"Batch '{line.batch_no}' not found")
                available = int(batch_doc.get("available_base_units", 0))
                if available < base_qty:
                    raise HTTPException(status_code=400, detail="INSUFFICIENT_STOCK")
                db["stock_batches"].update_one(
                    {"_id": batch_doc["_id"]},
                    {"$inc": {"available_base_units": -base_qty}, "$set": {"updated_at": datetime.utcnow()}},
                )
                allocations = [{"batch_id": str(batch_doc["_id"]), "batch_no": batch_doc.get("batch_no"), "qty_base_units": base_qty}]
            else:
                allocations = _fefo_allocate(db, user["merchant_id"], product_key, base_qty)
            txn_type = payload.action
            delta = -base_qty
        else:
            allocations = []
            batch = _upsert_batch_stock(db, user["merchant_id"], product, line, base_qty, payload.action)
            txn_type = payload.action
            delta = base_qty
            allocations.append({"batch_id": str(batch["_id"]), "batch_no": batch.get("batch_no"), "qty_base_units": base_qty})
        _update_product_stock(db, product_key, delta)
        after_qty = before_qty + delta
        _append_ledger(
            db,
            {
                "tenant_id": user["merchant_id"],
                "merchant_id": user["merchant_id"],
                "product_id": product_key,
                "txn_type": txn_type,
                "direction": "in" if delta > 0 else "out",
                "qty_base_units": abs(delta),
                "pack_breakdown_input": line.qty.model_dump(),
                "reason_code": line.reason_code or payload.reason_code,
                "reference_type": payload.reference_type or "product_action",
                "reference_id": payload.reference_id or idempotency_key,
                "idempotency_key": idempotency_key,
                "actor": {"user_id": user.get("user_id"), "role": user.get("role")},
                "before_qty": before_qty,
                "after_qty": after_qty,
                "allocations": allocations,
                "created_at": datetime.utcnow(),
            },
        )
        results.append({"product_id": product_key, "qty_base_units": base_qty, "allocations": allocations})
    return {"status": "ok", "idempotency_key": idempotency_key, "results": results}


@router.post("/counter/scan", summary="Resolve barcode for counter mode")
def counter_scan(payload: CounterScanRequest, user: dict = Depends(get_current_user)):
    db = get_db()
    product = _resolve_product_ref(db, payload.barcode)
    if not product:
        raise HTTPException(status_code=404, detail="Barcode not mapped to a product")
    session_doc = db["counter_sessions"].find_one(
        {"merchant_id": user["merchant_id"], "session_id": payload.session_id}
    ) or {"merchant_id": user["merchant_id"], "session_id": payload.session_id, "lines": []}
    product_key = product.get("Product ID") or product.get("product_id")
    qty_payload = payload.qty.model_dump() if payload.qty else {"unit": 1}
    existing = next((line for line in session_doc["lines"] if line["product_id"] == product_key and line.get("barcode") == payload.barcode), None)
    if existing:
        existing["qty"] = {
            "box": existing["qty"].get("box", 0) + qty_payload.get("box", 0),
            "strip": existing["qty"].get("strip", 0) + qty_payload.get("strip", 0),
            "unit": existing["qty"].get("unit", 0) + qty_payload.get("unit", 0),
        }
    else:
        session_doc["lines"].append(
            {
                "product_id": product_key,
                "barcode": payload.barcode,
                "qty": qty_payload,
                "reason_code": "counter_sale",
            }
        )
    db["counter_sessions"].update_one(
        {"merchant_id": user["merchant_id"], "session_id": payload.session_id},
        {"$set": session_doc},
        upsert=True,
    )
    return {
        "status": "ok",
        "data": {
            "session_id": payload.session_id,
            "barcode": payload.barcode,
            "product": _build_product_view(product),
            "matched_barcode_level": product.get("_matched_barcode_level", "unit"),
        },
    }


@router.post("/counter/confirm-sale", summary="Commit counter sale")
def counter_confirm_sale(
    payload: CounterConfirmRequest,
    user: dict = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    db = get_db()
    session_doc = db["counter_sessions"].find_one({"merchant_id": user["merchant_id"], "session_id": payload.session_id}) or {"lines": []}
    lines = session_doc.get("lines", [])
    if not lines:
        raise HTTPException(status_code=400, detail="No scanned lines found for session")
    action_lines = []
    for line in lines:
        action_lines.append(
            StockActionLine(
                product_id=line["product_id"],
                qty=PackQuantity(**line.get("qty", {"unit": line.get("qty_base_units", 0)})),
                barcode=line.get("barcode"),
                reason_code=line.get("reason_code") or payload.reason_code,
            )
        )
    return stock_actions(
        StockActionRequest(
            action="sale_out",
            lines=action_lines,
            reason_code=payload.reason_code or ("missed_sale" if payload.missed_sale_entry else "counter_sale"),
            reference_type="sale",
            reference_id=payload.reference_id or payload.session_id,
            notes="missed_sale_entry" if payload.missed_sale_entry else None,
        ),
        user=user,
        idempotency_key=idempotency_key or f"{user['merchant_id']}:sale:{payload.session_id}",
    )


@router.post("/reconciliation/adjust", summary="Apply physical vs system adjustment")
def reconcile_adjust(
    payload: ReconciliationAdjustRequest,
    user: dict = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    db = get_db()
    results = []
    for line in payload.lines:
        product = _get_product_by_identifier(db, line.product_id)
        if not product:
            raise HTTPException(status_code=404, detail=f"Product '{line.product_id}' not found")
        packaging = _product_packaging(product)
        physical = _pack_to_base(line.physical_qty.model_dump(), packaging)
        system = _pack_to_base((line.system_qty or PackQuantity()).model_dump(), packaging)
        delta = physical - system
        if delta != 0:
            _update_product_stock(db, product.get("Product ID") or product.get("product_id"), delta)
            _append_ledger(
                db,
                {
                    "tenant_id": user["merchant_id"],
                    "merchant_id": user["merchant_id"],
                    "product_id": product.get("Product ID") or product.get("product_id"),
                    "txn_type": "adjustment",
                    "direction": "in" if delta > 0 else "out",
                    "qty_base_units": abs(delta),
                    "pack_breakdown_input": line.physical_qty.model_dump(),
                    "reason_code": line.reason_code,
                    "reference_type": "reconciliation",
                    "reference_id": payload.date,
                    "idempotency_key": idempotency_key or f"{user['merchant_id']}:recon:{payload.date}:{line.product_id}:{line.reason_code}",
                    "actor": {"user_id": user.get("user_id"), "role": user.get("role")},
                    "before_qty": system,
                    "after_qty": physical,
                    "created_at": datetime.utcnow(),
                },
            )
        results.append({"product_id": line.product_id, "delta_base_units": delta, "physical": physical, "system": system})
    return {"status": "ok", "data": results}
