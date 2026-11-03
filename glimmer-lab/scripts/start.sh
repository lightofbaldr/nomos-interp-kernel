#!/bin/sh
# Pick the installed, pinned runtime without sourcing a shell profile or installing anything.
set -eu
app_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
IFS= read -r supported_node < "$app_root/.nvmrc"
if [ -n "${GLIMMER_NODE_BIN:-}" ]; then
    launch_node=$GLIMMER_NODE_BIN
else
    installed_node="${NVM_DIR:-$HOME/.nvm}/versions/node/v$supported_node/bin/node"
    if [ -x "$installed_node" ]; then
        launch_node=$installed_node
    else
        launch_node=$(command -v node) || { printf '%s\n' 'Supported Node is not installed/configured.' >&2; exit 3; }
    fi
fi
case "$launch_node" in /*) ;; *) printf '%s\n' 'GLIMMER_NODE_BIN must be an absolute executable path.' >&2; exit 3;; esac
test -x "$launch_node" || { printf '%s\n' 'Configured Node executable is unavailable.' >&2; exit 3; }
# Child programs resolve this same Node. start.mjs validates actual versions before the adapter import.
PATH="$(dirname -- "$launch_node"):$PATH"
export PATH
exec "$launch_node" "$app_root/scripts/start.mjs" "$@"
