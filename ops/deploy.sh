#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")/.."
# Refuse the local-development defaults on a publicly reachable deployment.
python3 - <<'PY'
from pathlib import Path
values = dict(line.split('=', 1) for line in Path('.env').read_text().splitlines() if '=' in line and not line.startswith('#'))
def require(condition, message):
    if not condition:
        raise SystemExit(message)
require(values.get('ENVIRONMENT') == 'production', 'Set ENVIRONMENT=production')
require(values.get('SECURE_COOKIES') == 'true', 'Set SECURE_COOKIES=true')
require(values.get('PUBLIC_ORIGIN', '').startswith('https://'), 'Set a public HTTPS origin')
require(values.get('PUBLIC_ORIGIN') == 'https://' + values.get('SITE_HOST', ''), 'SITE_HOST must match PUBLIC_ORIGIN')
require(values.get('OPENAI_API_KEY') and values.get('PINECONE_API_KEY') and values.get('PINECONE_INDEX_HOST'), 'Configure AI credentials')
PY
docker compose --profile production config --quiet
docker compose --profile production up --build -d --wait --wait-timeout 300
