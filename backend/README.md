# fstak backend

**Status: Implemented for local/MVP development (verified 2026-05)**

Control plane API for fstak static SPA deployments.

See `notes/stories/fstak-mvp-stories.md` for story-by-story status. This README reflects the current small-VM MVP direction: Caddy + control plane + local Bun subprocess builds, GCS artifact upload, and deployment-revision semantics.

## Current Implementation (observed via live process + TestClient)

All endpoints below are implemented and exercised by the CLI (`fstak login`, `run`, `ps`, `kill`, `env *`, `add`, `logs`). They currently run with an in-memory store (`InMemoryStore`) and a GitHub OAuth-backed auth manager, with env/config hooks added for Neon/GCS/Caddy integration.

- `GET /health` — liveness (no auth)
- `POST /auth/device` — start device flow (returns user_code, verification_uri, poll_token, interval, expires_in)
- `POST /auth/token` — poll for device token from GitHub OAuth and return an fstak token once approved
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

## Auth

- Device flow (`fstak login`): `POST /auth/device` → GitHub device OAuth → poll `/auth/token`. Requires the shared SPX OAuth app env var `SPX_GITHUB_CLIENT_ID`; the backend returns 500 if it is not configured instead of silently falling back to a local identity.
- Code flow (`fstak login --code CODE`): any non-empty code works. Username derived from the code for determinism in tests.
- Tokens are opaque strings (username.random). No real JWTs or revocation yet.
- `require_auth` (used by all protected routes) returns 401 with `{"detail": "missing bearer token"}` or `{"detail": "invalid or expired token"}`.

Production will replace this with the shared SPX/GitHub device + token system.

## Persistence and build

- Project/deployment/env/dependency state is currently backed by `InMemoryStore`.
- `/run`:
  - Extracts the uploaded tar.gz into an isolated temp workspace.
  - If `bun` is on PATH: `bun install --frozen-lockfile` + `bun run build` (with project env injected).
  - Fallback (no bun): copies `index.html` from the archive root into `dist/index.html`.
  - Uploads build outputs to GCS when `FSTAK_GCS_BUCKET_NAME` is set.
  - Generates artifact metadata (source hash, artifact hash, manifest hash) and records timings.
  - Reconciles Caddy route for `<slug>.fstak.runspx.com` to `storage.googleapis.com` when `FSTAK_CADDY_ADMIN_URL` is set.
- Route reconciliation uses DB-truth semantics (`active_deployment_id` pointer), with in-memory fallback in this branch.

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
- Persisted database store (Neon/Postgres) replacing in-memory defaults.
- Production auth integration (real OAuth, token lifecycle, revocation).
- Persistent store (Postgres or equivalent) instead of in-memory.
- Storage limits, upload-size enforcement, and proper error surface for them.
- Deployment history pruning / retention policy.

The single source of truth for what is "done" vs "planned" is `notes/stories/fstak-mvp-stories.md` (Story 10 and the Outstanding Work list).

## Verification

The implemented surface above was verified with hermetic TestClient calls against the live app object (health, auth flows, project CRUD, deployments list, kill, ownership enforcement, 401/403/404 error shapes, and consistent `{"detail": "..."}` responses). See the Story 10 status report in the stories document for the full evidence log.
