from __future__ import annotations

from unittest.mock import patch

import mongomock


_mock_client = mongomock.MongoClient()
_mock_db = _mock_client["sanjeevani_rx_db"]


def _get_test_client():
    return _mock_client


def _get_test_db(db_name: str | None = None):
    return _mock_client[db_name or "sanjeevani_rx_db"]


async def _get_test_user() -> dict:
    return {
        "sub": "test-pharmacy",
        "merchant_id": "test-pharmacy",
        "pharmacy_id": "test-pharmacy",
        "role": "pharmacist",
    }


_client_patch = patch("app.database.mongo_client.get_client", new=_get_test_client)
_db_patch = patch("app.database.mongo_client.get_db", new=_get_test_db)
_security_patch = patch("app.utils.security.get_current_user", new=_get_test_user)

_client_patch.start()
_db_patch.start()
_security_patch.start()
