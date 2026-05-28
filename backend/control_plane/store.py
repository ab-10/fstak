from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from threading import Lock
from typing import Any
from uuid import uuid4

import asyncpg

from .config import get_settings
from .models import Deployment, Project, utcnow


def _sha256_bytes(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


class InMemoryStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self.projects: dict[str, Project] = {}
        self.deployments: dict[str, Deployment] = {}
        self.project_env: dict[str, dict[str, str]] = {}
        self.project_deps: dict[str, set[str]] = {}
        self.account_projects: dict[str, set[str]] = {}

    def reset_for_tests(self) -> None:
        with self._lock:
            self.projects.clear()
            self.deployments.clear()
            self.project_env.clear()
            self.project_deps.clear()
            self.account_projects.clear()

    async def upsert_project(self, account_id: str, project_name: str, project_slug: str | None) -> Project:
        with self._lock:
            if project_slug and project_slug in self.projects:
                project = self.projects[project_slug]
                if project.account_id != account_id:
                    raise PermissionError("not your project")
                project.updated_at = utcnow()
                return project

            slug = project_slug or f"{project_name}-{uuid4().hex[:8]}"
            domain = f"https://{slug}.{get_settings().domain_suffix}"
            project = Project(slug=slug, account_id=account_id, project_name=project_name, domain=domain)
            self.projects[slug] = project
            self.account_projects.setdefault(account_id, set()).add(slug)
            self.project_env.setdefault(slug, {})
            self.project_deps.setdefault(slug, set())
            return project

    async def create_deployment(self, project_slug: str) -> Deployment:
        with self._lock:
            deployment = Deployment(
                id=f"dep_{uuid4().hex}",
                project_slug=project_slug,
                asset_prefix=f"deployments/{project_slug}/pending",
                status="building",
            )
            self.deployments[deployment.id] = deployment
            return deployment

    async def finalize_deployment(
        self,
        deployment_id: str,
        *,
        asset_prefix: str,
        source_hash: str,
        artifact_hash: str,
        manifest_json: dict[str, Any],
        build_seconds: float,
        upload_seconds: float,
        route_update_seconds: float,
    ) -> Deployment:
        with self._lock:
            d = self.deployments[deployment_id]
            d.status = "ready"
            d.asset_prefix = asset_prefix
            d.source_hash = source_hash
            d.artifact_hash = artifact_hash
            d.manifest_hash = _sha256_bytes(json.dumps(manifest_json, sort_keys=True).encode("utf-8"))
            d.build_seconds = build_seconds
            d.upload_seconds = upload_seconds
            d.route_update_seconds = route_update_seconds
            d.error = None
            p = self.projects[d.project_slug]
            p.active_deployment_id = d.id
            p.updated_at = utcnow()
            return d

    async def fail_deployment(self, deployment_id: str, *, build_seconds: float, upload_seconds: float, route_update_seconds: float, error: str) -> Deployment:
        with self._lock:
            d = self.deployments[deployment_id]
            d.status = "failed"
            d.build_seconds = build_seconds
            d.upload_seconds = upload_seconds
            d.route_update_seconds = route_update_seconds
            d.error = error
            return d

    async def list_projects_for_account(self, account_id: str) -> list[dict[str, Any]]:
        with self._lock:
            out: list[dict[str, Any]] = []
            for slug in sorted(self.account_projects.get(account_id, set())):
                p = self.projects[slug]
                out.append({"slug": p.slug, "project_name": p.project_name, "url": p.domain, "active_deployment_id": p.active_deployment_id, "updated_at": p.updated_at.isoformat()})
            return out

    async def ensure_project_owner(self, account_id: str, project_slug: str) -> Project:
        p = self.projects.get(project_slug)
        if p is None:
            raise KeyError(project_slug)
        if p.account_id != account_id:
            raise PermissionError("not your project")
        return p

    async def kill_project(self, project_slug: str) -> None:
        with self._lock:
            p = self.projects[project_slug]
            p.active_deployment_id = None
            p.updated_at = utcnow()

    async def list_deployments_for_project(self, project_slug: str) -> list[dict[str, Any]]:
        with self._lock:
            items = [d for d in self.deployments.values() if d.project_slug == project_slug]
            items.sort(key=lambda d: d.created_at, reverse=True)
            return [asdict(d) | {"created_at": d.created_at.isoformat()} for d in items]

    async def list_env(self, project_slug: str) -> dict[str, str]:
        return dict(self.project_env.get(project_slug, {}))

    async def set_env(self, project_slug: str, key: str, value: str) -> None:
        self.project_env.setdefault(project_slug, {})[key] = value

    async def unset_env(self, project_slug: str, key: str) -> None:
        self.project_env.setdefault(project_slug, {}).pop(key, None)

    async def list_deps(self, project_slug: str) -> list[str]:
        return sorted(self.project_deps.get(project_slug, set()))

    async def set_dep(self, project_slug: str, pkg: str) -> None:
        self.project_deps.setdefault(project_slug, set()).add(pkg)

    async def unset_dep(self, project_slug: str, pkg: str) -> None:
        self.project_deps.setdefault(project_slug, set()).discard(pkg)

    async def list_active_project_routes(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        with self._lock:
            for slug, p in self.projects.items():
                if p.active_deployment_id:
                    out.append((slug, p.active_deployment_id))
        return out


class PostgresStore:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url.replace("postgres://", "postgresql://", 1)
        self._pool: asyncpg.Pool | None = None

    async def init(self) -> None:
        self._pool = await asyncpg.create_pool(self._database_url, min_size=1, max_size=5)
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                create table if not exists fstak_projects (
                  slug text primary key,
                  account_id text not null,
                  project_name text not null,
                  domain text not null,
                  active_deployment_id text,
                  created_at timestamptz not null default now(),
                  updated_at timestamptz not null default now()
                );
                create table if not exists fstak_deployments (
                  id text primary key,
                  project_slug text not null references fstak_projects(slug) on delete cascade,
                  asset_prefix text not null,
                  status text not null,
                  source_hash text,
                  artifact_hash text,
                  manifest_hash text,
                  build_seconds double precision not null default 0,
                  upload_seconds double precision not null default 0,
                  route_update_seconds double precision not null default 0,
                  error text,
                  created_at timestamptz not null default now()
                );
                create table if not exists fstak_project_env (
                  project_slug text not null references fstak_projects(slug) on delete cascade,
                  key text not null,
                  value text not null,
                  primary key (project_slug, key)
                );
                create table if not exists fstak_project_deps (
                  project_slug text not null references fstak_projects(slug) on delete cascade,
                  name text not null,
                  primary key (project_slug, name)
                );
                """
            )

    def _p(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("postgres store not initialized")
        return self._pool

    async def upsert_project(self, account_id: str, project_name: str, project_slug: str | None) -> Project:
        slug = project_slug or f"{project_name}-{uuid4().hex[:8]}"
        domain = f"https://{slug}.{get_settings().domain_suffix}"
        async with self._p().acquire() as conn:
            row = await conn.fetchrow("select slug, account_id, project_name, domain, active_deployment_id, created_at, updated_at from fstak_projects where slug=$1", slug)
            if row is not None:
                if row["account_id"] != account_id:
                    raise PermissionError("not your project")
                await conn.execute("update fstak_projects set updated_at=now() where slug=$1", slug)
                row = await conn.fetchrow("select * from fstak_projects where slug=$1", slug)
            else:
                await conn.execute(
                    "insert into fstak_projects (slug, account_id, project_name, domain) values ($1,$2,$3,$4)",
                    slug, account_id, project_name, domain,
                )
                row = await conn.fetchrow("select * from fstak_projects where slug=$1", slug)
        assert row is not None
        return Project(slug=row["slug"], account_id=row["account_id"], project_name=row["project_name"], domain=row["domain"], active_deployment_id=row["active_deployment_id"], created_at=row["created_at"], updated_at=row["updated_at"])

    async def create_deployment(self, project_slug: str) -> Deployment:
        deployment_id = f"dep_{uuid4().hex}"
        async with self._p().acquire() as conn:
            await conn.execute(
                "insert into fstak_deployments (id, project_slug, asset_prefix, status) values ($1,$2,$3,$4)",
                deployment_id, project_slug, f"deployments/{project_slug}/pending", "building",
            )
            row = await conn.fetchrow("select * from fstak_deployments where id=$1", deployment_id)
        assert row is not None
        return Deployment(
            id=row["id"], project_slug=row["project_slug"], asset_prefix=row["asset_prefix"], status=row["status"],
            source_hash=row["source_hash"], artifact_hash=row["artifact_hash"], manifest_hash=row["manifest_hash"],
            build_seconds=row["build_seconds"], upload_seconds=row["upload_seconds"], route_update_seconds=row["route_update_seconds"], error=row["error"], created_at=row["created_at"],
        )

    async def finalize_deployment(self, deployment_id: str, *, asset_prefix: str, source_hash: str, artifact_hash: str, manifest_json: dict[str, Any], build_seconds: float, upload_seconds: float, route_update_seconds: float) -> Deployment:
        manifest_hash = _sha256_bytes(json.dumps(manifest_json, sort_keys=True).encode("utf-8"))
        async with self._p().acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow("select project_slug from fstak_deployments where id=$1", deployment_id)
                if row is None:
                    raise KeyError(deployment_id)
                project_slug = row["project_slug"]
                await conn.execute(
                    """
                    update fstak_deployments
                    set status='ready', asset_prefix=$2, source_hash=$3, artifact_hash=$4, manifest_hash=$5,
                        build_seconds=$6, upload_seconds=$7, route_update_seconds=$8, error=null
                    where id=$1
                    """,
                    deployment_id, asset_prefix, source_hash, artifact_hash, manifest_hash, build_seconds, upload_seconds, route_update_seconds,
                )
                await conn.execute(
                    "update fstak_projects set active_deployment_id=$2, updated_at=now() where slug=$1",
                    project_slug, deployment_id,
                )
                full = await conn.fetchrow("select * from fstak_deployments where id=$1", deployment_id)
        assert full is not None
        return Deployment(
            id=full["id"], project_slug=full["project_slug"], asset_prefix=full["asset_prefix"], status=full["status"],
            source_hash=full["source_hash"], artifact_hash=full["artifact_hash"], manifest_hash=full["manifest_hash"],
            build_seconds=full["build_seconds"], upload_seconds=full["upload_seconds"], route_update_seconds=full["route_update_seconds"],
            error=full["error"], created_at=full["created_at"],
        )

    async def fail_deployment(self, deployment_id: str, *, build_seconds: float, upload_seconds: float, route_update_seconds: float, error: str) -> Deployment:
        async with self._p().acquire() as conn:
            await conn.execute(
                "update fstak_deployments set status='failed', build_seconds=$2, upload_seconds=$3, route_update_seconds=$4, error=$5 where id=$1",
                deployment_id, build_seconds, upload_seconds, route_update_seconds, error,
            )
            row = await conn.fetchrow("select * from fstak_deployments where id=$1", deployment_id)
        assert row is not None
        return Deployment(
            id=row["id"], project_slug=row["project_slug"], asset_prefix=row["asset_prefix"], status=row["status"],
            source_hash=row["source_hash"], artifact_hash=row["artifact_hash"], manifest_hash=row["manifest_hash"],
            build_seconds=row["build_seconds"], upload_seconds=row["upload_seconds"], route_update_seconds=row["route_update_seconds"], error=row["error"], created_at=row["created_at"],
        )

    async def list_projects_for_account(self, account_id: str) -> list[dict[str, Any]]:
        async with self._p().acquire() as conn:
            rows = await conn.fetch("select slug, project_name, domain, active_deployment_id, updated_at from fstak_projects where account_id=$1 order by slug asc", account_id)
        return [{"slug": r["slug"], "project_name": r["project_name"], "url": r["domain"], "active_deployment_id": r["active_deployment_id"], "updated_at": r["updated_at"].isoformat()} for r in rows]

    async def ensure_project_owner(self, account_id: str, project_slug: str) -> Project:
        async with self._p().acquire() as conn:
            row = await conn.fetchrow("select * from fstak_projects where slug=$1", project_slug)
        if row is None:
            raise KeyError(project_slug)
        if row["account_id"] != account_id:
            raise PermissionError("not your project")
        return Project(slug=row["slug"], account_id=row["account_id"], project_name=row["project_name"], domain=row["domain"], active_deployment_id=row["active_deployment_id"], created_at=row["created_at"], updated_at=row["updated_at"])

    async def kill_project(self, project_slug: str) -> None:
        async with self._p().acquire() as conn:
            await conn.execute("update fstak_projects set active_deployment_id=null, updated_at=now() where slug=$1", project_slug)

    async def list_deployments_for_project(self, project_slug: str) -> list[dict[str, Any]]:
        async with self._p().acquire() as conn:
            rows = await conn.fetch("select * from fstak_deployments where project_slug=$1 order by created_at desc", project_slug)
        return [{
            "id": r["id"], "project_slug": r["project_slug"], "asset_prefix": r["asset_prefix"], "status": r["status"],
            "source_hash": r["source_hash"], "artifact_hash": r["artifact_hash"], "manifest_hash": r["manifest_hash"],
            "build_seconds": r["build_seconds"], "upload_seconds": r["upload_seconds"], "route_update_seconds": r["route_update_seconds"],
            "error": r["error"], "created_at": r["created_at"].isoformat(),
        } for r in rows]

    async def list_env(self, project_slug: str) -> dict[str, str]:
        async with self._p().acquire() as conn:
            rows = await conn.fetch("select key, value from fstak_project_env where project_slug=$1", project_slug)
        return {r["key"]: r["value"] for r in rows}

    async def set_env(self, project_slug: str, key: str, value: str) -> None:
        async with self._p().acquire() as conn:
            await conn.execute(
                "insert into fstak_project_env (project_slug, key, value) values ($1,$2,$3) on conflict (project_slug, key) do update set value=excluded.value",
                project_slug, key, value,
            )

    async def unset_env(self, project_slug: str, key: str) -> None:
        async with self._p().acquire() as conn:
            await conn.execute("delete from fstak_project_env where project_slug=$1 and key=$2", project_slug, key)

    async def list_deps(self, project_slug: str) -> list[str]:
        async with self._p().acquire() as conn:
            rows = await conn.fetch("select name from fstak_project_deps where project_slug=$1 order by name asc", project_slug)
        return [r["name"] for r in rows]

    async def set_dep(self, project_slug: str, pkg: str) -> None:
        async with self._p().acquire() as conn:
            await conn.execute(
                "insert into fstak_project_deps (project_slug, name) values ($1,$2) on conflict (project_slug, name) do nothing",
                project_slug, pkg,
            )

    async def unset_dep(self, project_slug: str, pkg: str) -> None:
        async with self._p().acquire() as conn:
            await conn.execute("delete from fstak_project_deps where project_slug=$1 and name=$2", project_slug, pkg)

    async def list_active_project_routes(self) -> list[tuple[str, str]]:
        async with self._p().acquire() as conn:
            rows = await conn.fetch(
                "select slug, active_deployment_id from fstak_projects where active_deployment_id is not null"
            )
        return [(r["slug"], r["active_deployment_id"]) for r in rows]


class StoreFacade:
    def __init__(self) -> None:
        self._mem = InMemoryStore()
        self._backend: Any = self._mem

    async def configure(self, database_url: str) -> None:
        if database_url:
            pg = PostgresStore(database_url)
            await pg.init()
            self._backend = pg

    def reset_for_tests(self) -> None:
        self._mem.reset_for_tests()
        self._backend = self._mem

    def __getattr__(self, name: str) -> Any:
        return getattr(self._backend, name)


store = StoreFacade()
