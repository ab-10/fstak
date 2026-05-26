"""FastAPI application for the fstak control plane.

This is the single entry point for the control plane HTTP surface.
Routes are organized by domain (auth, run/deploy, projects, etc.).

For local development:
    cd backend
    uvicorn main:app --reload

From repo root:
    PYTHONPATH=backend uvicorn backend.main:app --reload
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

from fastapi import FastAPI, Depends, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .auth import require_auth, AuthContext
from .auth_manager import manager
from .store import store  # in-memory store for MVP (Story 1-5)

app = FastAPI(
    title="fstak control plane",
    description="Control plane for fstak static SPA deployments.",
    version="0.1.0",
)


def _extract_archive(archive_bytes: bytes, workdir: Path) -> Path:
    src_dir = workdir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tf:
        tf.extractall(src_dir, filter="data")
    return src_dir


def _run_build(src_dir: Path, deps: list[str]) -> tuple[Path, str]:
    bun = shutil.which("bun")
    if bun is None:
        fallback_dist = src_dir / "dist"
        fallback_dist.mkdir(parents=True, exist_ok=True)
        index_src = src_dir / "index.html"
        if index_src.exists():
            shutil.copy2(index_src, fallback_dist / "index.html")
            return fallback_dist, "fallback-no-bun"
        raise RuntimeError("bun is not installed on builder host and index.html fallback is unavailable")

    env = os.environ.copy()
    env.setdefault("CI", "1")

    if deps:
        add = subprocess.run(
            [bun, "add", *deps],
            cwd=src_dir,
            env=env,
            capture_output=True,
            text=True,
        )
        if add.returncode != 0:
            raise RuntimeError(f"bun add failed: {add.stderr.strip() or add.stdout.strip()}")
        strategy = "bun+deps"
    else:
        install = subprocess.run(
            [bun, "install", "--frozen-lockfile"],
            cwd=src_dir,
            env=env,
            capture_output=True,
            text=True,
        )
        if install.returncode != 0:
            raise RuntimeError(f"bun install failed: {install.stderr.strip() or install.stdout.strip()}")
        strategy = "bun"

    build = subprocess.run(
        [bun, "run", "build"],
        cwd=src_dir,
        env=env,
        capture_output=True,
        text=True,
    )
    if build.returncode != 0:
        raise RuntimeError(f"bun build failed: {build.stderr.strip() or build.stdout.strip()}")

    dist = src_dir / "dist"
    if not dist.exists():
        raise RuntimeError("build completed but dist/ was not produced")
    return dist, strategy


def _materialize_assets(project_slug: str, deployment_id: str, dist_dir: Path) -> str:
    assets_root = Path(tempfile.gettempdir()) / "fstak-assets"
    prefix = f"projects/{project_slug}/{deployment_id}"
    target = assets_root / prefix
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(dist_dir, target)
    return prefix


@app.get("/health")
def health() -> JSONResponse:
    """Liveness/readiness check. Returns 200 when the process is up."""
    return JSONResponse({"status": "ok"})


# --- Auth endpoints (Story 2) ---

@app.post("/auth/device")
async def auth_device() -> dict:
    """Start a device authorization flow (matches CLI login.rs expectations)."""
    return manager.create_device_session()


@app.post("/auth/token")
async def auth_token(request: Request) -> dict:
    """Poll for a device token (or receive the final token once approved)."""
    body = await request.json()
    poll_token = body.get("poll_token", "")
    return manager.poll_device_token(poll_token)


@app.post("/auth/code")
async def auth_code(request: Request) -> dict:
    """Redeem a registration / bootstrap code (used by `fstak login --code`)."""
    body = await request.json()
    code = body.get("code", "")
    return manager.redeem_code(code)


@app.get("/auth/whoami")
def auth_whoami(auth: AuthContext = Depends(require_auth)) -> JSONResponse:
    """Return account identity for a validated bearer token."""
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
    try:
        project = store.upsert_project(auth.account_id, project_name, project_slug)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    deployment = store.create_deployment(project.slug, asset_prefix=f"projects/{project.slug}/pending")
    build_seconds = 0.0
    upload_seconds = 0.0
    route_update_seconds = 0.0
    strategy = "unknown"

    with tempfile.TemporaryDirectory(prefix="fstak-build-") as tmpdir:
        workdir = Path(tmpdir)
        try:
            archive_bytes = await code.read()

            start = time.perf_counter()
            src_dir = _extract_archive(archive_bytes, workdir)
            dist_dir, strategy = _run_build(src_dir, store.list_deps(project.slug))
            build_seconds = time.perf_counter() - start

            start = time.perf_counter()
            asset_prefix = _materialize_assets(project.slug, deployment.id, dist_dir)
            upload_seconds = time.perf_counter() - start

            start = time.perf_counter()
            store.mark_deployment(
                deployment.id,
                status="ready",
                build_seconds=build_seconds,
                upload_seconds=upload_seconds,
                route_update_seconds=0.001,
                error=None,
            )
            deployment.asset_prefix = asset_prefix
            route_update_seconds = time.perf_counter() - start
        except Exception as exc:  # noqa: BLE001
            store.mark_deployment(
                deployment.id,
                status="failed",
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
def list_projects(auth: AuthContext = Depends(require_auth)) -> JSONResponse:
    return JSONResponse(store.list_projects_for_account(auth.account_id))


@app.get("/projects/{project_slug}")
def project_details(project_slug: str, auth: AuthContext = Depends(require_auth)) -> JSONResponse:
    try:
        project = store.ensure_project_owner(auth.account_id, project_slug)
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
def list_deployments(project_slug: str, auth: AuthContext = Depends(require_auth)) -> JSONResponse:
    try:
        store.ensure_project_owner(auth.account_id, project_slug)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return JSONResponse(store.list_deployments_for_project(project_slug))


@app.post("/projects/{project_slug}/kill")
def kill_project(project_slug: str, auth: AuthContext = Depends(require_auth)) -> JSONResponse:
    try:
        store.ensure_project_owner(auth.account_id, project_slug)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    store.kill_project(project_slug)
    return JSONResponse({"status": "killed", "project_slug": project_slug})


@app.get("/projects/{project_slug}/env")
def env_list(project_slug: str, auth: AuthContext = Depends(require_auth)) -> JSONResponse:
    try:
        project = store.ensure_project_owner(auth.account_id, project_slug)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    items = [{"key": k, "updated_at": None} for k in sorted(store.list_env(project_slug).keys())]
    return JSONResponse({"project_slug": project.slug, "project_name": project.project_name, "variables": items})


@app.put("/projects/{project_slug}/env/{key}")
def env_set(
    project_slug: str,
    key: str,
    payload: EnvSetPayload,
    auth: AuthContext = Depends(require_auth),
) -> JSONResponse:
    try:
        store.ensure_project_owner(auth.account_id, project_slug)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    store.set_env(project_slug, key, payload.value)
    return JSONResponse({"status": "ok"})


@app.delete("/projects/{project_slug}/env/{key}")
def env_unset(project_slug: str, key: str, auth: AuthContext = Depends(require_auth)) -> JSONResponse:
    try:
        store.ensure_project_owner(auth.account_id, project_slug)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    store.unset_env(project_slug, key)
    return JSONResponse({"status": "ok"})


@app.get("/projects/{project_slug}/deps")
def deps_list(project_slug: str, auth: AuthContext = Depends(require_auth)) -> JSONResponse:
    try:
        project = store.ensure_project_owner(auth.account_id, project_slug)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    items = [{"name": dep, "requirement": dep, "updated_at": None} for dep in store.list_deps(project_slug)]
    return JSONResponse(
        {"project_slug": project.slug, "project_name": project.project_name, "dependencies": items}
    )


@app.put("/projects/{project_slug}/deps/{name}")
def deps_set(
    project_slug: str,
    name: str,
    payload: DepSetPayload,
    auth: AuthContext = Depends(require_auth),
) -> JSONResponse:
    _ = payload
    try:
        store.ensure_project_owner(auth.account_id, project_slug)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    store.set_dep(project_slug, name)
    return JSONResponse({"status": "ok"})


@app.delete("/projects/{project_slug}/deps/{name}")
def deps_unset(project_slug: str, name: str, auth: AuthContext = Depends(require_auth)) -> JSONResponse:
    try:
        store.ensure_project_owner(auth.account_id, project_slug)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    store.unset_dep(project_slug, name)
    return JSONResponse({"status": "ok"})


@app.get("/projects/{project_slug}/logs")
def logs(project_slug: str, auth: AuthContext = Depends(require_auth)) -> JSONResponse:
    try:
        store.ensure_project_owner(auth.account_id, project_slug)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return JSONResponse([])


@app.post("/feedback")
async def feedback(_: Request) -> JSONResponse:
    return JSONResponse({"status": "accepted"})


# --- Story 5+, 9+, 10+ routes are implemented above with in-memory defaults ---
#
# Example pattern (not yet implemented; added here so the skeleton is clear):
#
# from fastapi import APIRouter
#
# run_router = APIRouter(tags=["deploy"])
# projects_router = APIRouter(prefix="/projects", tags=["projects"])
#
# @run_router.post("/run")
# async def run_deploy(..., auth: AuthContext = Depends(require_auth)): ...
#
# app.include_router(run_router)
# app.include_router(projects_router)
#
# Auth routes are implemented above for Story 2.
