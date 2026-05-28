"""Client that proxies fstak auth operations to the SPX backend.

SPX is the source of truth for accounts, sessions, and tokens. The fstak
backend issues no tokens of its own; it forwards device-flow, code-flow,
whoami, and logout to SPX and uses a small in-process cache to keep the
per-request validation cost low.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx
from fastapi import HTTPException


logger = logging.getLogger(__name__)


@dataclass
class CachedIdentity:
    account_id: str
    spx_username: str
    github_username: Optional[str]
    expires_at: float


class SpxAuthClient:
    """Forwards auth calls to the SPX backend and caches whoami responses.

    Cache TTL is intentionally short (60 s) so that a `spx logout` (or any
    server-side revocation) propagates to fstak within one minute without
    requiring cache invalidation hooks across services. Cache misses that
    cannot reach SPX fail closed: `validate_token` returns ``None`` so
    `require_auth` rejects the request with 401 rather than allowing an
    unauthenticated call through.
    """

    _CACHE_TTL_SECONDS = 60
    _TIMEOUT_SECONDS = 5.0

    def __init__(self, spx_api_url: str) -> None:
        if not spx_api_url:
            raise ValueError(
                "FSTAK_SPX_API_URL must be set; SpxAuthClient cannot operate "
                "without an SPX API URL"
            )
        self._spx_api_url = spx_api_url.rstrip("/")
        self._cache: dict[str, CachedIdentity] = {}
        self._lock = asyncio.Lock()

    async def validate_token(self, token: str) -> Optional[CachedIdentity]:
        now = time.time()
        async with self._lock:
            cached = self._cache.get(token)
            if cached is not None and cached.expires_at > now:
                return cached

        try:
            async with httpx.AsyncClient(timeout=self._TIMEOUT_SECONDS) as client:
                resp = await client.get(
                    f"{self._spx_api_url}/auth/whoami",
                    headers={"Authorization": f"Bearer {token}"},
                )
        except httpx.HTTPError as exc:
            logger.error("SPX /auth/whoami transport error: %s", exc)
            return None

        if resp.status_code == 401 or resp.status_code == 403:
            async with self._lock:
                self._cache.pop(token, None)
            return None
        if resp.status_code >= 400:
            logger.error(
                "SPX /auth/whoami returned %s: %s", resp.status_code, resp.text
            )
            return None

        try:
            body = resp.json()
            identity = CachedIdentity(
                account_id=body["account_id"],
                spx_username=body["spx_username"],
                github_username=body.get("github_username"),
                expires_at=now + self._CACHE_TTL_SECONDS,
            )
        except (ValueError, KeyError) as exc:
            logger.error("SPX /auth/whoami returned unexpected body: %s", exc)
            return None

        async with self._lock:
            self._cache[token] = identity
        return identity

    async def whoami(self, token: str) -> dict[str, Any]:
        identity = await self.validate_token(token)
        if identity is None:
            raise HTTPException(status_code=401, detail="invalid or expired token")
        return {
            "account_id": identity.account_id,
            "spx_username": identity.spx_username,
            "github_username": identity.github_username,
        }

    async def start_device_auth(self) -> dict[str, Any]:
        return await self._proxy_post("/auth/device", payload={})

    async def poll_device_token(self, poll_token: str) -> dict[str, Any]:
        return await self._proxy_post("/auth/token", payload={"poll_token": poll_token})

    async def redeem_code(self, code: str) -> dict[str, Any]:
        return await self._proxy_post("/auth/code", payload={"code": code})

    async def revoke_session(self, token: str) -> None:
        async with self._lock:
            self._cache.pop(token, None)
        try:
            async with httpx.AsyncClient(timeout=self._TIMEOUT_SECONDS) as client:
                resp = await client.delete(
                    f"{self._spx_api_url}/auth/session",
                    headers={"Authorization": f"Bearer {token}"},
                )
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502, detail=f"SPX auth unreachable: {exc}"
            ) from exc
        if resp.status_code >= 500:
            raise HTTPException(
                status_code=502,
                detail=f"SPX returned {resp.status_code}: {resp.text}",
            )

    async def _proxy_post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self._TIMEOUT_SECONDS) as client:
                resp = await client.post(f"{self._spx_api_url}{path}", json=payload)
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502, detail=f"SPX auth unreachable: {exc}"
            ) from exc
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail") or resp.text
            except ValueError:
                detail = resp.text
            raise HTTPException(status_code=resp.status_code, detail=detail)
        try:
            return resp.json()
        except ValueError as exc:
            raise HTTPException(
                status_code=502, detail=f"SPX returned non-JSON body: {exc}"
            ) from exc
