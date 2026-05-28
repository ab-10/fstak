from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from control_plane.app import app
from control_plane.auth import get_spx_auth_client
from control_plane.config import get_settings


class AuthProxyTests(unittest.TestCase):
    """fstak's /auth/* endpoints proxy to SPX; no GitHub calls happen in-process."""

    def setUp(self) -> None:
        get_settings.cache_clear()
        get_spx_auth_client.cache_clear()
        self._env_patch = patch.dict(
            os.environ,
            {
                "FSTAK_DOMAIN_SUFFIX": "test.example.com",
                "FSTAK_SPX_API_URL": "https://api.runspx.com",
            },
            clear=True,
        )
        self._env_patch.start()

    def tearDown(self) -> None:
        self._env_patch.stop()
        get_settings.cache_clear()
        get_spx_auth_client.cache_clear()

    def test_device_endpoint_proxies_response_from_spx(self) -> None:
        spx_payload = {
            "user_code": "ABCD-1234",
            "verification_uri": "https://github.com/login/device",
            "poll_token": "spx-poll-token",
            "interval": 5,
            "expires_in": 900,
        }
        with patch(
            "control_plane.spx_auth_client.SpxAuthClient.start_device_auth",
            new=AsyncMock(return_value=spx_payload),
        ):
            client = TestClient(app)
            response = client.post("/auth/device")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), spx_payload)

    def test_token_endpoint_forwards_poll_token(self) -> None:
        spx_payload = {
            "status": "ready",
            "spx_token": "spx-token-abc",
            "username": "ab-10",
        }
        mock = AsyncMock(return_value=spx_payload)
        with patch(
            "control_plane.spx_auth_client.SpxAuthClient.poll_device_token", new=mock
        ):
            client = TestClient(app)
            response = client.post("/auth/token", json={"poll_token": "spx-poll-token"})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), spx_payload)
        mock.assert_awaited_once_with("spx-poll-token")

    def test_whoami_proxies_spx_identity_shape(self) -> None:
        identity = {
            "account_id": "spx-account-uuid",
            "spx_username": "ab-10",
            "github_username": "ab-10",
        }
        with patch(
            "control_plane.spx_auth_client.SpxAuthClient.whoami",
            new=AsyncMock(return_value=identity),
        ):
            client = TestClient(app)
            response = client.get(
                "/auth/whoami", headers={"Authorization": "Bearer spx-token-abc"}
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), identity)

    def test_whoami_rejects_missing_bearer(self) -> None:
        client = TestClient(app)
        response = client.get("/auth/whoami")
        self.assertEqual(response.status_code, 401)

    def test_logout_proxies_to_spx_and_returns_204(self) -> None:
        revoke = AsyncMock(return_value=None)
        with patch(
            "control_plane.spx_auth_client.SpxAuthClient.revoke_session", new=revoke
        ):
            client = TestClient(app)
            response = client.delete(
                "/auth/session", headers={"Authorization": "Bearer spx-token-abc"}
            )

        self.assertEqual(response.status_code, 204)
        revoke.assert_awaited_once_with("spx-token-abc")


if __name__ == "__main__":
    unittest.main()
