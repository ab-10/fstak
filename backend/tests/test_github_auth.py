from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from control_plane.app import app
from control_plane.auth_manager import manager
from control_plane.config import get_settings


class MockResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        return None


class GitHubAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        manager._sessions.clear()  # noqa: SLF001 - test-only reset
        manager._user_codes.clear()  # noqa: SLF001 - test-only reset
        manager._tokens.clear()  # noqa: SLF001 - test-only reset
        get_settings.cache_clear()

    def tearDown(self) -> None:
        get_settings.cache_clear()

    def test_device_auth_requires_spx_github_client_id(self) -> None:
        with patch.dict(os.environ, {"FSTAK_DOMAIN_SUFFIX": "test.example.com"}, clear=True):
            client = TestClient(app)
            response = client.post("/auth/device")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["detail"], "SPX_GITHUB_CLIENT_ID is not configured")

    @patch("control_plane.auth_manager.httpx.get")
    @patch("control_plane.auth_manager.httpx.post")
    def test_device_auth_uses_github_login_identity(self, post: MagicMock, get: MagicMock) -> None:
        post.side_effect = [
            MockResponse(
                {
                    "device_code": "github-device-code",
                    "user_code": "ABCD-1234",
                    "verification_uri": "https://github.com/login/device",
                    "interval": 1,
                    "expires_in": 900,
                }
            ),
            MockResponse({"error": "authorization_pending"}),
            MockResponse({"access_token": "github-access-token"}),
        ]
        get.return_value = MockResponse({"login": "ab-10"})

        with patch.dict(
            os.environ,
            {"SPX_GITHUB_CLIENT_ID": "client-123", "FSTAK_DOMAIN_SUFFIX": "test.example.com"},
            clear=True,
        ):
            client = TestClient(app)
            start = client.post("/auth/device")
            self.assertEqual(start.status_code, 200, start.text)
            started = start.json()

            pending = client.post("/auth/token", json={"poll_token": started["poll_token"]})
            self.assertEqual(pending.status_code, 200, pending.text)
            self.assertEqual(pending.json(), {"status": "pending"})

            ready = client.post("/auth/token", json={"poll_token": started["poll_token"]})

        self.assertEqual(ready.status_code, 200, ready.text)
        body = ready.json()
        self.assertEqual(body["status"], "ready")
        self.assertEqual(body["username"], "ab-10")
        self.assertTrue(body["fstak_token"].startswith("ab-10."))
        self.assertEqual(post.call_args_list[0].kwargs["data"]["client_id"], "client-123")
        self.assertEqual(post.call_args_list[1].kwargs["data"]["device_code"], "github-device-code")
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer github-access-token")


if __name__ == "__main__":
    unittest.main()
