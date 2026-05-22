from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


class TestTelegramEndpoints:
    PREFIX = "/api/v1"

    def test_telegram_webhook_returns_200(self, client):
        # A minimal valid Telegram update object
        telegram_update = {
            "update_id": 123456789,
            "message": {
                "message_id": 123,
                "from": {"id": 12345, "is_bot": False, "first_name": "Test"},
                "chat": {"id": 12345, "first_name": "Test", "type": "private"},
                "date": 1678886400,
                "text": "Hello Telegram",
            },
        }
        resp = client.post(f"{self.PREFIX}/telegram-webhook", json=telegram_update)
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
