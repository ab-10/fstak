#!/usr/bin/env bash
set -euo pipefail

mkdir -p /run/fstak
PROJECT="${FSTAK_GCP_PROJECT_ID:-spawn-hq}"

SECRETS=(
  spx-database-url:FSTAK_DATABASE_URL
  spx-caddy-cloudflare-token:FSTAK_CADDY_CLOUDFLARE_TOKEN
  spx-secret-encryption-key:FSTAK_SECRET_ENCRYPTION_KEY
  spx-github-client-id:SPX_GITHUB_CLIENT_ID
)

: > /run/fstak/secrets.env
chmod 600 /run/fstak/secrets.env

for entry in "${SECRETS[@]}"; do
  secret="${entry%%:*}"
  var="${entry##*:}"
  val=$(gcloud secrets versions access latest --secret="$secret" --project="$PROJECT" 2>/dev/null) || continue
  printf '%s=%s\n' "$var" "$val" >> /run/fstak/secrets.env
done
