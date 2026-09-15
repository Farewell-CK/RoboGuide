#!/usr/bin/env bash
# Shared helpers for C0-C smoke runs. Sourced by run-c0c1.sh / run-c0c2.sh / run-c0c3.sh.
# Every helper is test-only orchestration around unmodified production binaries.
set -euo pipefail

C0C_DIR="$(cd "$(dirname "${BASH_SOURCE[1]}")/.." && pwd)"
REPO="$(cd "$C0C_DIR/../../.." && pwd)"
SERVER_BIN="$REPO/target/debug/integration-server"
NODE_BIN="$REPO/target/debug/roboguide-node"
mkdir -p "$C0C_DIR/logs" "$C0C_DIR/responses" "$C0C_DIR/events"
PIDS=()

cleanup() {
    for pid in "${PIDS[@]:-}"; do
        kill "$pid" 2>/dev/null || true
    done
}
trap cleanup EXIT

record_head() {
    # Persist the exact commit and timestamp provenance for one run.
    local out="$1"
    {
        echo "git_sha=$(git -C "$REPO" rev-parse HEAD)"
        echo "git_branch=$(git -C "$REPO" rev-parse --abbrev-ref HEAD)"
        echo "started_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    } > "$out"
}

wait_http_ok() {
    # Poll one URL until it returns HTTP 200 within a bounded budget.
    local url="$1" budget="${2:-30}"
    for _ in $(seq 1 "$budget"); do
        if curl -sf -o /dev/null "$url"; then return 0; fi
        sleep 1
    done
    echo "timeout waiting for $url" >&2
    return 1
}

wait_node_in_inventory() {
    # Poll /v1/inventory until one node_id appears.
    local base="$1" node_id="$2" budget="${3:-30}"
    for _ in $(seq 1 "$budget"); do
        if curl -sf "$base/v1/inventory" | grep -q "\"$node_id\""; then return 0; fi
        sleep 1
    done
    echo "timeout waiting for node $node_id in inventory" >&2
    return 1
}

wait_task_status_not() {
    # Poll GET /v1/missions/{id} until no task remains in the given transient status.
    local base="$1" mission_id="$2" transient="$3" budget="${4:-90}" out="$5"
    for i in $(seq 1 "$budget"); do
        curl -sf "$base/v1/missions/$mission_id" -o "$out" || true
        if python3 - "$out" "$transient" <<'PYEOF'
import json, sys
doc = json.load(open(sys.argv[1]))
transient = sys.argv[2]
tasks = doc.get("tasks", [])
sys.exit(0 if tasks and all(t["status"] != transient for t in tasks) else 1)
PYEOF
        then
            echo "settled after ${i}s" >&2
            return 0
        fi
        sleep 1
    done
    echo "timeout waiting for tasks to leave status $transient" >&2
    return 1
}
