#!/bin/bash
cd "$(dirname "$0")"
set -a; [ -f .env ] && . ./.env; set +a
exec .venv/bin/uvicorn vera.app:app --host 0.0.0.0 --port ${PORT:-8080}
