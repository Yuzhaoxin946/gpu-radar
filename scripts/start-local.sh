#!/usr/bin/env bash
set -euo pipefail
radar_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ! -x "$radar_root/.venv/bin/python" ]]; then
    "$radar_root/scripts/setup-local.sh"
fi
mkdir -p "$radar_root/.local"
export DATABASE_URL="${DATABASE_URL:-sqlite:///$radar_root/.local/radar.db}"
export REGISTRATION_ENABLED="${REGISTRATION_ENABLED:-true}"
cd "$radar_root/server"
exec "$radar_root/.venv/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port "${RADAR_PORT:-8000}"

