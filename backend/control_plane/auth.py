from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from fastapi import Header, HTTPException

from .config import get_settings
from .spx_auth_client import SpxAuthClient


@dataclass
class AuthContext:
    account_id: str
    username: str


@lru_cache(maxsize=1)
def get_spx_auth_client() -> SpxAuthClient:
    settings = get_settings()
    return SpxAuthClient(settings.spx_api_url)


def _extract_bearer(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="missing bearer token")
    return token


async def require_auth(authorization: str | None = Header(default=None)) -> AuthContext:
    """Validate the Bearer token against the SPX backend.

    Rejects with 401 for missing, malformed, or invalid tokens. On success
    returns the account identity resolved from SPX (spx_username is exposed
    as ``AuthContext.username`` for backwards compatibility with callers).
    """
    token = _extract_bearer(authorization)
    client = get_spx_auth_client()
    identity = await client.validate_token(token)
    if identity is None:
        raise HTTPException(status_code=401, detail="invalid or expired token")
    return AuthContext(account_id=identity.account_id, username=identity.spx_username)
