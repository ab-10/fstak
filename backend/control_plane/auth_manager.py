"""In-memory GitHub device OAuth and fstak token manager.

This is intentionally non-persistent for the current MVP, but device login uses
GitHub OAuth instead of a local auto-approve fallback.
"""

from __future__ import annotations

import logging
import os
import secrets
import time
from dataclasses import dataclass
from threading import Lock
from typing import Optional

import httpx


logger = logging.getLogger(__name__)


GITHUB_DEVICE_CODE_URL = "https://github.com/login/device/code"
GITHUB_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"


@dataclass
class DeviceSession:
    user_code: str
    poll_token: str
    device_code: str
    expires_at: float


@dataclass
class IssuedToken:
    token: str
    account_id: str
    username: str
    issued_at: float
    expires_at: float


class EphemeralAuthManager:
    """In-memory, non-persistent auth manager for the fstak control plane.

    All device sessions and issued tokens live only in process memory and are
    lost on every restart. The class name carries the ``Ephemeral`` prefix to
    make this property explicit at every call site.

    This is acceptable for the MVP, but production deployments MUST replace
    this with a persistent implementation (e.g. backed by Postgres or the
    shared SPX auth service) so that issued tokens survive process restarts
    and rolling deploys.
    """

    def __init__(self, token_ttl_seconds: int = 3600 * 24 * 30) -> None:
        self._lock = Lock()
        self._sessions: dict[str, DeviceSession] = {}  # poll_token -> session
        self._user_codes: dict[str, str] = {}          # user_code -> poll_token (for UX)
        self._tokens: dict[str, IssuedToken] = {}      # token -> IssuedToken
        self._token_ttl = token_ttl_seconds
        logger.warning(
            "EphemeralAuthManager initialized: all sessions and issued tokens "
            "will be lost on process restart. This is acceptable for MVP but "
            "must be replaced with a persistent store before relying on this "
            "in production."
        )

    # ---------- Device flow (used by `fstak login`) ----------

    def create_device_session(self, github_client_id: str) -> dict:
        """Create a new device authorization session.

        Returns the shape expected by the CLI:
            {
              "user_code": "...",
              "verification_uri": "...",
              "poll_token": "...",
              "interval": 5,
              "expires_in": 900,
            }
        """
        github_client_id = github_client_id.strip()
        if not github_client_id:
            raise RuntimeError("SPX_GITHUB_CLIENT_ID is not configured")

        response = httpx.post(
            GITHUB_DEVICE_CODE_URL,
            data={"client_id": github_client_id, "scope": "read:user"},
            headers={"Accept": "application/json"},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()

        device_code = payload.get("device_code")
        user_code = payload.get("user_code")
        verification_uri = payload.get("verification_uri")
        interval = int(payload.get("interval", 5))
        expires_in = int(payload.get("expires_in", 900))
        if not device_code or not user_code or not verification_uri:
            raise RuntimeError("GitHub device auth response was missing required fields")

        poll_token = secrets.token_urlsafe(24)
        now = time.time()
        with self._lock:
            session = DeviceSession(
                user_code=user_code,
                poll_token=poll_token,
                device_code=device_code,
                expires_at=now + expires_in,
            )
            self._sessions[poll_token] = session
            self._user_codes[user_code] = poll_token

            return {
                "user_code": user_code,
                "verification_uri": verification_uri,
                "poll_token": poll_token,
                "interval": interval,
                "expires_in": expires_in,
            }

    def poll_device_token(self, github_client_id: str, poll_token: str) -> dict:
        """Called repeatedly by the CLI after create_device_session.

        Returns:
            {"status": "pending" | "ready" | "expired", "fstak_token"?, "username"? }
        """
        github_client_id = github_client_id.strip()
        if not github_client_id:
            raise RuntimeError("SPX_GITHUB_CLIENT_ID is not configured")

        with self._lock:
            session = self._sessions.get(poll_token)
            if session is None:
                return {"status": "expired"}

            if time.time() > session.expires_at:
                self._drop_session(session)
                return {"status": "expired"}

            device_code = session.device_code

        response = httpx.post(
            GITHUB_ACCESS_TOKEN_URL,
            data={
                "client_id": github_client_id,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            headers={"Accept": "application/json"},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()

        error = payload.get("error")
        if error == "authorization_pending":
            return {"status": "pending"}
        if error == "slow_down":
            return {"status": "pending"}
        if error in {"expired_token", "access_denied"}:
            with self._lock:
                session = self._sessions.get(poll_token)
                if session is not None:
                    self._drop_session(session)
            return {"status": "expired"}
        if error:
            raise RuntimeError(f"GitHub device auth failed: {error}")

        access_token = payload.get("access_token")
        if not access_token:
            raise RuntimeError("GitHub token response was missing access_token")

        username = self._github_username(access_token)
        token = self._issue_token(username)
        with self._lock:
            session = self._sessions.get(poll_token)
            if session is not None:
                self._drop_session(session)
        return {"status": "ready", "fstak_token": token, "username": username}

    # ---------- Code bypass (used by `fstak login --code`) ----------

    def redeem_code(self, code: str) -> dict:
        """Redeem a registration / bootstrap code.

        DEV-ONLY BYPASS: this path skips GitHub OAuth and trusts any non-empty
        code as a valid login. It is gated behind the ``FSTAK_ALLOW_DEV_LOGIN``
        environment variable (must be set to ``"1"``) and emits a warning on
        every successful use. Production environments MUST leave this unset.

        The username is derived from the code for determinism during testing
        (e.g. code "alice" → user "alice").
        """
        if os.environ.get("FSTAK_ALLOW_DEV_LOGIN") != "1":
            raise PermissionError(
                "code-based login is disabled; set FSTAK_ALLOW_DEV_LOGIN=1 "
                "to enable for development"
            )

        if not code or not code.strip():
            return {"status": "error"}

        # Normalize: take first segment or the whole string as username
        username = code.split(".", 1)[0].strip() or "local-code-user"

        logger.warning(
            "FSTAK_ALLOW_DEV_LOGIN is enabled - accepting unverified "
            "code-based login for user %s; this bypass must not be enabled "
            "in production",
            username,
        )

        token = self._issue_token(username)
        return {
            "status": "ready",
            "fstak_token": token,
            "username": username,
        }

    # ---------- Token validation (used by require_auth) ----------

    def validate_token(self, token: str) -> Optional[IssuedToken]:
        """Return IssuedToken if valid, else None."""
        with self._lock:
            it = self._tokens.get(token)
            if it is None:
                return None
            if time.time() > it.expires_at:
                self._tokens.pop(token, None)
                return None
            return it

    # ---------- Internal helpers ----------

    def _issue_token(self, username: str) -> str:
        """Create a new long-lived token for the given username."""
        # Simple but sufficient for local: username + random suffix
        # The real system will use proper JWTs or signed opaque tokens from the shared auth service.
        now = time.time()
        raw = f"{username}.{secrets.token_urlsafe(18)}"
        account_id = f"acct_{username}"

        it = IssuedToken(
            token=raw,
            account_id=account_id,
            username=username,
            issued_at=now,
            expires_at=now + self._token_ttl,
        )
        self._tokens[raw] = it
        return raw

    def _drop_session(self, session: DeviceSession) -> None:
        self._sessions.pop(session.poll_token, None)
        self._user_codes.pop(session.user_code, None)

    def _github_username(self, access_token: str) -> str:
        response = httpx.get(
            GITHUB_USER_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {access_token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=10,
        )
        response.raise_for_status()
        username = response.json().get("login")
        if not username:
            raise RuntimeError("GitHub user response was missing login")
        return username


# Singleton for the app to import
manager = EphemeralAuthManager()
