from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Deployment:
    id: str
    project_slug: str
    asset_prefix: str
    status: str = "pending"
    source_hash: str | None = None
    artifact_hash: str | None = None
    manifest_hash: str | None = None
    build_seconds: float = 0.0
    upload_seconds: float = 0.0
    route_update_seconds: float = 0.0
    error: str | None = None
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class Project:
    slug: str
    account_id: str
    project_name: str
    domain: str
    active_deployment_id: str | None = None
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)
