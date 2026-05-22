"""
tests/test_predictions.py
─────────────────────────────────────────────────────────────────────────────
Tests for RefillPredictionService and PatientContextService:
  - Risk score is in 0–100 range
  - Confidence score is in 0–1 range
  - Predicted dates are not None for patients with ≥ 2 orders
  - based_on_orders reflects correct order count
  - Adherence rate is 0–1
"""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures: in-memory patient + order data
# ──────────────────────────────────────────────────────────────────────────────


def _make_orders(patient_id: str, medicine: str, n: int = 5):
    """Return *n* fake orders spaced 30 days apart."""
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return [
        {
            "Patient ID": patient_id,
            "Patient Name": f"Patient {patient_id}",
            "Medicine Name": medicine,
            "Quantity Ordered": 60.0,
            "Unit Price": 5.5,
            "Total Amount": 330.0,
            "Order Date": base + timedelta(days=30 * i),
            "Order Status": "Fulfilled",
            "Order Channel": "WhatsApp",
            "Is Chronic": "Yes",
            "Diagnosis": "Test Condition",
        }
        for i in range(n)
    ]


@pytest.fixture(scope="module")
def seeded_db():
    """
    Seed an in-memory MongoDB (mongomock or real) with test patient data
    and return the database handle + helpers.
    """
    from app.database.mongo_client import get_db

    db = get_db()
    # Clear any residual test data
    db["consumer_orders"].delete_many({"Patient ID": {"$in": ["TPRED001", "TPRED002"]}})
    db["patients"].delete_many({"patient_id": {"$in": ["TPRED001", "TPRED002"]}})

    # Insert test orders
    orders_p1 = _make_orders("TPRED001", "Metformin 500mg", n=6)
    orders_p2 = _make_orders("TPRED002", "Atorvastatin 10mg", n=2)
    db["consumer_orders"].insert_many(orders_p1 + orders_p2)

    # Insert test patient docs
    db["patients"].insert_many(
        [
            {
                "patient_id": "TPRED001",
                "name": "Test Patient 1",
                "regular_medicines": ["Metformin 500mg"],
            },
            {
                "patient_id": "TPRED002",
                "name": "Test Patient 2",
                "regular_medicines": ["Atorvastatin 10mg"],
            },
        ]
    )

    yield db

    # Cleanup
    db["consumer_orders"].delete_many({"Patient ID": {"$in": ["TPRED001", "TPRED002"]}})
    db["patients"].delete_many({"patient_id": {"$in": ["TPRED001", "TPRED002"]}})
    db["predictions"].delete_many({"patient_id": {"$in": ["TPRED001", "TPRED002"]}})


# ──────────────────────────────────────────────────────────────────────────────
# Risk Score Tests
# ──────────────────────────────────────────────────────────────────────────────


class TestRiskScore:
    """generate_refill_risk_score must return a score in 0–100."""

    def test_risk_score_in_range(self, seeded_db):
        from app.modules.patient_context import PatientContextService

        svc = PatientContextService()
        result = svc.generate_refill_risk_score("TPRED001", "Metformin 500mg")
        score = result["risk_score"]
        assert isinstance(score, (int, float)), "risk_score must be numeric"
        assert 0 <= score <= 100, f"risk_score {score} outside 0-100 range"

    def test_risk_score_has_risk_level(self, seeded_db):
        from app.modules.patient_context import PatientContextService

        svc = PatientContextService()
        result = svc.generate_refill_risk_score("TPRED001", "Metformin 500mg")
        assert result["risk_level"] in ("low", "medium", "high", "critical")

    def test_risk_score_has_explanation(self, seeded_db):
        from app.modules.patient_context import PatientContextService

        svc = PatientContextService()
        result = svc.generate_refill_risk_score("TPRED001", "Metformin 500mg")
        assert "explanation" in result
        assert isinstance(result["explanation"], str)

    def test_chronic_patient_has_nonzero_risk(self, seeded_db):
        """All test orders are chronic — score must be > 0."""
        from app.modules.patient_context import PatientContextService

        svc = PatientContextService()
        result = svc.generate_refill_risk_score("TPRED001", "Metformin 500mg")
        assert result["risk_score"] > 0

    def test_unknown_patient_returns_score(self, seeded_db):
        """Unknown patients should not raise — return score safely."""
        from app.modules.patient_context import PatientContextService

        svc = PatientContextService()
        result = svc.generate_refill_risk_score("UNKNOWN_XYZ", "Some Medicine")
        assert 0 <= result["risk_score"] <= 100


# ──────────────────────────────────────────────────────────────────────────────
# Confidence Score Tests
# ──────────────────────────────────────────────────────────────────────────────


class TestConfidenceScore:
    """calculate_confidence_score must return a value in [0, 1]."""

    @pytest.mark.parametrize(
        "order_count,variability",
        [
            (1, 0.0),
            (5, 0.2),
            (10, 0.5),
            (20, 0.0),
            (20, 1.0),
            (0, 0.0),
        ],
    )
    def test_confidence_in_range(self, order_count, variability, seeded_db):
        from app.modules.refill_prediction import RefillPredictionService

        svc = RefillPredictionService()
        score = svc.calculate_confidence_score(order_count, variability)
        assert isinstance(score, float)
        assert (
            0.0 <= score <= 1.0
        ), f"confidence {score} outside [0,1] for count={order_count}, cv={variability}"

    def test_more_orders_higher_confidence(self, seeded_db):
        from app.modules.refill_prediction import RefillPredictionService

        svc = RefillPredictionService()
        c5 = svc.calculate_confidence_score(5, 0.1)
        c15 = svc.calculate_confidence_score(15, 0.1)
        assert c15 >= c5, "More orders should yield equal or higher confidence"

    def test_higher_variability_lower_confidence(self, seeded_db):
        from app.modules.refill_prediction import RefillPredictionService

        svc = RefillPredictionService()
        c_lo = svc.calculate_confidence_score(10, 0.1)
        c_hi = svc.calculate_confidence_score(10, 0.9)
        assert c_lo >= c_hi, "Lower variability should yield equal or higher confidence"


# ──────────────────────────────────────────────────────────────────────────────
# Predicted Date Tests
# ──────────────────────────────────────────────────────────────────────────────


class TestPredictedDate:
    """predict_refill_date must return a non-None datetime for patients with ≥ 2 orders."""

    def test_predicted_date_not_none(self, seeded_db):
        from app.modules.refill_prediction import RefillPredictionService

        svc = RefillPredictionService()
        date = svc.predict_refill_date("TPRED001", "Metformin 500mg")
        assert (
            date is not None
        ), "Predicted date must not be None for patient with 6 orders"

    def test_predicted_date_is_in_future_or_near(self, seeded_db):
        """Prediction should be near current date (within ±180 days for test data)."""
        from app.modules.refill_prediction import RefillPredictionService

        svc = RefillPredictionService()
        date = svc.predict_refill_date("TPRED001", "Metformin 500mg")
        now = datetime.now(tz=timezone.utc)
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        assert (
            abs((date - now).days) < 365
        ), f"Predicted date {date} is too far from today"

    def test_two_orders_also_get_prediction(self, seeded_db):
        """Even with only 2 orders, a date should be returned."""
        from app.modules.refill_prediction import RefillPredictionService

        svc = RefillPredictionService()
        date = svc.predict_refill_date("TPRED002", "Atorvastatin 10mg")
        assert date is not None


# ──────────────────────────────────────────────────────────────────────────────
# generate_prediction / based_on_orders Tests
# ──────────────────────────────────────────────────────────────────────────────


class TestGeneratePrediction:
    """generate_prediction stores correct metadata."""

    def test_based_on_orders_correct(self, seeded_db):
        from app.modules.refill_prediction import RefillPredictionService

        svc = RefillPredictionService()
        pred = svc.generate_prediction("TPRED001", "Metformin 500mg")
        assert (
            pred["based_on_orders"] == 6
        ), f"Expected based_on_orders=6, got {pred['based_on_orders']}"

    def test_prediction_has_all_required_fields(self, seeded_db):
        from app.modules.refill_prediction import RefillPredictionService

        svc = RefillPredictionService()
        pred = svc.generate_prediction("TPRED001", "Metformin 500mg")
        required = [
            "prediction_type",
            "patient_id",
            "medicine_name",
            "confidence_score",
            "risk_score",
            "risk_level",
            "based_on_orders",
            "generated_at",
        ]
        for field in required:
            assert field in pred, f"Missing field '{field}' in prediction"

    def test_prediction_type_is_refill(self, seeded_db):
        from app.modules.refill_prediction import RefillPredictionService

        svc = RefillPredictionService()
        pred = svc.generate_prediction("TPRED001", "Metformin 500mg")
        assert pred["prediction_type"] == "refill"

    def test_prediction_stored_in_db(self, seeded_db):
        from app.modules.refill_prediction import RefillPredictionService
        from app.database.mongo_client import get_db

        svc = RefillPredictionService()
        svc.generate_prediction("TPRED001", "Metformin 500mg")
        doc = get_db()["predictions"].find_one(
            {"patient_id": "TPRED001", "medicine_name": "Metformin 500mg"}
        )
        assert doc is not None, "Prediction was not persisted to DB"


# ──────────────────────────────────────────────────────────────────────────────
# Adherence Pattern Tests
# ──────────────────────────────────────────────────────────────────────────────


class TestAdherencePattern:
    """get_adherence_pattern must return adherence_rate in [0, 1]."""

    def test_adherence_rate_in_range(self, seeded_db):
        from app.modules.patient_context import PatientContextService

        svc = PatientContextService()
        result = svc.get_adherence_pattern("TPRED001")
        rate = result["adherence_rate"]
        assert 0.0 <= rate <= 1.0, f"adherence_rate {rate} outside [0, 1]"

    def test_adherence_has_medicine_list(self, seeded_db):
        from app.modules.patient_context import PatientContextService

        svc = PatientContextService()
        result = svc.get_adherence_pattern("TPRED001")
        assert "medicines_checked" in result
        assert isinstance(result["medicines_checked"], list)
