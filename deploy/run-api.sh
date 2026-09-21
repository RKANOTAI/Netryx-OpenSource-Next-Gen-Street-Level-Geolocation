#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
: "${NETRYX_ENV_FILE:?Set NETRYX_ENV_FILE to the private API environment file}"
exec "$ROOT/.venv/bin/python" -m uvicorn netryx_web.api:app \
  --host 127.0.0.1 --port 8000 --workers 1 --limit-concurrency 16 \
  --timeout-keep-alive 5 --env-file "$NETRYX_ENV_FILE"
