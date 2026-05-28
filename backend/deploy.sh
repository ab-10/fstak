#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

INSTANCE="${FSTAK_HOST_INSTANCE:-fstak-staging-vm}"
ZONE="${FSTAK_HOST_ZONE:-us-central1-a}"
PROJECT="${FSTAK_GCP_PROJECT_ID:-spawn-hq}"
API_HOSTNAME="${FSTAK_API_HOSTNAME:-api.fstak.runspx.com}"
DOMAIN_SUFFIX="${FSTAK_DOMAIN_SUFFIX:-fstak.runspx.com}"
GCS_BUCKET_NAME="${FSTAK_GCS_BUCKET_NAME:-fstak-static-runspx-com}"
STATIC_IP_NAME="${FSTAK_STATIC_IP_NAME:-fstak-staging-ip}"
REGION="${FSTAK_REGION:-${ZONE%-*}}"
SPX_API_URL="${FSTAK_SPX_API_URL:-https://api.runspx.com}"

if ! gcloud compute instances describe "$INSTANCE" --zone="$ZONE" --project="$PROJECT" >/dev/null 2>&1; then
  echo "creating VM $INSTANCE"
  gcloud compute instances create "$INSTANCE" \
    --project="$PROJECT" \
    --zone="$ZONE" \
    --machine-type=e2-standard-2 \
    --image-family=ubuntu-2204-lts \
    --image-project=ubuntu-os-cloud \
    --boot-disk-size=40GB \
    --tags=fstak-host
fi

if ! gcloud compute addresses describe "$STATIC_IP_NAME" --region="$REGION" --project="$PROJECT" >/dev/null 2>&1; then
  echo "creating static IP $STATIC_IP_NAME"
  gcloud compute addresses create "$STATIC_IP_NAME" --region="$REGION" --project="$PROJECT"
fi

STATIC_IP="$(gcloud compute addresses describe "$STATIC_IP_NAME" --region="$REGION" --project="$PROJECT" --format='value(address)')"
CURRENT_IP="$(gcloud compute instances describe "$INSTANCE" --zone="$ZONE" --project="$PROJECT" --format='value(networkInterfaces[0].accessConfigs[0].natIP)')"
if [ "$CURRENT_IP" != "$STATIC_IP" ]; then
  echo "attaching static IP $STATIC_IP to $INSTANCE"
  gcloud compute instances delete-access-config "$INSTANCE" --zone="$ZONE" --project="$PROJECT" --access-config-name="external-nat" >/dev/null 2>&1 || true
  gcloud compute instances add-access-config "$INSTANCE" \
    --zone="$ZONE" \
    --project="$PROJECT" \
    --access-config-name="external-nat" \
    --address="$STATIC_IP"
fi

if ! gcloud compute firewall-rules describe fstak-allow-web --project="$PROJECT" >/dev/null 2>&1; then
  gcloud compute firewall-rules create fstak-allow-web \
    --project="$PROJECT" \
    --allow=tcp:80,tcp:443 \
    --target-tags=fstak-host \
    --description="Allow web ingress for fstak"
fi

if ! gcloud storage buckets describe "gs://$GCS_BUCKET_NAME" --project="$PROJECT" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://$GCS_BUCKET_NAME" --project="$PROJECT" --location=us-central1 --no-uniform-bucket-level-access
fi
gcloud storage buckets update "gs://$GCS_BUCKET_NAME" --no-uniform-bucket-level-access --no-public-access-prevention >/dev/null

DB_URL="$(gcloud secrets versions access latest --secret=spx-database-url --project="$PROJECT")"
CF_TOKEN="$(gcloud secrets versions access latest --secret=spx-caddy-cloudflare-token --project="$PROJECT")"

ARCHIVE=""
SECRETS_FILE=$(mktemp -t fstak-secrets.XXXXXX)
trap 'rm -f "${ARCHIVE:-}" "${SECRETS_FILE:-}"' EXIT
cat > "$SECRETS_FILE" <<EOF
FSTAK_DATABASE_URL=$DB_URL
FSTAK_CADDY_CLOUDFLARE_TOKEN=$CF_TOKEN
EOF

ARCHIVE=$(mktemp -t fstak-backend.XXXXXX.tar.gz)

COPYFILE_DISABLE=1 tar czf "$ARCHIVE" \
  --exclude='./.venv' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.pytest_cache' \
  --exclude='.ruff_cache' \
  --exclude='._*' \
  -C "$SCRIPT_DIR" .

REMOTE_TGZ=/tmp/fstak-backend-deploy.tar.gz

gcloud compute scp "$ARCHIVE" "$INSTANCE:$REMOTE_TGZ" \
  --zone="$ZONE" --project="$PROJECT"
gcloud compute scp "$SECRETS_FILE" "$INSTANCE:/tmp/fstak-secrets.env" \
  --zone="$ZONE" --project="$PROJECT"

gcloud compute ssh "$INSTANCE" --zone="$ZONE" --project="$PROJECT" --command "
set -euo pipefail
sudo mkdir -p /opt/fstak/backend
sudo find /opt/fstak/backend -mindepth 1 -maxdepth 1 ! -name '.venv' -exec rm -rf {} +
sudo tar xzf '$REMOTE_TGZ' -C /opt/fstak/backend
rm -f '$REMOTE_TGZ'
sudo mkdir -p /etc/fstak
sudo mv /tmp/fstak-secrets.env /etc/fstak/secrets.env
sudo chmod 600 /etc/fstak/secrets.env
sudo bash /opt/fstak/backend/infra/deploy-remote.sh \
  --api-hostname='$API_HOSTNAME' \
  --domain-suffix='$DOMAIN_SUFFIX' \
  --gcs-bucket-name='$GCS_BUCKET_NAME' \
  --spx-api-url='$SPX_API_URL'
"

echo "deployed to $INSTANCE ($ZONE)"
