from __future__ import annotations

from dataclasses import dataclass

from fastapi import Header, HTTPException

from .auth_manager import manager


@dataclass
class AuthContext:
    account_id: str
    username: str


def require_auth(authorization: str | None = Header(default=None)) -> AuthContext:
    """Validate the Bearer token using the real auth manager.

    Rejects with 401 for missing or invalid tokens.
    On success returns the account identity resolved from the issued token.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")

    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="missing bearer token")

    issued = manager.validate_token(token)
    if issued is None:
        raise HTTPException(status_code=401, detail="invalid or expired token")

    return AuthContext(account_id=issued.account_id, username=issued.username)
