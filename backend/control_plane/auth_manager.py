"""Minimal in-memory device flow + code auth manager for local/MVP development.

This is intentionally simple and non-persistent. It exists so that the CLI's
`fstak login` (device flow and --code) can succeed against a running control plane
during development and testing.

Production will replace this with the shared SPX-style auth system.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Optional


@dataclass
class DeviceSession:
    user_code: str
    poll_token: str
    expires_at: float
    approved: bool = False
    username: Optional[str] = None


@dataclass
class IssuedToken:
    token: str
    account_id: str
    username: str
    issued_at: float
    expires_at: float


class AuthManager:
    def __init__(self, token_ttl_seconds: int = 3600 * 24 * 30) -> None:
        self._lock = Lock()
        self._sessions: dict[str, DeviceSession] = {}  # poll_token -> session
        self._user_codes: dict[str, str] = {}          # user_code -> poll_token (for UX)
        self._tokens: dict[str, IssuedToken] = {}      # token -> IssuedToken
        self._token_ttl = token_ttl_seconds

    # ---------- Device flow (used by `fstak login`) ----------

    def create_device_session(self, interval: int = 5, expires_in: int = 900) -> dict:
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
        with self._lock:
            poll_token = secrets.token_urlsafe(24)
            # Human-friendly code (e.g. "ABCD-EFGH")
            parts = []
            alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
            for _ in range(2):
                parts.append("".join(secrets.choice(alphabet) for _ in range(4)))
            user_code = "-".join(parts)

            now = time.time()
            session = DeviceSession(
                user_code=user_code,
                poll_token=poll_token,
                expires_at=now + expires_in,
            )
            self._sessions[poll_token] = session
            self._user_codes[user_code] = poll_token

            # For local dev we point at a placeholder.
            # In a real deployment this would be the GitHub OAuth or SPX device page.
            verification_uri = "https://github.com/login/device"  # placeholder

            return {
                "user_code": user_code,
                "verification_uri": verification_uri,
                "poll_token": poll_token,
                "interval": interval,
                "expires_in": expires_in,
            }

    def poll_device_token(self, poll_token: str) -> dict:
        """Called repeatedly by the CLI after create_device_session.

        Returns:
            {"status": "pending" | "ready" | "expired", "fstak_token"?, "username"? }
        """
        with self._lock:
            session = self._sessions.get(poll_token)
            if session is None:
                return {"status": "expired"}

            if time.time() > session.expires_at:
                # Clean up
                self._sessions.pop(poll_token, None)
                self._user_codes.pop(session.user_code, None)
                return {"status": "expired"}

            if not session.approved:
                # For local development we auto-approve on first poll.
                # This makes `fstak login` (device flow) succeed without browser interaction.
                # A real implementation would wait for the user to complete OAuth on the verification_uri.
                session.approved = True
                session.username = session.username or "local-dev"

            if session.approved:
                token = self._issue_token(session.username or "local-dev")
                return {
                    "status": "ready",
                    "fstak_token": token,
                    "username": session.username,
                }

            return {"status": "pending"}

    # ---------- Code bypass (used by `fstak login --code`) ----------

    def redeem_code(self, code: str) -> dict:
        """Redeem a registration / bootstrap code.

        For local development any non-empty code works and yields a token.
        The username is derived from the code for determinism during testing
        (e.g. code "alice" → user "alice").
        """
        if not code or not code.strip():
            return {"status": "error"}

        # Normalize: take first segment or the whole string as username
        username = code.split(".", 1)[0].strip() or "local-code-user"

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


# Singleton for the app to import
manager = AuthManager()
