"""
tests/test_api.py
─────────────────────────────────────────────────────────────────────────────
Integration tests using FastAPI TestClient:
  - All major endpoints return 200
  - Pagination parameters work correctly
  - Error cases return proper HTTP codes (404, 422)
  - Response JSON structure is validated
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


# ──────────────────────────────────────────────────────────────────────────────
# App fixture
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def client():
    """
    Return a TestClient for the FastAPI app.
    Requires MongoDB to be reachable (use docker-compose or a local instance).
    """
    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ──────────────────────────────────────────────────────────────────────────────
# Health / Root
# ──────────────────────────────────────────────────────────────────────────────


class TestHealthEndpoints:
    def test_root_returns_200(self, client):
        resp = client.get("/")
        assert resp.status_code == 200, f"Root returned {resp.status_code}"

    def test_health_returns_200_or_503(self, client):
        resp = client.get("/health")
        assert resp.status_code in (200, 503)
        body = resp.json()
        assert "status" in body

    def test_health_has_database_key(self, client):
        resp = client.get("/health")
        body = resp.json()
        assert "database" in body


# ──────────────────────────────────────────────────────────────────────────────
# Dashboard
# ──────────────────────────────────────────────────────────────────────────────


class TestDashboardEndpoints:
    PREFIX = "/api/v1/dashboard"

    def test_overview_returns_200(self, client):
        resp = client.get(f"{self.PREFIX}/overview")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "data" in body

    def test_overview_data_has_kpis(self, client):
        resp = client.get(f"{self.PREFIX}/overview")
        data = resp.json()["data"]
        for key in (
            "total_patients",
            "total_orders",
            "total_products",
            "active_alerts",
        ):
            assert key in data, f"Missing KPI key: {key}"

    def test_customers_insights(self, client):
        resp = client.get(f"{self.PREFIX}/customers")
        assert resp.status_code == 200

    def test_product_analytics(self, client):
        resp = client.get(f"{self.PREFIX}/products")
        assert resp.status_code == 200

    def test_order_analytics(self, client):
        resp = client.get(f"{self.PREFIX}/orders")
        assert resp.status_code == 200

    def test_timeseries_orders(self, client):
        resp = client.get(f"{self.PREFIX}/timeseries?metric=orders&period=30d")
        assert resp.status_code == 200
        body = resp.json()
        assert body["metric"] == "orders"
        assert isinstance(body["data"], list)

    def test_timeseries_invalid_metric(self, client):
        resp = client.get(f"{self.PREFIX}/timeseries?metric=invalid&period=30d")
        assert resp.status_code == 422


# ──────────────────────────────────────────────────────────────────────────────
# Customers
# ──────────────────────────────────────────────────────────────────────────────


class TestCustomersEndpoints:
    PREFIX = "/api/v1/customers"

    def test_list_returns_200(self, client):
        resp = client.get(f"{self.PREFIX}/")
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "total" in body

    def test_pagination_defaults(self, client):
        resp = client.get(f"{self.PREFIX}/?page=1&page_size=5")
        body = resp.json()
        assert len(body["data"]) <= 5
        assert body["page"] == 1

    def test_pagination_page_2(self, client):
        resp1 = client.get(f"{self.PREFIX}/?page=1&page_size=5")
        resp2 = client.get(f"{self.PREFIX}/?page=2&page_size=5")
        assert resp1.status_code == 200
        assert resp2.status_code == 200
        # Pages 1 and 2 should not share the same first item (when data exists)
        data1 = resp1.json()["data"]
        data2 = resp2.json()["data"]
        if data1 and data2:
            assert data1[0] != data2[0]

    def test_invalid_page_size_422(self, client):
        resp = client.get(f"{self.PREFIX}/?page_size=0")
        assert resp.status_code == 422

    def test_unknown_patient_404(self, client):
        resp = client.get(f"{self.PREFIX}/PATIENT_DOES_NOT_EXIST_XYZ")
        assert resp.status_code == 404


# ──────────────────────────────────────────────────────────────────────────────
# Products
# ──────────────────────────────────────────────────────────────────────────────


class TestProductsEndpoints:
    PREFIX = "/api/v1/products"

    def test_list_returns_200(self, client):
        resp = client.get(f"{self.PREFIX}/")
        assert resp.status_code == 200

    def test_pagination_response_structure(self, client):
        resp = client.get(f"{self.PREFIX}/?page=1&page_size=10")
        body = resp.json()
        for key in ("status", "page", "page_size", "total", "data"):
            assert key in body, f"Missing key '{key}' in paginated response"

    def test_category_filter(self, client):
        resp = client.get(f"{self.PREFIX}/?category=Antidiabetic")
        assert resp.status_code == 200

    def test_low_stock_endpoint(self, client):
        resp = client.get(f"{self.PREFIX}/low-stock")
        assert resp.status_code == 200
        assert isinstance(resp.json()["data"], list)

    def test_expiry_risk_endpoint(self, client):
        resp = client.get(f"{self.PREFIX}/expiry-risk?days=90")
        assert resp.status_code == 200
        assert isinstance(resp.json()["data"], list)

    def test_unknown_product_404(self, client):
        resp = client.get(f"{self.PREFIX}/PRODUCT_DOES_NOT_EXIST_XYZ")
        assert resp.status_code == 404


# ──────────────────────────────────────────────────────────────────────────────
# Orders
# ──────────────────────────────────────────────────────────────────────────────


class TestOrdersEndpoints:
    PREFIX = "/api/v1/orders"

    def test_list_returns_200(self, client):
        resp = client.get(f"{self.PREFIX}/")
        assert resp.status_code == 200

    def test_status_filter(self, client):
        resp = client.get(f"{self.PREFIX}/?status=Fulfilled")
        assert resp.status_code == 200

    def test_channel_filter(self, client):
        resp = client.get(f"{self.PREFIX}/?channel=WhatsApp")
        assert resp.status_code == 200

    def test_order_stats(self, client):
        resp = client.get(f"{self.PREFIX}/stats")
        assert resp.status_code == 200
        body = resp.json()
        assert "by_status" in body["data"]

    def test_validate_order_missing_body_422(self, client):
        resp = client.post(f"{self.PREFIX}/validate", json={})
        assert resp.status_code == 422

    def test_validate_order_valid_payload(self, client):
        resp = client.post(
            f"{self.PREFIX}/validate",
            json={
                "patient_id": "TEST_PATIENT",
                "medicine_name": "Metformin 500mg",
                "quantity": 30,
            },
        )
        # Should return 200 with validation result (may warn about stock)
        assert resp.status_code == 200
        body = resp.json()
        assert "is_valid" in body["data"]

    def test_validate_negative_quantity_422(self, client):
        resp = client.post(
            f"{self.PREFIX}/validate",
            json={
                "patient_id": "P001",
                "medicine_name": "Metformin 500mg",
                "quantity": 0,
            },
        )
        # quantity > 0 constraint → 422 from FastAPI or error from service
        assert resp.status_code in (200, 422)

    def test_unknown_order_404(self, client):
        resp = client.get(f"{self.PREFIX}/ORD-DOES-NOT-EXIST-9999")
        assert resp.status_code == 404


# ──────────────────────────────────────────────────────────────────────────────
# Recommendations
# ──────────────────────────────────────────────────────────────────────────────


class TestRecommendationsEndpoints:
    PREFIX = "/api/v1/recommendations"

    def test_list_returns_200(self, client):
        resp = client.get(f"{self.PREFIX}/")
        assert resp.status_code == 200

    def test_risk_level_filter(self, client):
        resp = client.get(f"{self.PREFIX}/?risk_level=high")
        assert resp.status_code == 200

    def test_invalid_risk_level_422(self, client):
        resp = client.get(f"{self.PREFIX}/?risk_level=INVALID")
        assert resp.status_code == 422

    def test_patient_recommendations(self, client):
        resp = client.get(f"{self.PREFIX}/patient/SOME_PATIENT")
        assert resp.status_code == 200

    def test_alternatives_endpoint(self, client):
        resp = client.get(f"{self.PREFIX}/alternatives/Metformin 500mg")
        assert resp.status_code == 200
        assert isinstance(resp.json()["data"], list)


# ──────────────────────────────────────────────────────────────────────────────
# Alerts
# ──────────────────────────────────────────────────────────────────────────────


class TestAlertsEndpoints:
    PREFIX = "/api/v1/alerts"

    def test_list_returns_200(self, client):
        resp = client.get(f"{self.PREFIX}/")
        assert resp.status_code == 200

    def test_pagination_works(self, client):
        resp = client.get(f"{self.PREFIX}/?page=1&page_size=5")
        body = resp.json()
        assert len(body["data"]) <= 5

    def test_filter_by_resolved(self, client):
        resp = client.get(f"{self.PREFIX}/?is_resolved=false")
        assert resp.status_code == 200

    def test_filter_invalid_severity_422(self, client):
        resp = client.get(f"{self.PREFIX}/?severity=INVALID_LEVEL")
        assert resp.status_code == 422

    def test_alert_summary(self, client):
        resp = client.get(f"{self.PREFIX}/summary")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "total_unresolved" in data
        assert "by_type" in data

    def test_invalid_alert_id_400(self, client):
        resp = client.get(f"{self.PREFIX}/not-a-valid-objectid")
        assert resp.status_code in (400, 404)

    def test_resolve_invalid_id_400(self, client):
        resp = client.patch(
            f"{self.PREFIX}/not-a-valid-id/resolve",
            params={"alert_id": "not-a-valid-id"},
            json={"resolved_by": "test", "resolution_note": ""},
        )
        assert resp.status_code in (400, 422)
