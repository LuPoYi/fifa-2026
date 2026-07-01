#!/usr/bin/env bash
# 快速啟動 SX Bet 賠率分析工具(免記 python3)
# 用法:  ./start.sh
set -e
cd "$(dirname "$0")"

# 找可用的 Python(python3 優先)
PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then
  echo "找不到 Python,請先安裝 python3。" >&2
  exit 1
fi

exec "$PY" main.py "$@"
