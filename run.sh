#!/usr/bin/env bash
set -euo pipefail

OPTIONS_FILE=/data/options.json
child_pid=""

stop_child() {
    echo "Received stop signal; shutting down."
    if [ -n "$child_pid" ]; then
        kill "$child_pid" 2>/dev/null || true
    fi
    exit 0
}

run_interruptible() {
    "$@" &
    child_pid=$!
    wait "$child_pid"
    child_pid=""
}

trap stop_child INT TERM

if [ ! -f "$OPTIONS_FILE" ]; then
    echo "ERROR: $OPTIONS_FILE not found. Are you running outside Home Assistant?"
    echo "Run the base container directly for command-line use:"
    echo "  podman run --rm --env-file .env akahu-to-budget"
    exit 1
fi

export AKAHU_TO_BUDGET_OPTIONS_FILE="$OPTIONS_FILE"

MAPPING_FILE=$(jq -r '.mapping_file // "/config/akahu_budget_mapping.json"' "$OPTIONS_FILE")
SYNC_INTERVAL=$(jq -r '.sync_interval // 86400' "$OPTIONS_FILE")

if [ ! -f "$MAPPING_FILE" ]; then
    echo "ERROR: Mapping file not found: $MAPPING_FILE"
    echo "Create it with akahu_budget_mapping.py outside HAOS, then place it at the configured path."
    exit 1
fi

echo "Using Home Assistant options from $OPTIONS_FILE"
echo "Using mapping file $MAPPING_FILE"

echo "Sync interval: ${SYNC_INTERVAL}s"
echo "Starting sync loop..."

while true; do
    echo "=== Sync started at $(date -u) ==="
    run_interruptible python /app/sync_cli.py || echo "Sync failed; retrying in ${SYNC_INTERVAL}s"
    echo "=== Sync finished; sleeping ${SYNC_INTERVAL}s ==="
    run_interruptible sleep "$SYNC_INTERVAL"
done
