from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


class TestVoiceEndpoints:
    PREFIX = "/api/v1"

    def test_voice_webhook_returns_200(self, client):
        # A minimal valid Twilio voice webhook payload
        twilio_voice_payload = {
            "CallSid": "CA1234567890abcdef1234567890abcdef",
            "AccountSid": "TWILIO_TEST_ACCOUNT_SID_PLACEHOLDER",
            "From": "+1234567890",
            "To": "+11234567890",
            "CallStatus": "ringing",
            "ApiVersion": "2010-04-01",
            "Direction": "inbound",
            "ForwardedFrom": "",
            "CallerName": "",
            "ParentCallSid": "",
            "SpeechResult": "hello", # Simulate speech input
            "Confidence": "0.9",
        }
        resp = client.post(f"{self.PREFIX}/voice-webhook", data=twilio_voice_payload)
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/xml"
        assert "<Response>" in resp.text
        assert "<Say>" in resp.text
