from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


class TestSmsEndpoints:
    PREFIX = "/api/v1"

    def test_sms_webhook_returns_200(self, client):
        # A minimal valid Twilio SMS webhook payload
        twilio_sms_payload = {
            "SmsMessageSid": "SM1234567890abcdef1234567890abcdef",
            "AccountSid": "TWILIO_TEST_ACCOUNT_SID_PLACEHOLDER",
            "From": "+1234567890",
            "To": "+11234567890",
            "Body": "Hello SMS",
            "ApiVersion": "2010-04-01",
        }
        resp = client.post(f"{self.PREFIX}/sms-webhook", data=twilio_sms_payload)
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/xml"
        assert "<Response>" in resp.text
        assert "<Message>" in resp.text
