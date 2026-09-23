#!/usr/bin/env bash
# WorkBuddy-Daily 一键部署器（macOS / Linux）
set -euo pipefail
cd "$(dirname "$0")"

PY=""
if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "  [x] 没找到 Python 3，请先安装："
  echo "        macOS : brew install python"
  echo "        Debian: sudo apt install python3"
  exit 1
fi

exec "$PY" ./deploy.py "$@"
