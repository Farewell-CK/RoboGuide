#!/usr/bin/env bash
# E2-S0: fixed MissionPlan through production RoboGuide into the COHERENT physical runner.
set -euo pipefail

SCENARIO="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$SCENARIO/../.." && pwd)"
EXP_ROOT="${ROBOGUIDE_E2_ROOT:-$(cd "$REPO/../.." && pwd)}"
RUN="${1:-$EXP_ROOT/results/roboguide-controlled/Merom_1_int_Task1/$(date +%Y%m%d-%H%M%S)}"
SERVER="$REPO/target/debug/integration-server"
NODE="$REPO/target/debug/roboguide-node"
PHYSICAL_RUNNER="${COHERENT_E2_RUNNER:-/workspace/exp2-coherent/scripts/run_official_task.sh}"
PIDS=()

cleanup() {
    for pid in "${PIDS[@]:-}"; do
        kill "$pid" 2>/dev/null || true
    done
}
trap cleanup EXIT

wait_http() {
    local url="$1" budget="$2"
    for _ in $(seq 1 "$budget"); do
        if curl -sf -o /dev/null "$url"; then return 0; fi
        sleep 1
    done
    echo "timeout waiting for $url" >&2
    return 1
}

wait_node() {
    local node_pid="$1"
    for _ in $(seq 1 120); do
        if ! kill -0 "$node_pid" 2>/dev/null; then
            echo "roboguide-node exited before registration" >&2
            return 1
        fi
        if curl -sf http://127.0.0.1:28060/v1/inventory \
            | grep -q '"coherent-team-e2-s0"'; then
            return 0
        fi
        sleep 1
    done
    echo "timeout waiting for COHERENT node registration" >&2
    return 1
}

wait_mission() {
    local output="$1" status
    for _ in $(seq 1 1400); do
        curl -sf http://127.0.0.1:28060/v1/missions/mission-e2-s0-coherent-merom \
            -o "$output" || true
        status=$(python3 - "$output" <<'PYEOF' || true
import json
import sys

try:
    print(json.load(open(sys.argv[1], encoding="utf-8"))["status"])
except (FileNotFoundError, KeyError, json.JSONDecodeError):
    pass
PYEOF
)
        case "$status" in
            Completed) return 0 ;;
            Failed|Cancelled)
                echo "Mission terminated unexpectedly: $status" >&2
                return 1 ;;
        esac
        sleep 1
    done
    echo "timeout waiting for E2-S0 Mission" >&2
    return 1
}

for port in 25060 28060 28090 28110; do
    if ss -tln | grep -q ":$port "; then
        echo "configured port is occupied: $port" >&2
        exit 1
    fi
done
if [[ ! -x "$SERVER" || ! -x "$NODE" ]]; then
    echo "build integration-server and roboguide-node before running E2-S0" >&2
    exit 1
fi
if ! docker inspect -f '{{.State.Running}}' coherent-sim | grep -qx true; then
    echo "coherent-sim is not running" >&2
    exit 1
fi
if [[ -e "$RUN" ]]; then
    echo "refusing to reuse run directory: $RUN" >&2
    exit 1
fi
mkdir -p "$RUN/artifacts" "$RUN/bridge-evidence"
sed "s|NODE_STATE_PLACEHOLDER|$RUN/node-state|" "$SCENARIO/node.toml" > "$RUN/node.toml"

PYTHONPATH="$REPO/integrations/coherent-local-eaios" \
python3 -u -m coherent_local_eaios \
    --port 28110 \
    --state-db "$RUN/coherent-bridge.sqlite3" \
    --evidence-dir "$RUN/bridge-evidence" \
    --results-root "$EXP_ROOT/results/baseline" \
    --runner "$PHYSICAL_RUNNER" \
    --timeout-s 900 \
    >"$RUN/coherent-bridge.log" 2>&1 &
PIDS+=($!)
wait_http http://127.0.0.1:28110/v1/health 45

"$SERVER" \
    127.0.0.1:25060 \
    "$RUN/controller.sqlite3" \
    127.0.0.1:28060 \
    127.0.0.1:28090 \
    "$RUN/artifacts" \
    >"$RUN/integration-server.log" 2>&1 &
PIDS+=($!)
wait_http http://127.0.0.1:28060/healthz 30

"$NODE" "$RUN/node.toml" >"$RUN/roboguide-node.log" 2>&1 &
NODE_PID=$!
PIDS+=($NODE_PID)
wait_node "$NODE_PID"
curl -sf http://127.0.0.1:28060/v1/inventory -o "$RUN/inventory.json"

python3 - "$RUN/provenance.json" "$REPO" "$EXP_ROOT/repos/COHERENT" <<'PYEOF'
import json
import subprocess
import sys
from datetime import datetime, timezone

output, roboguide, coherent = sys.argv[1:]
def git(path, *args):
    return subprocess.check_output(["git", "-C", path, *args], text=True).strip()
record = {
    "schema": "roboguide.e2-s0-provenance/v0.1",
    "started_at": datetime.now(timezone.utc).isoformat(),
    "roboguide_commit": git(roboguide, "rev-parse", "HEAD"),
    "roboguide_status": git(roboguide, "status", "--short"),
    "coherent_commit": git(coherent, "rev-parse", "HEAD"),
    "coherent_status": git(coherent, "status", "--short"),
    "mission_plan": "scenarios/e2-coherent-minimal/mission-plan.json",
    "controlled_scope": "one pre-authored COHERENT Trio plan as one canonical operation",
}
with open(output, "w", encoding="utf-8") as stream:
    json.dump(record, stream, indent=2, sort_keys=True)
PYEOF

curl -sS -X POST http://127.0.0.1:28060/v1/missions \
    -H 'Content-Type: application/json' \
    --data-binary @"$SCENARIO/mission-plan.json" \
    -D "$RUN/post-headers.txt" \
    -o "$RUN/post-body.json" \
    -w '%{http_code}' >"$RUN/post-status.txt"

wait_mission "$RUN/mission.json"
sleep 2
curl -sf http://127.0.0.1:28060/v1/missions/mission-e2-s0-coherent-merom \
    -o "$RUN/mission.json"
curl -sf http://127.0.0.1:28060/v1/events -o "$RUN/events.json"
curl -sf http://127.0.0.1:28060/v1/execution-attempts -o "$RUN/execution-attempts.json"

for pid in "${PIDS[@]}"; do kill -0 "$pid"; done
python3 "$SCENARIO/verify-controlled.py" "$RUN" > "$RUN/verdict.json"
sha256sum "$RUN"/*.json "$RUN"/*.log "$RUN"/*.txt > "$RUN/SHA256SUMS"
cat "$RUN/verdict.json"
echo "E2-S0 evidence: $RUN"
