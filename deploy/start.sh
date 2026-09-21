#!/bin/sh
set -eu
NETRYX_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
export NETRYX_ROOT
export NETRYX_DEPLOY_DIR=${NETRYX_DEPLOY_DIR:-/opt/data/services/netryx}
export NETRYX_ENV_FILE=${NETRYX_ENV_FILE:-$NETRYX_DEPLOY_DIR/api.env}
export NETRYX_CLOUDFLARED=${NETRYX_CLOUDFLARED:-/opt/data/bin/cloudflared}
export NETRYX_TUNNEL_ENABLED=${NETRYX_TUNNEL_ENABLED:-false}
exec "$NETRYX_DEPLOY_DIR/supervisor-venv/bin/supervisord" -n -c "$NETRYX_ROOT/deploy/supervisord.conf"
