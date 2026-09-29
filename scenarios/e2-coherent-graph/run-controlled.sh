#!/usr/bin/env bash
# E2-S1: public COHERENT graph task through three production RoboGuide nodes.
set -euo pipefail

SCENARIO="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$SCENARIO/../.." && pwd)"
EXP_ROOT="${ROBOGUIDE_E2_ROOT:-$(cd "$REPO/../.." && pwd)}"
RUN="${1:-$EXP_ROOT/results/roboguide-controlled-graph/env4-task17/$(date +%Y%m%d-%H%M%S)}"
SERVER="$REPO/target/debug/integration-server"
NODE="$REPO/target/debug/roboguide-node"
PEFA="$EXP_ROOT/repos/COHERENT/src/experiment/PEFA"
MISSION="mission-e2-s1-coherent-env4-task17"
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

wait_nodes() {
    for _ in $(seq 1 120); do
        for pid in "${NODE_PIDS[@]}"; do
            if ! kill -0 "$pid" 2>/dev/null; then
                echo "roboguide-node exited before all registrations" >&2
                return 1
            fi
        done
        if curl -sf http://127.0.0.1:28070/v1/inventory | python3 -c '
import json,sys
nodes={node["node_id"] for node in json.load(sys.stdin)["nodes"]}
required={"coherent-arm-e2-s1","coherent-drone-e2-s1","coherent-dog-e2-s1"}
raise SystemExit(0 if required <= nodes else 1)
'; then return 0; fi
        sleep 1
    done
    echo "timeout waiting for three E2-S1 nodes" >&2
    return 1
}

wait_mission() {
    local output="$1" status
    for _ in $(seq 1 240); do
        curl -sf "http://127.0.0.1:28070/v1/missions/$MISSION" -o "$output" || true
        status=$(python3 - "$output" <<'PYEOF' || true
import json,sys
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
    echo "timeout waiting for E2-S1 Mission" >&2
    return 1
}

render_node() {
    local node_id="$1" local_system="$2" contract="$3" readiness="$4" resource="$5" lock="$6"
    sed \
        -e "s|NODE_ID_PLACEHOLDER|$node_id|g" \
        -e "s|STATE_DIRECTORY_PLACEHOLDER|$RUN/node-state-$node_id|g" \
        -e "s|LOCAL_SYSTEM_PLACEHOLDER|$local_system|g" \
        -e "s|CONTRACT_PLACEHOLDER|$contract|g" \
        -e "s|READINESS_PLACEHOLDER|$readiness|g" \
        -e "s|RESOURCE_PLACEHOLDER|$resource|g" \
        -e "s|LOCK_PLACEHOLDER|$lock|g" \
        "$SCENARIO/node.toml.template" > "$RUN/$node_id.toml"
}

for port in 25070 28070 28091 28120; do
    if ss -tln | grep -q ":$port "; then
        echo "configured port is occupied: $port" >&2
        exit 1
    fi
done
if [[ ! -x "$SERVER" || ! -x "$NODE" ]]; then
    echo "build integration-server and roboguide-node before running E2-S1" >&2
    exit 1
fi
if [[ ! -f "$PEFA/env/env4.json" ]]; then
    echo "COHERENT PEFA env4 dataset is unavailable" >&2
    exit 1
fi
if [[ -e "$RUN" ]]; then
    echo "refusing to reuse run directory: $RUN" >&2
    exit 1
fi
mkdir -p "$RUN/artifacts" "$RUN/graph-evidence"

render_node coherent-arm-e2-s1 coherent-arm-local coherent.arm-phase@v1 coherent.arm-phase coherent-arm-slot coherent-arm-world
render_node coherent-drone-e2-s1 coherent-drone-local coherent.drone-phase@v1 coherent.drone-phase coherent-drone-slot coherent-drone-world
render_node coherent-dog-e2-s1 coherent-dog-local coherent.dog-phase@v1 coherent.dog-phase coherent-dog-slot coherent-dog-world

PYTHONPATH="$REPO/integrations/coherent-local-eaios" \
python3 -u -m coherent_local_eaios.graph_main \
    --port 28120 \
    --state-db "$RUN/coherent-graph.sqlite3" \
    --evidence-dir "$RUN/graph-evidence" \
    --pefa-root "$PEFA" \
    >"$RUN/coherent-graph.log" 2>&1 &
PIDS+=($!)
wait_http http://127.0.0.1:28120/v1/health 30

"$SERVER" \
    127.0.0.1:25070 \
    "$RUN/controller.sqlite3" \
    127.0.0.1:28070 \
    127.0.0.1:28091 \
    "$RUN/artifacts" \
    >"$RUN/integration-server.log" 2>&1 &
PIDS+=($!)
wait_http http://127.0.0.1:28070/healthz 30

NODE_PIDS=()
for node_id in coherent-arm-e2-s1 coherent-drone-e2-s1 coherent-dog-e2-s1; do
    "$NODE" "$RUN/$node_id.toml" >"$RUN/$node_id.log" 2>&1 &
    NODE_PIDS+=($!)
    PIDS+=($!)
done
wait_nodes
curl -sf http://127.0.0.1:28070/v1/inventory -o "$RUN/inventory.json"

python3 - "$RUN/provenance.json" "$REPO" "$EXP_ROOT/repos/COHERENT" <<'PYEOF'
import json,subprocess,sys
from datetime import datetime,timezone
output,roboguide,coherent=sys.argv[1:]
def git(path,*args):
    return subprocess.check_output(["git","-C",path,*args],text=True).strip()
record={
    "schema":"roboguide.e2-s1-provenance/v0.1",
    "started_at":datetime.now(timezone.utc).isoformat(),
    "roboguide_commit":git(roboguide,"rev-parse","HEAD"),
    "roboguide_status":git(roboguide,"status","--short"),
    "coherent_commit":git(coherent,"rev-parse","HEAD"),
    "coherent_status":git(coherent,"status","--short"),
    "public_task":"env4/task17",
    "ground_truth_steps":13,
    "mission_plan":"scenarios/e2-coherent-graph/mission-plan.json",
    "controlled_scope":"fixed correct graph plan split across three formal nodes",
}
json.dump(record,open(output,"w",encoding="utf-8"),indent=2,sort_keys=True)
PYEOF

curl -sS -X POST http://127.0.0.1:28070/v1/missions \
    -H 'Content-Type: application/json' \
    --data-binary @"$SCENARIO/mission-plan.json" \
    -D "$RUN/post-headers.txt" \
    -o "$RUN/post-body.json" \
    -w '%{http_code}' >"$RUN/post-status.txt"

wait_mission "$RUN/mission.json"
sleep 1
curl -sf "http://127.0.0.1:28070/v1/missions/$MISSION" -o "$RUN/mission.json"
curl -sf http://127.0.0.1:28070/v1/events -o "$RUN/events.json"
curl -sf http://127.0.0.1:28070/v1/execution-attempts -o "$RUN/execution-attempts.json"

for pid in "${PIDS[@]}"; do kill -0 "$pid"; done
python3 "$SCENARIO/verify-controlled.py" "$RUN"
find "$RUN" -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > "$RUN/SHA256SUMS"
echo "E2-S1 evidence: $RUN"
