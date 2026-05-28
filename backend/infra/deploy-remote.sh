#!/usr/bin/env bash
set -euo pipefail

API_HOSTNAME=""
DOMAIN_SUFFIX=""
GCS_BUCKET_NAME=""

for arg in "$@"; do
  case "$arg" in
    --api-hostname=*) API_HOSTNAME="${arg#*=}" ;;
    --domain-suffix=*) DOMAIN_SUFFIX="${arg#*=}" ;;
    --gcs-bucket-name=*) GCS_BUCKET_NAME="${arg#*=}" ;;
    *)
      echo "unknown arg: $arg" >&2
      exit 2
      ;;
  esac
done

if [ -z "$API_HOSTNAME" ] || [ -z "$DOMAIN_SUFFIX" ] || [ -z "$GCS_BUCKET_NAME" ]; then
  echo "required args: --api-hostname= --domain-suffix= --gcs-bucket-name=" >&2
  exit 2
fi

BACKEND=/opt/fstak/backend
INFRA=$BACKEND/infra

echo "==> Installing runtime deps"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y >/dev/null
apt-get install -y curl jq ca-certificates unzip >/dev/null

if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
fi

if ! command -v bun >/dev/null 2>&1; then
  curl -fsSL https://bun.sh/install | bash >/dev/null
fi

if [ -x /root/.bun/bin/bun ] && [ ! -x /usr/local/bin/bun ]; then
  ln -s /root/.bun/bin/bun /usr/local/bin/bun
fi

if ! command -v caddy >/dev/null 2>&1; then
  apt-get install -y debian-keyring debian-archive-keyring apt-transport-https >/dev/null
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  apt-get update -y >/dev/null
  apt-get install -y caddy >/dev/null
fi

echo "==> Syncing fstak service units"
cp "$INFRA/fstak-control.service" /etc/systemd/system/fstak-control.service
mkdir -p /etc/systemd/system/fstak-control.service.d
cat > /etc/systemd/system/fstak-control.service.d/hostnames.conf <<EOF
[Service]
Environment=FSTAK_API_HOSTNAME=$API_HOSTNAME
Environment=FSTAK_DOMAIN_SUFFIX=$DOMAIN_SUFFIX
Environment=FSTAK_GCS_BUCKET_NAME=$GCS_BUCKET_NAME
Environment=FSTAK_CADDY_ADMIN_URL=http://127.0.0.1:2019
EOF

echo "==> Python deps"
cd "$BACKEND"
PATH="$HOME/.local/bin:$PATH" uv sync --frozen

echo "==> Restarting services"
systemctl daemon-reload
systemctl enable caddy fstak-control.service >/dev/null
systemctl restart caddy

echo "==> Writing base Caddy config"
bash "$INFRA/configure-caddy.sh" "$API_HOSTNAME" "$DOMAIN_SUFFIX"

systemctl restart fstak-control.service

echo "==> Health check"
for _ in $(seq 1 20); do
  if curl -fsS http://127.0.0.1:9000/health >/dev/null; then
    echo "ok"
    exit 0
  fi
  sleep 1
done

echo "fstak health check failed" >&2
journalctl -u fstak-control -n 100 --no-pager >&2
exit 1
