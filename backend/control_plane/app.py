from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .auth import AuthContext, require_auth
from .auth_manager import manager
from .caddy import CaddyClient
from .config import get_settings
from .storage import AssetStorage
from .store import store

app = FastAPI(title="fstak control plane", description="Control plane for fstak static SPA deployments.", version="0.2.0")


def _asset_storage() -> AssetStorage:
    settings = get_settings()
    return AssetStorage(settings.gcs_bucket_name)


def _caddy_client() -> CaddyClient:
    settings = get_settings()
    return CaddyClient(settings.caddy_admin_url, settings.domain_suffix, settings.gcs_bucket_name)


@app.on_event("startup")
async def startup() -> None:
    settings = get_settings()
    await store.configure(settings.database_url)
    caddy = _caddy_client()
    route_lister = getattr(store, "list_active_project_routes", None)
    if route_lister is None:
        return
    for project_slug, deployment_id in await route_lister():
        await caddy.upsert_project_route(project_slug, deployment_id)


def _extract_archive(archive_bytes: bytes, workdir: Path) -> Path:
    src_dir = workdir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tf:
        tf.extractall(src_dir, filter="data")
    return src_dir


def _run_build(src_dir: Path, deps: list[str], env_vars: dict[str, str]) -> tuple[Path, str]:
    bun = shutil.which("bun")
    if bun is None:
        raise RuntimeError(
            "bun is required for the build pipeline but was not found on PATH; install bun on the build host"
        )

    env = os.environ.copy()
    env.setdefault("CI", "1")
    env.update(env_vars)

    if deps:
        add = subprocess.run([bun, "add", *deps], cwd=src_dir, env=env, capture_output=True, text=True)
        if add.returncode != 0:
            raise RuntimeError(f"bun add failed: {add.stderr.strip() or add.stdout.strip()}")
        strategy = "bun+deps"
    else:
        install = subprocess.run(
            [bun, "install", "--frozen-lockfile"], cwd=src_dir, env=env, capture_output=True, text=True
        )
        if install.returncode != 0:
            raise RuntimeError(f"bun install failed: {install.stderr.strip() or install.stdout.strip()}")
        strategy = "bun"

    build = subprocess.run([bun, "run", "build"], cwd=src_dir, env=env, capture_output=True, text=True)
    if build.returncode != 0:
        raise RuntimeError(f"bun build failed: {build.stderr.strip() or build.stdout.strip()}")

    dist = src_dir / "dist"
    if not dist.exists():
        raise RuntimeError("build completed but dist/ was not produced")
    if not (dist / "index.html").is_file():
        raise RuntimeError("build completed but dist/index.html was not produced")
    return dist, strategy


def _sha256_bytes(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


@app.get("/health")
def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.post("/auth/device")
async def auth_device() -> dict:
    settings = get_settings()
    if not settings.spx_github_client_id:
        raise HTTPException(status_code=500, detail="SPX_GITHUB_CLIENT_ID is not configured")
    return manager.create_device_session(settings.spx_github_client_id)


@app.post("/auth/token")
async def auth_token(request: Request) -> dict:
    body = await request.json()
    poll_token = body.get("poll_token", "")
    settings = get_settings()
    if not settings.spx_github_client_id:
        raise HTTPException(status_code=500, detail="SPX_GITHUB_CLIENT_ID is not configured")
    return manager.poll_device_token(settings.spx_github_client_id, poll_token)


@app.post("/auth/code")
async def auth_code(request: Request) -> dict:
    body = await request.json()
    code = body.get("code", "")
    try:
        return manager.redeem_code(code)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.get("/auth/whoami")
def auth_whoami(auth: AuthContext = Depends(require_auth)) -> JSONResponse:
    return JSONResponse({"account_id": auth.account_id, "username": auth.username})


class EnvSetPayload(BaseModel):
    value: str


class DepSetPayload(BaseModel):
    requirement: str


@app.post("/run")
async def run_deploy(
    code: UploadFile = File(...),
    project_name: str = Form(...),
    project_slug: str | None = Form(default=None),
    auth: AuthContext = Depends(require_auth),
) -> JSONResponse:
    storage = _asset_storage()
    caddy = _caddy_client()

    try:
        project = await store.upsert_project(auth.account_id, project_name, project_slug)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    deployment = await store.create_deployment(project.slug)
    build_seconds = 0.0
    upload_seconds = 0.0
    route_update_seconds = 0.0
    strategy = "unknown"

    with tempfile.TemporaryDirectory(prefix="fstak-build-") as tmpdir:
        workdir = Path(tmpdir)
        try:
            archive_bytes = await code.read()
            source_hash = _sha256_bytes(archive_bytes)

            start = time.perf_counter()
            src_dir = _extract_archive(archive_bytes, workdir)
            deps = await store.list_deps(project.slug)
            env_vars = await store.list_env(project.slug)
            dist_dir, strategy = _run_build(src_dir, deps, env_vars)
            build_seconds = time.perf_counter() - start

            start = time.perf_counter()
            asset_prefix = f"deployments/{project.slug}/{deployment.id}"
            uploaded = await storage.upload_dist(asset_prefix, dist_dir)
            upload_seconds = time.perf_counter() - start

            manifest: dict[str, Any] = {
                "deployment_id": deployment.id,
                "project_slug": project.slug,
                "source_hash": source_hash,
                "artifact_hash": uploaded["artifact_hash"],
                "files": uploaded["files"],
            }

            start = time.perf_counter()
            await store.finalize_deployment(
                deployment.id,
                asset_prefix=asset_prefix,
                source_hash=source_hash,
                artifact_hash=uploaded["artifact_hash"],
                manifest_json=manifest,
                build_seconds=build_seconds,
                upload_seconds=upload_seconds,
                route_update_seconds=0.0,
            )
            await caddy.upsert_project_route(project.slug, deployment.id)
            route_update_seconds = time.perf_counter() - start
        except Exception as exc:  # noqa: BLE001
            await store.fail_deployment(
                deployment.id,
                build_seconds=build_seconds,
                upload_seconds=upload_seconds,
                route_update_seconds=route_update_seconds,
                error=str(exc),
            )
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return JSONResponse(
        {
            "url": project.domain,
            "project_name": project.project_name,
            "project_slug": project.slug,
            "deployment_id": deployment.id,
            "status": "ready",
            "build_strategy": strategy,
            "timings": {
                "build_seconds": round(build_seconds, 3),
                "upload_seconds": round(upload_seconds, 3),
                "route_update_seconds": round(route_update_seconds, 3),
            },
        }
    )


@app.get("/projects")
async def list_projects(auth: AuthContext = Depends(require_auth)) -> JSONResponse:
    return JSONResponse(await store.list_projects_for_account(auth.account_id))


@app.get("/projects/{project_slug}")
async def project_details(project_slug: str, auth: AuthContext = Depends(require_auth)) -> JSONResponse:
    try:
        project = await store.ensure_project_owner(auth.account_id, project_slug)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return JSONResponse(
        {
            "slug": project.slug,
            "project_name": project.project_name,
            "url": project.domain,
            "active_deployment_id": project.active_deployment_id,
            "updated_at": project.updated_at.isoformat(),
        }
    )


@app.get("/projects/{project_slug}/deployments")
async def list_deployments(project_slug: str, auth: AuthContext = Depends(require_auth)) -> JSONResponse:
    try:
        await store.ensure_project_owner(auth.account_id, project_slug)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return JSONResponse(await store.list_deployments_for_project(project_slug))


@app.post("/projects/{project_slug}/kill")
async def kill_project(project_slug: str, auth: AuthContext = Depends(require_auth)) -> JSONResponse:
    caddy = _caddy_client()
    try:
        await store.ensure_project_owner(auth.account_id, project_slug)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    await store.kill_project(project_slug)
    await caddy.remove_project_route(project_slug)
    return JSONResponse({"status": "killed", "project_slug": project_slug})


@app.get("/projects/{project_slug}/env")
async def env_list(project_slug: str, auth: AuthContext = Depends(require_auth)) -> JSONResponse:
    try:
        project = await store.ensure_project_owner(auth.account_id, project_slug)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    items = [{"key": k, "updated_at": None} for k in sorted((await store.list_env(project_slug)).keys())]
    return JSONResponse({"project_slug": project.slug, "project_name": project.project_name, "variables": items})


@app.put("/projects/{project_slug}/env/{key}")
async def env_set(
    project_slug: str,
    key: str,
    payload: EnvSetPayload,
    auth: AuthContext = Depends(require_auth),
) -> JSONResponse:
    try:
        await store.ensure_project_owner(auth.account_id, project_slug)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    await store.set_env(project_slug, key, payload.value)
    return JSONResponse({"status": "ok"})


@app.delete("/projects/{project_slug}/env/{key}")
async def env_unset(project_slug: str, key: str, auth: AuthContext = Depends(require_auth)) -> JSONResponse:
    try:
        await store.ensure_project_owner(auth.account_id, project_slug)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    await store.unset_env(project_slug, key)
    return JSONResponse({"status": "ok"})


@app.get("/projects/{project_slug}/deps")
async def deps_list(project_slug: str, auth: AuthContext = Depends(require_auth)) -> JSONResponse:
    try:
        project = await store.ensure_project_owner(auth.account_id, project_slug)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    items = [{"name": dep, "requirement": dep, "updated_at": None} for dep in await store.list_deps(project_slug)]
    return JSONResponse({"project_slug": project.slug, "project_name": project.project_name, "dependencies": items})


@app.put("/projects/{project_slug}/deps/{name}")
async def deps_set(
    project_slug: str,
    name: str,
    payload: DepSetPayload,
    auth: AuthContext = Depends(require_auth),
) -> JSONResponse:
    _ = payload
    try:
        await store.ensure_project_owner(auth.account_id, project_slug)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    await store.set_dep(project_slug, name)
    return JSONResponse({"status": "ok"})


@app.delete("/projects/{project_slug}/deps/{name}")
async def deps_unset(project_slug: str, name: str, auth: AuthContext = Depends(require_auth)) -> JSONResponse:
    try:
        await store.ensure_project_owner(auth.account_id, project_slug)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    await store.unset_dep(project_slug, name)
    return JSONResponse({"status": "ok"})


@app.get("/projects/{project_slug}/logs")
async def logs(project_slug: str, auth: AuthContext = Depends(require_auth)) -> JSONResponse:
    try:
        await store.ensure_project_owner(auth.account_id, project_slug)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    raise HTTPException(status_code=501, detail="log retrieval is not yet implemented")


@app.post("/feedback")
async def feedback(_: Request) -> JSONResponse:
    raise HTTPException(status_code=501, detail="feedback storage is not yet implemented")
