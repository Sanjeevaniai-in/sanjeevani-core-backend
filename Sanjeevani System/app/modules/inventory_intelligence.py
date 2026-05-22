"""
app/modules/inventory_intelligence.py
─────────────────────────────────────────────────────────────────────────────
Inventory intelligence: low-stock detection, expiry risk, demand trends
and simple moving-average demand forecast.

Public API
──────────
    from app.modules.inventory_intelligence import InventoryIntelligenceService
    svc = InventoryIntelligenceService()
    svc.check_low_stock()
    svc.check_expiry_risk(days=90)
    svc.generate_inventory_alerts()
    svc.get_reorder_recommendations()
    svc.forecast_demand("Metformin 500mg", days=30)
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import statistics

from app.database.mongo_client import get_db
from app.utils.logger import get_logger

logger = get_logger(__name__)


class InventoryIntelligenceService:
    """Inventory analytics and alert generation service."""

    def __init__(self) -> None:
        self._db = None

    @property
    def db(self):
        if self._db is None:
            self._db = get_db()
        return self._db

    # ──────────────────────────────────────────────────────────────────────
    # 1. check_low_stock
    # ──────────────────────────────────────────────────────────────────────

    def check_low_stock(self, merchant_id: str) -> List[Dict[str, Any]]:
        """
        Return inventory items where ``current_stock <= reorder_level``.

        Each result includes urgency classification:
        - ``critical``: stock == 0
        - ``high``:     stock ≤ 50% of reorder level
        - ``medium``:   stock ≤ reorder level
        """
        items = list(
            self.db["inventory"].find(
                {"is_low_stock": True, "merchant_id": merchant_id},
                {
                    "_id": 0,
                    "product_id": 1,
                    "medicine_name": 1,
                    "category": 1,
                    "current_stock": 1,
                    "reorder_level": 1,
                    "supplier_name": 1,
                    "unit_price": 1,
                },
            )
        )
        result = []
        for item in items:
            stock = float(item.get("current_stock") or 0)
            reorder = float(item.get("reorder_level") or 0)
            if stock == 0:
                urgency = "critical"
            elif reorder > 0 and stock <= reorder * 0.5:
                urgency = "high"
            else:
                urgency = "medium"
            item["urgency"] = urgency
            result.append(item)

        result.sort(key=lambda x: ["critical", "high", "medium"].index(x["urgency"]))
        logger.info("Low stock check", extra={"low_stock_count": len(result)})
        return result

    # ──────────────────────────────────────────────────────────────────────
    # 2. check_expiry_risk
    # ──────────────────────────────────────────────────────────────────────

    def check_expiry_risk(self, merchant_id: str, days: int = 90) -> List[Dict[str, Any]]:
        """
        Return items expiring within *days* calendar days.
        Includes ``days_until_expiry`` and ``urgency`` (critical/high/medium).
        """
        now = datetime.now(tz=timezone.utc)
        cutoff = now + timedelta(days=days)
        result = []

        for item in self.db["inventory"].find({"expiry_date": {"$ne": None}, "merchant_id": merchant_id}):
            exp_raw = item.get("expiry_date")
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
                if days_left <= days:
                    urgency = (
                        "critical"
                        if days_left <= 7
                        else "high" if days_left <= 30 else "medium"
                    )
                    result.append(
                        {
                            "product_id": str(item.get("product_id", "")),
                            "medicine_name": item.get("medicine_name", ""),
                            "expiry_date": exp_raw,
                            "days_until_expiry": days_left,
                            "current_stock": item.get("current_stock", 0),
                            "urgency": urgency,
                        }
                    )
            except (ValueError, TypeError):
                continue

        result.sort(key=lambda x: x["days_until_expiry"])
        logger.info("Expiry risk check", extra={"at_risk_count": len(result)})
        return result

    # ──────────────────────────────────────────────────────────────────────
    # NEW: predict_stock_out_days
    # ──────────────────────────────────────────────────────────────────────

    def predict_stock_out_days(self, medicine_name: str, merchant_id: str) -> float:
        """
        Estimate how many days until this medicine runs out based on 30d velocity.
        """
        now = datetime.now(tz=timezone.utc)
        since = now - timedelta(days=30)
        
        # Calculate daily velocity
        order_qty = list(self.db["consumer_orders"].aggregate([
            {"$match": {
                "Medicine Name": medicine_name, 
                "merchant_id": merchant_id,
                "Order Date": {"$gte": since}
            }},
            {"$group": {"_id": None, "total": {"$sum": {"$toDouble": "$Quantity"}}}}
        ]))
        
        total_units = order_qty[0]["total"] if order_qty else 0
        velocity = total_units / 30.0 # units per day
        
        inventory = self.db["inventory"].find_one({"medicine_name": medicine_name, "merchant_id": merchant_id})
        if not inventory: return 999
        
        stock = float(inventory.get("current_stock") or 0)
        if velocity <= 0: return 999 
        
        return round(stock / velocity, 1)

    # ──────────────────────────────────────────────────────────────────────
    # 3. analyze_movement_patterns
    # ──────────────────────────────────────────────────────────────────────

    def analyze_movement_patterns(self, merchant_id: str) -> List[Dict[str, Any]]:
        """
        Classify each product's sales velocity as:
        - ``fast_moving``:  top quartile order frequency
        - ``slow_moving``:  bottom quartile
        - ``medium_moving``: middle two quartiles
        - ``no_movement``:  never ordered

        Returns list of {product_id, medicine_name, total_orders,
                          orders_last_30d, velocity}.
        """
        now = datetime.now(tz=timezone.utc)
        since = now - timedelta(days=30)

        pipeline = [
            {
                "$group": {
                    "_id": "$Medicine Name",
                    "total_orders": {"$sum": 1},
                    "last_30d": {
                        "$sum": {"$cond": [{"$and": [{"$gte": ["$Order Date", since]}, {"$eq": ["$merchant_id", merchant_id]}]}, 1, 0]}
                    },
                }
            },
        ]
        # Wait, it's better to $match merchant_id early in pipeline
        pipeline = [
            {"$match": {"merchant_id": merchant_id}},
            {
                "$group": {
                    "_id": "$Medicine Name",
                    "total_orders": {"$sum": 1},
                    "last_30d": {
                        "$sum": {"$cond": [{"$gte": ["$Order Date", since]}, 1, 0]}
                    },
                }
            },
        ]
        med_stats = {
            r["_id"]: {"total_orders": r["total_orders"], "last_30d": r["last_30d"]}
            for r in self.db["consumer_orders"].aggregate(pipeline)
            if r["_id"]
        }

        all_products = list(
            self.db["inventory"].find(
                {"merchant_id": merchant_id}, {"_id": 0, "product_id": 1, "medicine_name": 1}
            )
        )

        counts = [
            med_stats.get(p["medicine_name"], {}).get("total_orders", 0)
            for p in all_products
        ]
        if counts:
            sorted_counts = sorted(counts)
            n = len(sorted_counts)
            q1 = sorted_counts[n // 4]
            q3 = sorted_counts[(3 * n) // 4]
        else:
            q1 = q3 = 0

        result = []
        for prod in all_products:
            name = prod.get("medicine_name", "")
            stats = med_stats.get(name, {})
            total = stats.get("total_orders", 0)
            last = stats.get("last_30d", 0)
            if total == 0:
                velocity = "no_movement"
            elif total >= q3:
                velocity = "fast_moving"
            elif total <= q1:
                velocity = "slow_moving"
            else:
                velocity = "medium_moving"
            result.append(
                {
                    "product_id": prod.get("product_id", ""),
                    "medicine_name": name,
                    "total_orders": total,
                    "orders_last_30d": last,
                    "velocity": velocity,
                }
            )

        return result

    # ──────────────────────────────────────────────────────────────────────
    # 4. analyze_demand_trend
    # ──────────────────────────────────────────────────────────────────────

    def analyze_demand_trend(self, product_id: str, merchant_id: str) -> Dict[str, Any]:
        """
        Monthly demand trend for a single product.

        Returns ``monthly_demand`` (list of {month, orders, qty}),
        ``trend`` (increasing / decreasing / stable), and ``slope`` (Δ orders/month).
        """
        pipeline = [
            {"$match": {"Medicine Name": product_id, "merchant_id": merchant_id}},
            {
                "$group": {
                    "_id": {
                        "year": {"$year": "$Order Date"},
                        "month": {"$month": "$Order Date"},
                    },
                    "orders": {"$sum": 1},
                    "qty": {"$sum": {"$ifNull": ["$Quantity Ordered", 0]}},
                }
            },
            {"$sort": {"_id.year": 1, "_id.month": 1}},
        ]
        data = list(self.db["consumer_orders"].aggregate(pipeline))
        if not data:
            return {"monthly_demand": [], "trend": "unknown", "slope": 0.0}

        monthly = [
            {
                "month": f"{r['_id']['year']}-{r['_id']['month']:02d}",
                "orders": r["orders"],
                "qty": float(r["qty"]),
            }
            for r in data
        ]

        orders_series = [m["orders"] for m in monthly]
        slope = 0.0
        if len(orders_series) >= 2:
            n = len(orders_series)
            x_bar = (n - 1) / 2
            y_bar = statistics.mean(orders_series)
            num = sum((i - x_bar) * (y - y_bar) for i, y in enumerate(orders_series))
            den = sum((i - x_bar) ** 2 for i in range(n))
            slope = num / den if den else 0.0

        trend = "stable"
        if slope > 0.1:
            trend = "increasing"
        elif slope < -0.1:
            trend = "decreasing"

        return {"monthly_demand": monthly, "trend": trend, "slope": round(slope, 4)}

    # ──────────────────────────────────────────────────────────────────────
    # 5. forecast_demand
    # ──────────────────────────────────────────────────────────────────────

    def forecast_demand(self, product_id: str, merchant_id: str, days: int = 30) -> Dict[str, Any]:
        """
        Simple moving-average (SMA-3) demand forecast for the next *days* days.

        Uses the last 3 months' average daily demand and extrapolates linearly
        with the monthly trend slope as a modifier.

        Returns
        ───────
        ``forecast_qty``    – forecasted units needed in the period
        ``avg_daily``       – average daily demand (basis)
        ``confidence``      – 0–1 based on data richness
        ``method``          – always ``"SMA-3 + trend"``
        """
        trend_data = self.analyze_demand_trend(product_id, merchant_id=merchant_id)
        monthly = trend_data.get("monthly_demand", [])
        slope = trend_data.get("slope", 0.0)

        if not monthly:
            return {
                "product_id": product_id,
                "forecast_qty": 0.0,
                "avg_daily": 0.0,
                "confidence": 0.0,
                "method": "SMA-3 + trend",
                "days": days,
            }

        # SMA over last 3 months
        last_3 = monthly[-3:]
        avg_monthly = statistics.mean(m["orders"] for m in last_3)
        avg_daily = avg_monthly / 30.44

        # Apply trend adjustment (compound over forecast period)
        months_ahead = days / 30.44
        trend_factor = 1 + slope * months_ahead / max(avg_monthly, 1)
        forecast_qty = avg_daily * days * max(0.5, min(trend_factor, 2.0))

        confidence = min(len(monthly) / 6, 1.0)  # saturates at 6 months of history

        return {
            "product_id": product_id,
            "forecast_qty": round(forecast_qty, 2),
            "avg_daily": round(avg_daily, 4),
            "confidence": round(confidence, 4),
            "trend": trend_data.get("trend", "stable"),
            "method": "SMA-3 + trend",
            "days": days,
        }

    # ──────────────────────────────────────────────────────────────────────
    # 6. generate_inventory_alerts
    # ──────────────────────────────────────────────────────────────────────

    def generate_inventory_alerts(self, merchant_id: str) -> Dict[str, int]:
        """
        Generate and upsert inventory alerts (low_stock + expiry_risk).

        Returns counts of alerts created by type.
        """
        now = datetime.now(tz=timezone.utc)
        low_count = expiry_count = 0

        for item in self.check_low_stock(merchant_id=merchant_id):
            alert = {
                "alert_type": "low_stock",
                "severity": item["urgency"],
                "title": f"Low Stock: {item['medicine_name']}",
                "message": (
                    f"{item['medicine_name']} has only "
                    f"{item['current_stock']} units remaining "
                    f"(reorder level: {item['reorder_level']}). "
                    f"Urgency: {item['urgency']}."
                ),
                "medicine_name": item["medicine_name"],
                "product_id": item["product_id"],
                "is_resolved": False,
                "merchant_id": merchant_id,
                "auto_actioned": False,
                "created_at": now,
                "updated_at": now,
            }
            self.db["alerts"].update_one(
                {
                    "alert_type": "low_stock",
                    "product_id": item["product_id"],
                    "is_resolved": False,
                },
                {"$set": alert},
                upsert=True,
            )
            low_count += 1

        for item in self.check_expiry_risk(merchant_id=merchant_id, days=90):
            # ... (existing logic)
            self.db["alerts"].update_one(
                {"alert_type": "expiry_risk", "product_id": item["product_id"], "is_resolved": False},
                {"$set": alert},
                upsert=True
            )
            expiry_count += 1
            
        # NEW: Predictive Stock Out Alert
        all_inventory = list(self.db["inventory"].find({"merchant_id": merchant_id}))
        for item in all_inventory:
            days_left = self.predict_stock_out_days(item["medicine_name"], merchant_id)
            if days_left <= 7: # Out within a week
                alert = {
                    "alert_type": "predictive_stock_out",
                    "severity": "high" if days_left <= 3 else "medium",
                    "title": f"Predictive Warning: {item['medicine_name']}",
                    "message": f"Based on current demand, this medicine will finish in {days_left} days. Reorder now!",
                    "medicine_name": item["medicine_name"],
                    "days_remaining": days_left,
                    "is_resolved": False,
                    "merchant_id": merchant_id,
                    "created_at": now,
                    "updated_at": now
                }
                self.db["alerts"].update_one(
                    {"alert_type": "predictive_stock_out", "medicine_name": item["medicine_name"], "is_resolved": False},
                    {"$set": alert},
                    upsert=True
                )
                low_count += 1

        logger.info(
            "Inventory alerts generated",
            extra={"low_stock": low_count, "expiry_risk": expiry_count},
        )
        return {"low_stock": low_count, "expiry_risk": expiry_count}

    # ──────────────────────────────────────────────────────────────────────
    # 7. get_reorder_recommendations
    # ──────────────────────────────────────────────────────────────────────

    def get_reorder_recommendations(self, merchant_id: str) -> List[Dict[str, Any]]:
        """
        Return reorder recommendations for every low-stock item.

        Recommended quantity = max(forecast_demand(30d), reorder_level × 2)
        to ensure a safety buffer.
        """
        low_items = self.check_low_stock(merchant_id=merchant_id)
        recs = []

        for item in low_items:
            med_name = item["medicine_name"]
            forecast = self.forecast_demand(med_name, merchant_id=merchant_id, days=30)
            reorder = float(item.get("reorder_level") or 0)

            recommended_qty = max(
                forecast.get("forecast_qty", 0),
                reorder * 2,
                10,  # absolute minimum order
            )

            recs.append(
                {
                    "product_id": item["product_id"],
                    "medicine_name": med_name,
                    "current_stock": item.get("current_stock", 0),
                    "reorder_level": reorder,
                    "recommended_qty": round(recommended_qty),
                    "forecast_30d": forecast.get("forecast_qty", 0),
                    "supplier_name": item.get("supplier_name"),
                    "unit_price": item.get("unit_price"),
                    "estimated_cost": round(
                        recommended_qty * float(item.get("unit_price") or 0), 2
                    ),
                    "urgency": item["urgency"],
                }
            )

        recs.sort(key=lambda x: ["critical", "high", "medium"].index(x["urgency"]))
        return recs
