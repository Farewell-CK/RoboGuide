#!/usr/bin/env bash
# E1 Protocol B1 RoboGuide arm: high-level text instruction through the real
# production Mission Intelligence (Interpreter -> Planner -> Reviewer/Repair ->
# approval) -> Controller -> shared-world RNS -> original EMOS Stage2.
# No pre-authored MissionPlan is submitted; the static plan remains B2-only.
# Usage: run-b1-roboguide.sh <run-dir-abs-or-rel>
set -euo pipefail

SCENARIO="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$SCENARIO/../.." && pwd)"
DEFAULT_EMOS_ROOT="$(dirname "$REPO")/emos-baseline"
EMOS_ROOT="${ROBOGUIDE_EMOS_ROOT:-$DEFAULT_EMOS_ROOT}"
HABITAT_ENV="${ROBOGUIDE_HABITAT_CONDA_ENV:-habitat}"
RUN="${1:?usage: run-b1-roboguide.sh <run-dir> [input-json]}"
INPUT_JSON="${2:-${ROBOGUIDE_B1_INPUT:-$SCENARIO/b1-input.json}}"
SERVER="$REPO/target/debug/integration-server"
NODE="$REPO/target/debug/roboguide-node"
PIDS=()

# Port hygiene: a previous run's server may survive its trap (SIGTERM is not
# guaranteed to be prompt). Kill only OUR OWN leftover binaries on these ports,
# then require the ports to be free before starting anything.
clean_port() {
    # Kill leftover RoboGuide/Habitat processes holding one of our ports.
    local port="$1" pid
    pid=$(ss -tlnp 2>/dev/null | grep ":$port " | grep -oP 'pid=\K[0-9]+' | head -1 || true)
    if [[ -n "$pid" ]]; then
        if ps -p "$pid" -o args= | grep -qE "integration-server|roboguide-node|habitat_local_eaios|mission.api|apps/mission-service/main.py"; then
            echo "port $port held by leftover pid $pid ($(ps -p "$pid" -o args= | head -c 60)) - killing" >&2
            kill -9 "$pid" 2>/dev/null || true
            sleep 1
        else
            echo "FATAL: port $port held by foreign process $pid" >&2
            exit 1
        fi
    fi
}

cleanup() {
    # Stop only the exact processes launched by this B1 run.
    for pid in "${PIDS[@]:-}"; do
        kill "$pid" 2>/dev/null || true
    done
}
trap cleanup EXIT

wait_http() {
    # Wait for one HTTP route within the supplied number of seconds.
    local url="$1" budget="$2"
    for _ in $(seq 1 "$budget"); do
        if curl -sf -o /dev/null "$url"; then return 0; fi
        sleep 1
    done
    echo "timeout waiting for $url" >&2
    return 1
}

wait_nodes() {
    # Wait until the inventory contains both shared-world node ids.
    for _ in $(seq 1 120); do
        if curl -sf http://127.0.0.1:28060/v1/inventory \
            | grep -q '"e1-shared-node-a"' \
            && curl -sf http://127.0.0.1:28060/v1/inventory \
            | grep -q '"e1-shared-node-b"'; then
            return 0
        fi
        sleep 1
    done
    echo "timeout waiting for node registration" >&2
    return 1
}

wait_mission_terminal() {
    # Poll the mission (once it exists) to any terminal status.
    local output="$1" budget="$2"
    for _ in $(seq 1 "$budget"); do
        curl -sf "http://127.0.0.1:28060/v1/missions/$MISSION_ID" -o "$output" || true
        if python3 - "$output" <<'PYEOF'
import json
import sys

try:
    status = json.load(open(sys.argv[1], encoding="utf-8"))["status"]
except (FileNotFoundError, KeyError, json.JSONDecodeError):
    raise SystemExit(1)
raise SystemExit(0 if status in {"Completed", "Failed", "Cancelled"} else 1)
PYEOF
        then
            return 0
        fi
        sleep 1
    done
    echo "timeout waiting for mission $MISSION_ID" >&2
    return 1
}

if [[ ! -x "$SERVER" || ! -x "$NODE" ]]; then
    echo "build integration-server and roboguide-node first" >&2
    exit 1
fi
if [[ ! -d "$EMOS_ROOT" ]]; then
    echo "EMOS checkout does not exist: $EMOS_ROOT" >&2
    exit 1
fi

mkdir -p "$RUN"
RUN="$(cd "$RUN" && pwd)"
rm -rf -- "$RUN"/* 2>/dev/null || true
mkdir -p "$RUN/mpl" "$RUN/artifacts" "$RUN/evidence"
for n in a b; do
    sed "s|NODE_STATE_PLACEHOLDER|$RUN/node-state-$n|" "$SCENARIO/node-$n.toml" > "$RUN/node-$n.toml"
done
sed "s|STATE_DB_PLACEHOLDER|$RUN/mission-service.sqlite3|" \
    "$SCENARIO/mission-service-b1.toml" > "$RUN/mission-service-b1.toml"

clean_port 25060
clean_port 28060
clean_port 28090
clean_port 28100
clean_port 28102
clean_port 8070
HABITAT_PYTHON="$(conda run -n "$HABITAT_ENV" which python)"
(
    cd "$EMOS_ROOT"
    exec env \
        CUDA_VISIBLE_DEVICES="${ROBOGUIDE_HABITAT_CUDA_DEVICE:-1}" \
        HABITAT_SIM_LOG=quiet MAGNUM_LOG=quiet MPLCONFIGDIR="$RUN/mpl" \
        PYTHONPATH="$REPO/integrations/habitat-local-eaios:$EMOS_ROOT/habitat-mas" \
        "$HABITAT_PYTHON" -u -m habitat_local_eaios \
        --port 28100 \
        --backend shared-emos-stage2 \
        --subtask-mode natural-objective \
        --port-b 28102 \
        --state-db "$RUN/bridge-a.sqlite3" \
        --state-db-b "$RUN/bridge-b.sqlite3" \
        --agent-id 0 \
        --agent-b-id 1 \
        --pair-wait-s 1200 \
        --evidence-dir "$RUN/evidence" \
        --habitat-config \
            "$EMOS_ROOT/habitat-baselines/habitat_baselines/config/multi_rearrange/llm_spot_fetch_mobility.yaml" \
        --episode-id 51 \
        --max-steps 3000 \
        --step-period-ms 20
) >"$RUN/shared-bridge.log" 2>&1 &
PIDS+=($!)
wait_http http://127.0.0.1:28100/v1/health 240
wait_http http://127.0.0.1:28102/v1/health 30

"$SERVER" 127.0.0.1:25060 "$RUN/controller.sqlite3" 127.0.0.1:28060 \
    127.0.0.1:28090 "$RUN/artifacts" >"$RUN/integration-server.log" 2>&1 &
PIDS+=($!)
wait_http http://127.0.0.1:28060/healthz 30

"$NODE" "$RUN/node-a.toml" >"$RUN/node-a.log" 2>&1 &
PIDS+=($!)
"$NODE" "$RUN/node-b.toml" >"$RUN/node-b.log" 2>&1 &
PIDS+=($!)
wait_nodes
curl -sf http://127.0.0.1:28060/v1/inventory -o "$RUN/inventory.json"

# Production Mission Intelligence ingress (no static plan anywhere in this path).
cd "$REPO"
# The frozen relay endpoint is plain HTTP on a remote host; the MI provider
# config requires this explicit opt-out (same relay the EMOS arm uses).
ROBOGUIDE_ALLOW_INSECURE_LLM_HTTP=1 uv run python "$REPO/apps/mission-service/main.py" \
    --mission-config config/mission.toml \
    --service-config "$RUN/mission-service-b1.toml" \
    --repository-root "$REPO" >"$RUN/mission-service.log" 2>&1 &
PIDS+=($!)
cd "$RUN"
# The collection GET returns 404 by design; connectivity alone proves readiness.
for _ in $(seq 1 120); do
    if curl -s -o /dev/null http://127.0.0.1:8070/v1/mission-requests; then break; fi
    sleep 1
done


INSTRUCTION=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["instruction"])' "$INPUT_JSON")
SUBMITTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "submission_start_utc=$SUBMITTED_AT" > "$RUN/b1-timing.txt"
REQUEST_JSON=$(curl -sS -X POST http://127.0.0.1:8070/v1/mission-requests \
    -H 'Content-Type: application/json' \
    -d "{\"instruction\": $(python3 -c 'import json,sys;print(json.dumps(sys.argv[1]))' "$INSTRUCTION")}")
REQUEST_ID=$(python3 -c 'import json,sys;print(json.loads(sys.argv[1])["request_id"])' "$REQUEST_JSON" 2>/dev/null || echo "")
MISSION_ID=$(python3 -c 'import json,sys;print(json.loads(sys.argv[1])["mission_id"])' "$REQUEST_JSON" 2>/dev/null || echo "")
if [[ -z "$REQUEST_ID" ]]; then
    echo "$REQUEST_JSON" > "$RUN/b1-request-record.json"
    echo "B1 ingress failed before a request id was minted" >&2
    exit 1
fi
echo "$REQUEST_JSON" > "$RUN/b1-request-record.json"
echo "request_id=$REQUEST_ID" >> "$RUN/b1-timing.txt"

# Wait for the pre-execution lifecycle to settle (accepted = submitted to Control).
LIFECYCLE=""
for _ in $(seq 1 240); do
    curl -sf "http://127.0.0.1:8070/v1/mission-requests/$REQUEST_ID" \
        -o "$RUN/b1-request-record.json" || true
    LIFECYCLE=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["lifecycle"])' \
        "$RUN/b1-request-record.json" 2>/dev/null || echo unknown)
    case "$LIFECYCLE" in
        Accepted|Blocked|Failed|NeedsClarification|AwaitingApproval|Cancelled)
            break ;;
    esac
    sleep 2
done
echo "lifecycle=$LIFECYCLE" >> "$RUN/b1-timing.txt"

if [[ "$LIFECYCLE" == "Accepted" ]]; then
    wait_mission_terminal "$RUN/mission.json" 1800 || true
    sleep 3
    curl -sf "http://127.0.0.1:28060/v1/missions/$MISSION_ID" -o "$RUN/mission.json" || true
fi
curl -sf http://127.0.0.1:28060/v1/events -o "$RUN/events.json" || true
curl -sf http://127.0.0.1:28060/v1/execution-attempts -o "$RUN/execution-attempts.json" || true

for pid in "${PIDS[@]}"; do
    kill -0 "$pid" 2>/dev/null || true
done

python3 "$SCENARIO/verify-shared-world.py" "$RUN" paired >"$RUN/verdict.json" || true
python3 "$SCENARIO/verify-b1.py" "$RUN" >"$RUN/b1-verdict.json"
cat "$RUN/b1-verdict.json"
