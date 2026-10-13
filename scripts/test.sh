#!/usr/bin/env bash
set -euo pipefail
radar_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
radar_python="${RADAR_PYTHON:-$radar_root/.venv/bin/python}"
export PYTHONPATH="$radar_root/server:$radar_root/agent/src${PYTHONPATH:+:$PYTHONPATH}"
"$radar_python" -m pytest "$radar_root/server/tests" "$radar_root/agent/tests" -q

