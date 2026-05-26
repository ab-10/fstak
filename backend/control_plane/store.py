from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from threading import Lock
from typing import Any
from uuid import uuid4

from .models import Deployment, Project, utcnow


class InMemoryStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self.projects: dict[str, Project] = {}
        self.deployments: dict[str, Deployment] = {}
        self.project_env: dict[str, dict[str, str]] = {}
        self.project_deps: dict[str, set[str]] = {}
        self.account_projects: dict[str, set[str]] = {}

    def upsert_project(self, account_id: str, project_name: str, project_slug: str | None) -> Project:
        with self._lock:
            if project_slug and project_slug in self.projects:
                project = self.projects[project_slug]
                if project.account_id != account_id:
                    raise PermissionError("not your project")
                project.updated_at = utcnow()
                return project

            slug = project_slug or f"{project_name}-{uuid4().hex[:8]}"
            domain = f"https://{slug}.fstak.runspx.com"
            project = Project(slug=slug, account_id=account_id, project_name=project_name, domain=domain)
            self.projects[slug] = project
            self.account_projects.setdefault(account_id, set()).add(slug)
            self.project_env.setdefault(slug, {})
            self.project_deps.setdefault(slug, set())
            return project

    def create_deployment(self, project_slug: str, asset_prefix: str) -> Deployment:
        with self._lock:
            deployment = Deployment(id=uuid4().hex, project_slug=project_slug, asset_prefix=asset_prefix)
            self.deployments[deployment.id] = deployment
            project = self.projects[project_slug]
            project.active_deployment_id = deployment.id
            project.updated_at = utcnow()
            return deployment

    def mark_deployment(
        self,
        deployment_id: str,
        *,
        status: str,
        build_seconds: float,
        upload_seconds: float,
        route_update_seconds: float,
        error: str | None = None,
    ) -> Deployment:
        with self._lock:
            d = self.deployments[deployment_id]
            d.status = status
            d.build_seconds = build_seconds
            d.upload_seconds = upload_seconds
            d.route_update_seconds = route_update_seconds
            d.error = error
            return d

    def list_projects_for_account(self, account_id: str) -> list[dict[str, Any]]:
        with self._lock:
            project_slugs = sorted(self.account_projects.get(account_id, set()))
            out: list[dict[str, Any]] = []
            for slug in project_slugs:
                p = self.projects[slug]
                out.append(
                    {
                        "slug": p.slug,
                        "project_name": p.project_name,
                        "url": p.domain,
                        "active_deployment_id": p.active_deployment_id,
                        "updated_at": p.updated_at.isoformat(),
                    }
                )
            return out

    def ensure_project_owner(self, account_id: str, project_slug: str) -> Project:
        p = self.projects.get(project_slug)
        if p is None:
            raise KeyError(project_slug)
        if p.account_id != account_id:
            raise PermissionError("not your project")
        return p

    def kill_project(self, project_slug: str) -> None:
        with self._lock:
            p = self.projects[project_slug]
            p.active_deployment_id = None
            p.updated_at = utcnow()

    def list_deployments_for_project(self, project_slug: str) -> list[dict[str, Any]]:
        with self._lock:
            items = [d for d in self.deployments.values() if d.project_slug == project_slug]
            items.sort(key=lambda d: d.created_at, reverse=True)
            return [
                {
                    "id": d.id,
                    "project_slug": d.project_slug,
                    "asset_prefix": d.asset_prefix,
                    "status": d.status,
                    "build_seconds": d.build_seconds,
                    "upload_seconds": d.upload_seconds,
                    "route_update_seconds": d.route_update_seconds,
                    "error": d.error,
                    "created_at": d.created_at.isoformat(),
                }
                for d in items
            ]

    def list_env(self, project_slug: str) -> dict[str, str]:
        return dict(self.project_env.get(project_slug, {}))

    def set_env(self, project_slug: str, key: str, value: str) -> None:
        self.project_env.setdefault(project_slug, {})[key] = value

    def unset_env(self, project_slug: str, key: str) -> None:
        self.project_env.setdefault(project_slug, {}).pop(key, None)

    def list_deps(self, project_slug: str) -> list[str]:
        return sorted(self.project_deps.get(project_slug, set()))

    def set_dep(self, project_slug: str, pkg: str) -> None:
        self.project_deps.setdefault(project_slug, set()).add(pkg)

    def unset_dep(self, project_slug: str, pkg: str) -> None:
        self.project_deps.setdefault(project_slug, set()).discard(pkg)


store = InMemoryStore()
