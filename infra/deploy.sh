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
#   (Later additions that will be required for full deploys:)
#     FSTAK_GCP_PROJECT, FSTAK_GCS_BUCKET, FSTAK_CADDY_HOST, FSTAK_BUILD_WORKER_IMAGE, ...
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
        echo "(dry-run) would start: cd $CONTROL_PLANE_DIR && uv run --with fastapi --with 'uvicorn[standard]' uvicorn main:app --host 127.0.0.1 --port $port"
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

      echo "[fstak deploy] control-plane (remote) — host=$FSTAK_CONTROL_PLANE_HOST env=$FSTAK_ENV"
      echo "NOTE: Remote deployment is a stub in Story 1/11. Real implementation will:"
      echo "  - rsync or image the backend/ tree"
      echo "  - manage a systemd unit or container on the host"
      echo "  - wire secrets via the platform secret store (never committed)"
      echo ""
      echo "For now this target only validates inputs and prints the intended steps."
      echo "Idempotent re-run is supported (no mutations performed yet)."
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
    echo "Future surfaces (documented for completeness; not yet applicable):"
    echo "  - Build workers: pgrep on worker hosts, queue depth via admin API"
    echo "  - GCS objects: gsutil ls -p \$FSTAK_GCP_PROJECT gs://\$FSTAK_GCS_BUCKET/fstak/..."
    echo "  - Caddy routes: curl -s http://localhost:2019/config/ | jq '.apps.http.servers'"
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
