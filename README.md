# fstak

fast deployment of SPA for agents.

Does not require local `bun` to be installed.
Does not provide a dev environment.
(user doesn't need a local preview/dev env, because deploy is sufficiently fast)

## Quickstart (Contributors)

These commands have been verified to work on macOS.

### Prerequisites
- Rust toolchain (`rustup` / `cargo`)
- `uv` (recommended Python toolchain) or Python 3.12+ with `pip`
- `curl` and `jq` (optional but helpful for verification)

### Build & sanity-check the CLI

```bash
cd fstak
cargo check                    # fast compile check (no artifacts needed)
cargo build                    # debug build → target/debug/fstak
```

Run without installing:
```bash
cargo run -- --help
```

### Run the control plane locally (required for most CLI commands)

```bash
cd backend
uv run --with fastapi --with 'uvicorn[standard]' \
  uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

In another terminal:
```bash
curl -s http://127.0.0.1:8000/health | jq .
# {"status":"ok"}
```

Health check must return HTTP 200. The backend uses an in-memory store for MVP.

### Infrastructure & deployment

`infra/deploy.sh` is the **definitive** scripted path for fstak infrastructure (per CLAUDE.md).

```bash
# See what would happen (no mutations)
FSTAK_ENV=local ./infra/deploy.sh control-plane --dry-run

# See the exact external verification commands you must run
FSTAK_ENV=local ./infra/deploy.sh verify

# The script fails with a clear message when required variables are missing
FSTAK_ENV=local ./infra/deploy.sh control-plane --local
```

Required variables for the script are documented at the top of `infra/deploy.sh`.

### Full Story 1 verification (self-check)

After the above steps, you should be able to:
1. `cargo check` succeeds in `fstak/`
2. `curl http://127.0.0.1:8000/health` returns 200 from a live uvicorn process
3. `FSTAK_ENV=local ./infra/deploy.sh verify` prints concrete external-system checks

See `notes/stories/fstak-mvp-stories.md` for the complete Story 1 criteria.

## glossary

CRITICAL: use the vocabulary in this section when referring to things

Developer: customer of the fstak platform

Contributor: someone working on the fstak codebase

End user: downstream visitor of a deployed app

Deployment: a particular running version of a project served by fstak.

## cli

fstak add <pkg> -> aliases `bun add` (always on prod)

fstak run -> deploys the current project and returns a public URL.
Must run under 5s.

fstak new -> initializes a new React + Bun project with `.fstak/` project dir.
Fixed React + Bun template (no local bun or Node runtime required to run `fstak new`).

## stack

**cli:** rust

**developer's app:**
1. react frontend
2. static build artifacts (remote build output; current scaffold uses Bun)


## infrastructure

**traffic route:**

caddy
(internal lookup table, mapping fstak project to GCS object prefix)

-> 

GCS bucket stores static assets.

For each request to a project hostname:
1. match host to project in Caddy route table
2. map request path to object under that project's GCS prefix
3. if object exists, serve it
4. if object does not exist and request is a navigation path, serve `index.html` (SPA fallback)
5. if object does not exist and request is an asset path, return 404

**fstak run:**

1. sync local code to build server
2. run remote build (bun or equivalent) and upload artifacts
3. upload built static assets to GCS
4. update Caddy route mapping for the project hostname

## Domains

platform subdomains.
Users create CNAME records for their own domains.

Uses *.fstak.runspx.com subdomains
One subdomain per project. Each latest deployment updates the running version.


## Platform boundary

fstak runs in the same GCP project as SPX, but with independent fstak resources.

Shared surface:
- authentication database and login semantics (fstak login mirrors SPX auth)

Isolated surfaces:
- fstak control-plane services
- fstak build infrastructure
- fstak storage buckets/prefixes
- fstak Caddy route entries

## Borrowed decisions

1. Stable project URL semantics:
   each project gets one stable public URL; each new Deployment updates what that URL serves.

2. Local project identity state:
   each project stores gitignored local state in `.fstak/state.json` so commands can resolve project identity without extra flags.

3. Non-interactive CLI by default:
   commands are deterministic and script-friendly for Contributors and coding agents, with machine-parseable output where appropriate.

4. Static-first deployment type:
   static SPAs are treated as a first-class deployment path and served as files, not as long-running app processes.

5. Storage + edge routing split:
   build artifacts are stored in object storage, while edge routing serves requests directly and keeps the control plane out of the request path.

6. Project-scoped environment configuration:
   persisted environment values are stored per project and applied during remote build and deploy.

7. Tarball deploy protocol:
   the CLI uploads a tarball plus metadata in one request for simple, reliable deploy transport.

8. Archive hygiene defaults:
   uploads exclude local-only directories and state files to reduce transfer size and avoid leaking local artifacts.

9. Developer authentication model:
   CLI authentication is token-based with local credential persistence for non-interactive deploy flows.

10. Explicit static artifact limits:
    static deploys enforce clear upload limits and path-serving rules to keep behavior predictable and abuse-resistant.
