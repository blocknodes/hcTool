#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export HC_HOST="${HC_HOST:-0.0.0.0}"
export HC_PORT="${HC_PORT:-8084}"
exec uvicorn app.main:app --host "$HC_HOST" --port "$HC_PORT"
