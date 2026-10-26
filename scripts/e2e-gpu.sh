#!/usr/bin/env bash
# Run only inside with_serve_down.sh AND a separate systemd user unit; see README.
# A new --work directory is mandatory. No prior run, socket or KV store is removed.
set -euo pipefail
AGENT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONDONTWRITEBYTECODE=1
exec python3 "$AGENT/scripts/e2e-launch.py" "$@"
