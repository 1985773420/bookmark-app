#!/usr/bin/env bash
# 开发/宿主模式启动脚本：直接用 venv 跑，跳过 docker。
set -euo pipefail
cd "$(dirname "$0")"

export BM_DATA_DIR="${BM_DATA_DIR:-$PWD/data}"
export BM_BACKUP_HOUR="${BM_BACKUP_HOUR:-3}"

PORT="${PORT:-8094}"
exec .venv/bin/uvicorn app.main:app --host "${HOST:-127.0.0.1}" --port "$PORT" "$@"
