"""
app/database/models.py
─────────────────────────────────────────────────────────────────────────────
Pydantic v2 domain models for SanjeevaniRxAI.

Collections → Model mapping
────────────────────────────
  consumer_orders  → ConsumerOrder
  products         → Product
  patients         → Patient
  inventory        → Inventory
  predictions      → Prediction
  alerts           → Alert

Key design decisions
─────────────────────
• ``ConsumerOrder`` and ``Product`` field names match the **original Excel
  column headers exactly** (spaces preserved) so that raw rows loaded from
  Excel can be inserted into MongoDB without any key transformation.
• All models use ``model_config = ConfigDict(populate_by_name=True)`` so
  that both the alias (MongoDB ``_id``) and the Python name (``id``) work.
• ``PyObjectId`` is a custom type that serialises ObjectId to a plain string
  in JSON responses while still accepting both ``str`` and ``ObjectId``
  inputs on the way in.
• ``Optional`` fields default to ``None`` to survive missing columns in
  partially-filled Excel rows.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional
from enum import Enum

from bson import ObjectId
from pydantic import BaseModel, ConfigDict, Field, field_validator


# =============================
# DATA MODELS (FOR NEW ENDPOINTS)
# =============================
class OrderChannel(str, Enum):
    WHATSAPP = "WhatsApp"
    TELEGRAM = "Telegram"
    SMS = "SMS"
    VOICE = "Voice"
    MANUAL = "Manual"


class OrderRequest(BaseModel):
    patient_name: str = Field(..., alias="patient_name")
    patient_id: str = Field(..., alias="patient_id")
    age: int = Field(0)
    gender: str = Field("Unknown")
    contact_number: str = Field(..., alias="contact_number")
    address: str = Field(..., alias="address")
    medicine_name: str = Field(..., alias="medicine_name")
    quantity: float = Field(1.0)
    unit_price: float = Field(0.0)
    channel: OrderChannel = Field(OrderChannel.MANUAL)


# Add Conversation State Enum
class ConversationState(str, Enum):
    ONBOARDING_NAME = "onboarding_name"
    ONBOARDING_LANGUAGE = "onboarding_language"
    ONBOARDING_GENDER = "onboarding_gender"
    ONBOARDING_AGE = "onboarding_age"
    ORDERING_MEDICINE = "ordering_medicine"
    ORDERING_QUANTITY = "ordering_quantity"
    ORDERING_ADDRESS = "ordering_address"
    ORDERING_ADDRESS_CONFIRM = "ordering_address_confirm"
    ORDERING_PAYMENT = "ordering_payment"
    ORDER_CONFIRMED = "order_confirmed"
    ORDER_TRACKING = "order_tracking"
    GENERAL = "general"


# ──────────────────────────────────────────────────────────────────────────────
# ObjectId helper
# ──────────────────────────────────────────────────────────────────────────────


class PyObjectId(str):
    """
    Pydantic-compatible wrapper for BSON ObjectId.

    Accepts ObjectId instances or their 24-hex-char string representations
    and serialises to plain ``str`` in JSON.
    """

    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v: Any) -> str:
        if isinstance(v, ObjectId):
            return str(v)
        if isinstance(v, str) and ObjectId.is_valid(v):
            return v
        raise ValueError(f"Invalid ObjectId: {v!r}")

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: Any
    ):  # noqa: ANN001
        """Pydantic v2 core-schema integration."""
        from pydantic_core import core_schema

        return core_schema.no_info_plain_validator_function(
            cls.validate,
            serialization=core_schema.to_string_ser_schema(),
        )


# ──────────────────────────────────────────────────────────────────────────────
# Shared base
# ──────────────────────────────────────────────────────────────────────────────


class _BaseDocument(BaseModel):
    """
    Common configuration inherited by every document model.

    - ``id`` maps to MongoDB ``_id``.
    - JSON serialisation converts ObjectId → str automatically.
    - Extra fields are allowed (MongoDB documents may have app-added keys).
    """

    model_config = ConfigDict(
        populate_by_name=True,  # allow both alias and field name
        arbitrary_types_allowed=True,
        json_encoders={ObjectId: str},
        extra="allow",  # tolerate extra MongoDB fields gracefully
    )

    id: Optional[PyObjectId] = Field(default=None, alias="_id")


# ──────────────────────────────────────────────────────────────────────────────
# ConsumerOrder  (collection: consumer_orders)
# ──────────────────────────────────────────────────────────────────────────────


class ConsumerOrder(_BaseDocument):
    """
    Mirrors the columns in the *Consumer Order History* Excel sheet.

    Column names are preserved verbatim (including spaces) so that raw
    ``df.to_dict("records")`` can be bulk-inserted without renaming.

    Adjust / extend the field list to match your actual .xlsx headers.
    """

    # ── Patient / consumer identifiers ───────────────────────────────────────
    patient_name: Optional[str] = Field(None, alias="Patient Name")
    patient_id: Optional[str] = Field(None, alias="Patient ID")
    age: Optional[int] = Field(None, alias="Age")
    gender: Optional[str] = Field(None, alias="Gender")
    contact_number: Optional[str] = Field(None, alias="Contact Number")
    address: Optional[str] = Field(None, alias="Address")

    # ── Order details ─────────────────────────────────────────────────────────
    order_id: Optional[str] = Field(None, alias="Order ID")
    order_date: Optional[str] = Field(None, alias="Order Date")
    order_channel: Optional[str] = Field(
        None, alias="Order Channel"
    )  # WhatsApp, SMS, Phone, Walk-in
    order_status: Optional[str] = Field(
        None, alias="Order Status"
    )  # Pending, Fulfilled, Cancelled

    # ── Medicine details ──────────────────────────────────────────────────────
    medicine_name: Optional[str] = Field(None, alias="Medicine Name")
    medicine_category: Optional[str] = Field(None, alias="Medicine Category")
    dosage: Optional[str] = Field(None, alias="Dosage")
    quantity_ordered: Optional[float] = Field(None, alias="Quantity Ordered")
    unit_price: Optional[float] = Field(None, alias="Unit Price")
    total_amount: Optional[float] = Field(None, alias="Total Amount")

    # ── Prescription / refill ─────────────────────────────────────────────────
    prescription_required: Optional[str] = Field(None, alias="Prescription Required")
    refill_due_date: Optional[str] = Field(None, alias="Refill Due Date")
    is_chronic: Optional[str] = Field(None, alias="Is Chronic")  # Yes / No
    doctor_name: Optional[str] = Field(None, alias="Doctor Name")
    diagnosis: Optional[str] = Field(None, alias="Diagnosis")

    # ── Fulfilment ────────────────────────────────────────────────────────────
    dispensed_by: Optional[str] = Field(None, alias="Dispensed By")
    payment_method: Optional[str] = Field(None, alias="Payment Method")
    insurance_provider: Optional[str] = Field(None, alias="Insurance Provider")
    notes: Optional[str] = Field(None, alias="Notes")


# ──────────────────────────────────────────────────────────────────────────────
# Product  (collection: products)
# ──────────────────────────────────────────────────────────────────────────────


class Product(_BaseDocument):
    """
    Mirrors the columns in the *Products / Drug Catalogue* Excel sheet.
    Field names are preserved verbatim to match original Excel headers.
    """

    # ── Identifiers ───────────────────────────────────────────────────────────
    product_id: Optional[str] = Field(None, alias="Product ID")
    medicine_name: Optional[str] = Field(None, alias="Medicine Name")
    brand_name: Optional[str] = Field(None, alias="Brand Name")
    generic_name: Optional[str] = Field(None, alias="Generic Name")
    manufacturer: Optional[str] = Field(None, alias="Manufacturer")

    # ── Classification ────────────────────────────────────────────────────────
    category: Optional[str] = Field(None, alias="Category")
    sub_category: Optional[str] = Field(None, alias="Sub Category")
    form: Optional[str] = Field(None, alias="Form")  # Tablet, Syrup, Injection …
    strength: Optional[str] = Field(None, alias="Strength")

    # ── Pricing ───────────────────────────────────────────────────────────────
    unit_price: Optional[float] = Field(None, alias="Unit Price")
    mrp: Optional[float] = Field(None, alias="MRP")

    # ── Stock / Supply ────────────────────────────────────────────────────────
    current_stock: Optional[float] = Field(None, alias="Current Stock")
    reorder_level: Optional[float] = Field(None, alias="Reorder Level")
    expiry_date: Optional[str] = Field(None, alias="Expiry Date")
    batch_number: Optional[str] = Field(None, alias="Batch Number")
    supplier_name: Optional[str] = Field(None, alias="Supplier Name")

    # ── Prescription flag ─────────────────────────────────────────────────────
    requires_prescription: Optional[str] = Field(
        None, alias="Requires Prescription"
    )  # Yes / No
    controlled_substance: Optional[str] = Field(None, alias="Controlled Substance")


# ──────────────────────────────────────────────────────────────────────────────
# Patient  (collection: patients)
# ──────────────────────────────────────────────────────────────────────────────


class Patient(_BaseDocument):
    """
    Enriched patient profile built from aggregated order history.
    Uses snake_case field names (not raw Excel headers).
    """

    patient_id: str = Field(..., description="Unique patient identifier")
    name: str = Field(..., description="Full patient name")
    age: Optional[int] = None
    gender: Optional[str] = None
    contact_number: Optional[str] = None
    address: Optional[str] = None
    diagnoses: List[str] = Field(default_factory=list)
    chronic_conditions: List[str] = Field(default_factory=list)
    regular_medicines: List[str] = Field(default_factory=list)
    preferred_channel: Optional[str] = None  # WhatsApp / SMS / Phone
    doctor_name: Optional[str] = None
    insurance_provider: Optional[str] = None
    last_order_date: Optional[str] = None
    total_orders: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ──────────────────────────────────────────────────────────────────────────────
# Inventory  (collection: inventory)
# ──────────────────────────────────────────────────────────────────────────────


class Inventory(_BaseDocument):
    """
    Real-time inventory snapshot for a single product.
    Updated whenever an order is fulfilled or new stock arrives.
    """

    product_id: str = Field(...)
    medicine_name: str = Field(...)
    category: Optional[str] = None
    current_stock: float = Field(default=0.0, ge=0)
    reorder_level: float = Field(default=0.0, ge=0)
    expiry_date: Optional[str] = None
    batch_number: Optional[str] = None
    supplier_name: Optional[str] = None
    unit_price: Optional[float] = None
    last_restocked_at: Optional[datetime] = None
    is_low_stock: bool = False
    is_expiry_risk: bool = False  # expires within 30 days
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("is_low_stock", mode="before")
    @classmethod
    def derive_low_stock(cls, v: bool, info: Any) -> bool:
        """Auto-derive low-stock flag from stock vs reorder level."""
        data = info.data if hasattr(info, "data") else {}
        stock = data.get("current_stock", 0)
        reorder = data.get("reorder_level", 0)
        if isinstance(stock, (int, float)) and isinstance(reorder, (int, float)):
            return stock <= reorder
        return bool(v)


# ──────────────────────────────────────────────────────────────────────────────
# Prediction  (collection: predictions)
# ──────────────────────────────────────────────────────────────────────────────


class Prediction(_BaseDocument):
    """
    A single AI prediction record (refill, demand forecast, etc.).
    """

    prediction_type: str = Field(
        ..., description="E.g. 'refill', 'demand_forecast', 'expiry_risk'"
    )
    patient_id: Optional[str] = None
    medicine_name: Optional[str] = None
    product_id: Optional[str] = None

    # ── Prediction output ─────────────────────────────────────────────────────
    predicted_value: Optional[float] = None  # days until refill, forecast qty, …
    confidence_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    predicted_refill_date: Optional[str] = None
    recommended_quantity: Optional[float] = None

    # ── Explainability ────────────────────────────────────────────────────────
    explanation: Optional[str] = None  # Human-readable rationale
    feature_importances: Dict[str, float] = Field(default_factory=dict)
    model_version: Optional[str] = None

    # ── Metadata ──────────────────────────────────────────────────────────────
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    is_actioned: bool = False  # Has this triggered an alert/action?
    action_taken: Optional[str] = None


# ──────────────────────────────────────────────────────────────────────────────
# Alert  (collection: alerts)
# ──────────────────────────────────────────────────────────────────────────────


class AlertSeverity(str):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Alert(_BaseDocument):
    """
    System-generated or AI-generated alerts surfaced on the pharmacist dashboard.
    """

    alert_type: str = Field(
        ...,
        description=(
            "Category: 'refill_due', 'low_stock', 'expiry_risk', "
            "'interaction_warning', 'proactive_outreach', 'system'"
        ),
    )
    severity: str = Field(
        default="medium",
        description="One of: low, medium, high, critical",
    )
    title: str = Field(..., description="Short human-readable title")
    message: str = Field(..., description="Full alert detail")

    # ── Linked entities ───────────────────────────────────────────────────────
    patient_id: Optional[str] = None
    patient_name: Optional[str] = None
    medicine_name: Optional[str] = None
    product_id: Optional[str] = None
    prediction_id: Optional[str] = None

    # ── Workflow state ────────────────────────────────────────────────────────
    is_resolved: bool = False
    resolved_by: Optional[str] = None  # pharmacist username
    resolved_at: Optional[datetime] = None
    resolution_note: Optional[str] = None

    # ── Metadata ──────────────────────────────────────────────────────────────
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    channel: Optional[str] = None  # WhatsApp / SMS / dashboard
    auto_actioned: bool = False  # Was the alert sent automatically?


# ──────────────────────────────────────────────────────────────────────────────
# Convenience exports
# ──────────────────────────────────────────────────────────────────────────────

__all__ = [
    "PyObjectId",
    "ConsumerOrder",
    "Product",
    "Patient",
    "Inventory",
    "Prediction",
    "Alert",
    "AlertSeverity",
]
