#!/usr/bin/env bash
set -euo pipefail

API_HOSTNAME="${1:-}"
DOMAIN_SUFFIX="${2:-}"

if [ -z "$API_HOSTNAME" ] || [ -z "$DOMAIN_SUFFIX" ]; then
  echo "usage: configure-caddy.sh <api-hostname> <domain-suffix>" >&2
  exit 2
fi

cat > /tmp/fstak-caddy.json <<EOF
{
  "apps": {
    "http": {
      "servers": {
        "fstak": {
          "listen": [":443", ":80"],
          "routes": [
            {
              "@id": "fstak-api",
              "match": [{"host": ["$API_HOSTNAME"]}],
              "handle": [{"handler": "reverse_proxy", "upstreams": [{"dial": "127.0.0.1:9000"}]}]
            }
          ]
        }
      }
    }
  }
}
EOF

curl -fsS -X POST "http://127.0.0.1:2019/load" \
  -H 'Content-Type: application/json' \
  --data-binary @/tmp/fstak-caddy.json >/dev/null
