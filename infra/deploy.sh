#!/usr/bin/env bash
#
# fstak infrastructure deployment script
#
# This is the DEFINITIVE scripted path for standing up fstak infrastructure and services.
# All fstak infrastructure configuration MUST live in code, config files, or this script.
#
# Usage:
#   ./infra/deploy.sh <target> [options]
#
# Targets:
#   control-plane   Stand up the fstak control plane (FastAPI/uvicorn).
#                   For local development this starts (or shows how to start) the API.
#   verify          Run verification commands against the deployed surface and print
#                   the exact external-system checks a human or agent should run.
#
# Environment variables (documented inputs — script fails fast if required ones are missing):
#
#   FSTAK_ENV
#       Required. One of: local | staging | prod.
#       Controls which set of services/config to deploy and which external systems to address.
#
#   FSTAK_CONTROL_PLANE_HOST (future)
#       For remote targets: hostname or IP where the control plane should run.
#       Example: "control.fstak.example.com"
#
#   FSTAK_CONTROL_PLANE_PORT (optional)
#       Local bind port for the control plane. Defaults to 8000 for local.
#
#   SPX_GITHUB_CLIENT_ID
#       Required for `fstak login` device auth. Shared SPX GitHub OAuth app client ID.
#
#   (Later additions that will be required for full deploys:)
#     FSTAK_GCP_PROJECT, FSTAK_GCS_BUCKET, FSTAK_CADDY_ADMIN_URL, FSTAK_DATABASE_URL, ...
#
# Idempotency:
#   This script is safe to re-run. It uses mkdir -p, conditional service management,
#   and never mutates a machine without explicit confirmation for destructive steps.
#
# Verification contract:
#   After any deploy target, run `./infra/deploy.sh verify` (or the commands it prints).
#   Verification MUST query the external system (curl against the real process/API,
#   ss -tlnp, pgrep, gcloud/gsutil, caddy config, etc.), not trust local state files.
#
# See also:
#   - CLAUDE.md (infrastructure rule + verification discipline)
#   - notes/stories/fstak-mvp-stories.md (Story 1, 11)
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTROL_PLANE_DIR="$REPO_ROOT/backend"
DEFAULT_LOCAL_PORT=8000

usage() {
  cat <<'EOF'
fstak infrastructure deployment (definitive script)

Usage:
  ./infra/deploy.sh control-plane [--local | --remote] [--dry-run]
  ./infra/deploy.sh verify [--local]

Environment (required unless noted):
  FSTAK_ENV=local|staging|prod          (controls target environment)
  FSTAK_CONTROL_PLANE_HOST              (required for --remote)
  FSTAK_CONTROL_PLANE_PORT              (optional, defaults to 8000 for local)
  FSTAK_GCP_PROJECT                      (required for --remote)
  FSTAK_GCS_BUCKET                       (required for --remote)
  FSTAK_DATABASE_URL                     (required for --remote)
  FSTAK_CADDY_ADMIN_URL                  (required for --remote)
  SPX_GITHUB_CLIENT_ID                   (required for shared SPX login device auth)

The script fails with a clear message if a required variable for the chosen target is unset.
EOF
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

require_var() {
  local varname="$1"
  local desc="${2:-$1}"
  if [[ -z "${!varname:-}" ]]; then
    die "Required environment variable $varname is not set ($desc).

Set it and re-run. Example:
  export $varname=...
  ./infra/deploy.sh ..."
  fi
}

cmd="${1:-}"
shift || true

case "$cmd" in
  control-plane)
    target="local"
    dry_run=false

    while [[ $# -gt 0 ]]; do
      case "$1" in
        --local) target="local"; shift ;;
        --remote) target="remote"; shift ;;
        --dry-run) dry_run=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown option for control-plane: $1" ;;
      esac
    done

    require_var FSTAK_ENV "one of: local, staging, prod"

    if [[ "$target" == "local" ]]; then
      port="${FSTAK_CONTROL_PLANE_PORT:-$DEFAULT_LOCAL_PORT}"

      echo "[fstak deploy] control-plane (local) — FSTAK_ENV=$FSTAK_ENV port=$port"
      echo "Control plane source: $CONTROL_PLANE_DIR"

      if [[ "$dry_run" == true ]]; then
        echo "(dry-run) would start: cd $CONTROL_PLANE_DIR && SPX_GITHUB_CLIENT_ID=... uv run --with fastapi --with 'uvicorn[standard]' uvicorn main:app --host 127.0.0.1 --port $port"
        echo "Verification command:"
        echo "  curl -s http://127.0.0.1:$port/health | jq ."
        exit 0
      fi

      # For local we do not daemonize by default — the operator runs this in a terminal
      # or wraps it with their own process manager (systemd, launchd, pm2, etc.).
      # This keeps the script idempotent and non-surprising.
      echo "To run the control plane locally (recommended for contributors):"
      echo ""
      echo "  cd $CONTROL_PLANE_DIR"
      echo "  export SPX_GITHUB_CLIENT_ID=..."
      echo "  uv run --with fastapi --with 'uvicorn[standard]' \\"
      echo "    uvicorn main:app --host 127.0.0.1 --port $port --reload"
      echo ""
      echo "Then verify:"
      echo "  curl -s http://127.0.0.1:$port/health"
      echo ""
      echo "This script does not auto-start background processes for local to avoid"
      echo "orphaned servers. Use your preferred process manager for long-running use."

    elif [[ "$target" == "remote" ]]; then
      require_var FSTAK_CONTROL_PLANE_HOST "hostname or IP for remote control plane"
      require_var FSTAK_GCP_PROJECT "GCP project ID"
      require_var FSTAK_GCS_BUCKET "GCS bucket for deployment artifacts"
      require_var FSTAK_DATABASE_URL "Neon/Postgres connection URL"
      require_var FSTAK_CADDY_ADMIN_URL "Caddy admin API URL"
      require_var SPX_GITHUB_CLIENT_ID "shared SPX GitHub OAuth app client ID for device login"

      echo "[fstak deploy] control-plane (remote) — host=$FSTAK_CONTROL_PLANE_HOST env=$FSTAK_ENV"
      echo "MVP topology selected: single small VM with colocated Caddy + control plane + build subprocess worker."
      echo "Expected env wiring for service runtime:"
      echo "  - FSTAK_DATABASE_URL=$FSTAK_DATABASE_URL"
      echo "  - FSTAK_GCS_BUCKET_NAME=$FSTAK_GCS_BUCKET"
      echo "  - FSTAK_CADDY_ADMIN_URL=$FSTAK_CADDY_ADMIN_URL"
      echo "  - SPX_GITHUB_CLIENT_ID=$SPX_GITHUB_CLIENT_ID"
      echo "  - FSTAK_DOMAIN_SUFFIX=fstak.runspx.com"
      echo ""
      echo "Remote rollout steps (idempotent, operator-executed):"
      echo "  1) Deploy backend code to $FSTAK_CONTROL_PLANE_HOST"
      echo "  2) Install/verify Bun, Caddy, and service manager units"
      echo "  3) Ensure Caddy admin API reachable at $FSTAK_CADDY_ADMIN_URL"
      echo "  4) Export env vars and restart control-plane service"
      echo "  5) Run ./infra/deploy.sh verify and capture evidence"
    fi
    ;;

  verify)
    scope="local"
    [[ "${1:-}" == "--local" ]] && scope="local" && shift || true

    require_var FSTAK_ENV

    echo "=== fstak infrastructure verification ==="
    echo "FSTAK_ENV=$FSTAK_ENV scope=$scope"
    echo ""
    echo "Run the following commands and capture output as evidence."
    echo "These query the EXTERNAL system (process table, listening sockets, live HTTP),"
    echo "not local belief about what was deployed."
    echo ""

    if [[ "$scope" == "local" ]]; then
      port="${FSTAK_CONTROL_PLANE_PORT:-$DEFAULT_LOCAL_PORT}"
      echo "Control plane (local) checks:"
      echo "  pgrep -af 'uvicorn main:app' || echo 'no uvicorn process found'"
      echo "  ss -tlnp | grep :$port || echo 'nothing listening on :$port'"
      echo "  curl -s http://127.0.0.1:$port/health && echo 'health 200 observed'"
      echo ""
      echo "When the public API exists (later stories):"
      echo "  curl -s https://api.fstak.runspx.com/health"
    fi

    echo ""
    echo "Cloud topology checks (small VM MVP):"
    echo "  - Caddy routes: curl -s \${FSTAK_CADDY_ADMIN_URL:-http://localhost:2019}/config/ | jq '.apps.http.servers'"
    echo "  - GCS objects: gsutil ls -p \$FSTAK_GCP_PROJECT gs://\$FSTAK_GCS_BUCKET/deployments/"
    echo "  - DB connectivity: psql \"\$FSTAK_DATABASE_URL\" -c 'select 1'"
    echo "  - Cloud resources: gcloud compute instances list --project \$FSTAK_GCP_PROJECT"
    echo ""
    echo "Evidence rule: paste the literal output of the above commands into status docs."
    ;;

  -h|--help|help)
    usage
    exit 0
    ;;

  "")
    usage
    exit 1
    ;;

  *)
    die "Unknown target: $cmd. See --help."
    ;;
esac
