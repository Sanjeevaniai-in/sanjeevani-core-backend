from fastapi import HTTPException

from app.api.orders import QuickOrderRequest, _resolve_target_merchant


def test_resolve_target_merchant_prefers_user_identity():
    body = QuickOrderRequest(
        patient_name="Test",
        medicine_name="Paracetamol",
        merchant_id=None,
    )
    user = {"merchant_id": "PHARM_ABC"}
    assert _resolve_target_merchant(body, user) == "PHARM_ABC"


def test_resolve_target_merchant_rejects_cross_merchant_override():
    body = QuickOrderRequest(
        patient_name="Test",
        medicine_name="Paracetamol",
        merchant_id="PHARM_OTHER",
    )
    user = {"merchant_id": "PHARM_ABC"}
    try:
        _resolve_target_merchant(body, user)
    except HTTPException as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError("Expected HTTPException for merchant mismatch")
