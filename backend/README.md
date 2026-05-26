# fstak backend

**Status: Implemented for local/MVP development (verified 2026-05)**

Control plane API for fstak static SPA deployments.

See `notes/stories/fstak-mvp-stories.md` for the current gap analysis, Story status (passed/failed/in_progress/planned), and prioritized outstanding work (remote workers, GCS storage, Caddy routing, production auth, real logs, etc.).

## Current Implementation (observed via live process + TestClient)

All endpoints below are implemented and exercised by the CLI (`fstak login`, `run`, `ps`, `kill`, `env *`, `add`, `logs`). They run against an in-memory store (`InMemoryStore`) and a dev auth manager.

- `GET /health` — liveness (no auth)
- `POST /auth/device` — start device flow (returns user_code, verification_uri, poll_token, interval, expires_in)
- `POST /auth/token` — poll for device token (dev mode auto-approves on first poll)
- `POST /auth/code` — redeem a bootstrap code for an immediate token (used by `fstak login --code`)
- `GET /auth/whoami` — return the validated identity for the bearer token (`account_id`, `username`). Auth required. (added after initial Story 10 verification; small extension of the auth surface)
- `POST /run` (multipart: `code` (tar.gz), `project_name`, optional `project_slug`) — upload + build + deploy. Returns stable `url`, `project_slug`, `deployment_id`, `build_strategy`, and `timings`. Auth required.
- `GET /projects` — list projects for the authenticated account (stable slugs + URLs)
- `GET /projects/{project_slug}` — project detail (slug, project_name, url, active_deployment_id, updated_at)
- `GET /projects/{project_slug}/deployments` — deployment history for the project (id, status, timings, error, created_at; newest first)
- `POST /projects/{project_slug}/kill` — deactivate the active deployment for the project
- `GET /projects/{project_slug}/env` — list env keys (values never returned)
- `PUT /projects/{project_slug}/env/{key}` — set or update an env var (body: `{"value": "..."}`)
- `DELETE /projects/{project_slug}/env/{key}` — unset an env var
- `GET /projects/{project_slug}/deps` — list dependencies (names only)
- `PUT /projects/{project_slug}/deps/{name}` — add/upsert a dependency (body: `{"requirement": "..."}` — currently requirement ignored, name is the key)
- `DELETE /projects/{project_slug}/deps/{name}` — remove a dependency
- `GET /projects/{project_slug}/logs` — **stub**: always returns `[]`. Real log capture is future work.
- `POST /feedback` — accept feedback (auth optional)

All project-scoped routes require `Authorization: Bearer <token>` and enforce ownership (403 for wrong account, 404 for unknown slug).

## Run locally

```bash
cd backend
uv run uvicorn main:app --reload
```

Default URL: `http://127.0.0.1:8000`

Health check:
```bash
curl http://127.0.0.1:8000/health
# {"status":"ok"}
```

## Auth (dev mode)

- Device flow (`fstak login`): `POST /auth/device` → poll `/auth/token`. The manager auto-approves the first poll and issues a long-lived token. `verification_uri` is a placeholder (`https://github.com/login/device`).
- Code flow (`fstak login --code CODE`): any non-empty code works. Username derived from the code for determinism in tests.
- Tokens are opaque strings (username.random). No real JWTs or revocation yet.
- `require_auth` (used by all protected routes) returns 401 with `{"detail": "missing bearer token"}` or `{"detail": "invalid or expired token"}`.

Production will replace this with the shared SPX/GitHub device + token system.

## Persistence and build

- Everything (projects, deployments, env, deps) lives in `InMemoryStore` (process memory, lost on restart).
- `/run`:
  - Extracts the uploaded tar.gz into an isolated temp workspace.
  - If `bun` is on PATH: `bun install --frozen-lockfile` + `bun run build`.
  - Fallback (no bun): copies `index.html` from the archive root into `dist/index.html`.
  - Materializes the `dist/` output to a local temp dir under `projects/{slug}/{deployment_id}` (future: GCS).
  - Records `build_seconds`, `upload_seconds`, `route_update_seconds`, status, and any error on the Deployment row.
- Asset serving and host routing (Caddy + `*.fstak.runspx.com`) are not yet implemented.

## Error responses (consistent schema)

All error paths use FastAPI `HTTPException(status_code=..., detail="...")`.

Observed shapes:
- 400: `{"detail": "<build or validation error>"}` (from /run)
- 401: `{"detail": "missing bearer token"}` or `{"detail": "invalid or expired token"}`
- 403: `{"detail": "not your project"}`
- 404: `{"detail": "project not found"}`

No ad-hoc error bodies in the current routes.

## CLI contract notes

- `fstak ps` prints the raw JSON array from `GET /projects`.
- `fstak run` reconciles local state using the `project_slug` + `url` returned by `POST /run`.
- `fstak kill <slug-or-url>` derives the slug and calls `POST /projects/{slug}/kill`.
- `fstak logs` calls `GET /projects/{slug}/logs` with `limit`, optional `severity`/`from`/`to`. Currently always gets `[]`.
- Env and dep commands use the obvious PUT/GET/DELETE shapes above and expect only keys (no secret values) on list responses.

## Planned / remaining work (see stories doc for priorities)

- Real log capture and queryable build/runtime logs (not the current `[]` stub).
- Remote/isolated build workers (currently runs on the control-plane host).
- GCS asset storage + manifest + re-deploy optimization (currently local temp dir).
- Caddy dynamic routing + `*.fstak.runspx.com` host mapping + SPA fallback semantics.
- Production auth integration (real OAuth, token lifecycle, revocation).
- Persistent store (Postgres or equivalent) instead of in-memory.
- Storage limits, upload-size enforcement, and proper error surface for them.
- Deployment history pruning / retention policy.

The single source of truth for what is "done" vs "planned" is `notes/stories/fstak-mvp-stories.md` (Story 10 and the Outstanding Work list).

## Verification

The implemented surface above was verified with hermetic TestClient calls against the live app object (health, auth flows, project CRUD, deployments list, kill, ownership enforcement, 401/403/404 error shapes, and consistent `{"detail": "..."}` responses). See the Story 10 status report in the stories document for the full evidence log.
