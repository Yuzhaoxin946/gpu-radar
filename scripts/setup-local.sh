#!/usr/bin/env bash
set -euo pipefail
radar_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
radar_python="${PYTHON_BIN:-python3}"
"$radar_python" -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10+ is required"'
if [[ ! -x "$radar_root/.venv/bin/python" ]]; then
    "$radar_python" -m venv "$radar_root/.venv"
fi
"$radar_root/.venv/bin/python" -m pip install -r "$radar_root/server/requirements.txt" -r "$radar_root/server/requirements-dev.txt" "$radar_root/agent"
printf '%s\n' '依赖安装完成。运行 ./scripts/start-local.sh 启动本地服务端。'

