#!/usr/bin/env bash
# Run from any directory. Real-DB tests require a disposable database named synapse_test.
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$ROOT"

command -v python3 >/dev/null || { echo "Python 3.11+ is required." >&2; exit 1; }
command -v npm >/dev/null && command -v node >/dev/null || { echo "Node 22.12+ (22.x), 24.x or 26+/npm is required." >&2; exit 1; }
node -e 'const [major, minor] = process.versions.node.split(".").map(Number); if (!(major === 22 && minor >= 12 || major === 24 || major >= 26)) { console.error("Use Node 22.12+ (22.x), 24.x or 26+."); process.exit(1); }'
if [[ "${REQUIRE_INTEGRATION:-0}" == 1 && -z "${TEST_DATABASE_URL:-}" ]]; then
  echo "Set TEST_DATABASE_URL to a disposable PostgreSQL database named synapse_test." >&2
  exit 1
fi
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
PYTHON="$ROOT/.venv/bin/python"
"$PYTHON" -m pip install --disable-pip-version-check -r backend/requirements-dev.txt

if [[ -f init_synapse.sh ]]; then
  "$PYTHON" ops/sync_initializer.py --check
  bash -n init_synapse.sh
fi
bash -n ops/deploy.sh
bash -n ops/verify.sh
"$ROOT/.venv/bin/ruff" check --config backend/pyproject.toml backend ops
(cd backend && "$PYTHON" -m pytest -q)
(cd frontend && npm ci --ignore-scripts --no-audit --no-fund && npm test && npm run typecheck && NEXT_TELEMETRY_DISABLED=1 npm run build)
if [[ -f init_synapse.sh ]]; then
  "$PYTHON" ops/sync_initializer.py --check
fi
if command -v docker >/dev/null && [[ -f .env ]]; then
  docker compose config --quiet
else
  printf '%s\n' 'Docker Compose validation skipped: Docker or .env is unavailable.'
fi
printf '%s\n' 'Verification complete. Live AI/Google and container-runtime checks require a configured deployment.'
