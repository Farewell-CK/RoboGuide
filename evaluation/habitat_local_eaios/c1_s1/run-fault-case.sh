#!/usr/bin/env bash
# C1-S1 fault-case runner: production path with one armed status-fault rule.
# Usage: run-fault-case.sh <case-name> <fault-rule> [cancel]
#   fault-rule examples: fail-status:1 | timeout-status:1 | malformed-status:1
#                        refuse-window:15
#   "cancel" cancels the Mission right after the first RUNNING observation.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
SCENARIO="$REPO/scenarios/habitat-local-eaios-c1-s0"
DEFAULT_EMOS_ROOT="$(dirname "$REPO")/emos-baseline"
EMOS_ROOT="${ROBOGUIDE_EMOS_ROOT:-$DEFAULT_EMOS_ROOT}"
HABITAT_ENV="${ROBOGUIDE_HABITAT_CONDA_ENV:-habitat}"
CASE_NAME="${1:?usage: run-fault-case.sh <case> <fault-rule> [cancel]}"
FAULT_RULE="${2:?missing fault rule}"
DO_CANCEL="${3:-}"
RUN="/tmp/roboguide-c1-s1-$CASE_NAME"
MISSION_ID="mission-c1-s0-habitat-navigation"
SERVER="$REPO/target/debug/integration-server"
NODE="$REPO/target/debug/roboguide-node"
PIDS=()

cleanup() {
    # Stop only the exact processes launched by this fault case.
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

wait_node() {
    # Wait until the Controller inventory contains the Habitat node.
    for _ in $(seq 1 90); do
        if curl -sf http://127.0.0.1:28060/v1/inventory | grep -q '"habitat-spot-c1-s0"'; then
            return 0
        fi
        sleep 1
    done
    echo "timeout waiting for node registration" >&2
    return 1
}

if [[ ! -x "$SERVER" || ! -x "$NODE" ]]; then
    echo "build integration-server and roboguide-node first" >&2
    exit 1
fi
rm -rf -- "$RUN"
mkdir -p "$RUN/mpl" "$RUN/artifacts"
sed "s|state_directory = .*|state_directory = \"$RUN/node-state\"|" \
    "$SCENARIO/node.toml" > "$RUN/node.toml"
# Route the node's workflow traffic through the fault proxy.
sed "s|endpoint = \"http://127.0.0.1:28100\"|endpoint = \"http://127.0.0.1:28101\"|" \
    "$RUN/node.toml" > "$RUN/node.toml.tmp" && mv "$RUN/node.toml.tmp" "$RUN/node.toml"

HABITAT_PYTHON="$(conda run -n "$HABITAT_ENV" which python)"
(
    cd "$EMOS_ROOT"
    exec env \
        CUDA_VISIBLE_DEVICES="${ROBOGUIDE_HABITAT_CUDA_DEVICE:-1}" \
        HABITAT_SIM_LOG=quiet MAGNUM_LOG=quiet MPLCONFIGDIR="$RUN/mpl" \
        PYTHONPATH="$REPO/integrations/habitat-local-eaios" \
        "$HABITAT_PYTHON" -u -m habitat_local_eaios \
        --port 28100 \
        --state-db "$RUN/habitat-bridge.sqlite3" \
        --habitat-config \
            "$EMOS_ROOT/habitat-baselines/habitat_baselines/config/multi_rearrange/llm_spot_fetch_mobility.yaml" \
        --episode-id 51 --agent-id 0 --max-steps 1000 --step-period-ms 100
) >"$RUN/habitat-bridge.log" 2>&1 &
PIDS+=($!)
wait_http http://127.0.0.1:28100/v1/health 150

python3 "$HERE/fault_proxy.py" --listen-port 28101 --target-port 28100 \
    --fault "$FAULT_RULE" >"$RUN/fault-proxy.log" 2>&1 &
PIDS+=($!)
sleep 1

"$SERVER" 127.0.0.1:25060 "$RUN/controller.sqlite3" 127.0.0.1:28060 \
    127.0.0.1:28090 "$RUN/artifacts" >"$RUN/integration-server.log" 2>&1 &
PIDS+=($!)
wait_http http://127.0.0.1:28060/healthz 30

"$NODE" "$RUN/node.toml" >"$RUN/roboguide-node.log" 2>&1 &
PIDS+=($!)
wait_node

curl -sS -X POST http://127.0.0.1:28060/v1/missions \
    -H 'Content-Type: application/json' \
    --data-binary @"$SCENARIO/mission-plan.json" \
    -o "$RUN/post-body.json" -w '%{http_code}' >"$RUN/post-status.txt"

# Observe the mission for a bounded window; record every observed status.
OBSERVATIONS="$RUN/status-observations.txt"
: > "$OBSERVATIONS"
CANCEL_SENT=0
for _ in $(seq 1 120); do
    STATUS=$(curl -sf http://127.0.0.1:28060/v1/missions/$MISSION_ID \
        | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])' || echo "unreachable")
    echo "$STATUS" >> "$OBSERVATIONS"
    if [[ "$DO_CANCEL" == "cancel" && "$CANCEL_SENT" == "0" && "$STATUS" == "Running" ]]; then
        sleep 1
        curl -sS -X POST "http://127.0.0.1:28060/v1/missions/$MISSION_ID/cancel" \
            -H 'Content-Type: application/json' -d '{}' \
            -o "$RUN/cancel-body.json" -w '%{http_code}' >"$RUN/cancel-status.txt" || true
        CANCEL_SENT=1
    fi
    if [[ "$STATUS" == "Completed" || "$STATUS" == "Failed" || "$STATUS" == "Cancelled" ]]; then
        break
    fi
    sleep 1
done
sleep 2
curl -sf http://127.0.0.1:28060/v1/missions/$MISSION_ID -o "$RUN/mission.json" || true
curl -sf http://127.0.0.1:28060/v1/events -o "$RUN/events.json" || true
curl -sf http://127.0.0.1:28060/v1/execution-attempts -o "$RUN/execution-attempts.json" || true

for pid in "${PIDS[@]}"; do kill -0 "$pid" 2>/dev/null || true; done
echo "fault case $CASE_NAME complete; final mission status: $(tail -1 "$OBSERVATIONS")"
