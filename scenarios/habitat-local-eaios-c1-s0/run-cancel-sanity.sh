#!/usr/bin/env bash
# C1-S0 production cancellation sanity through Control, Node Protocol, and real Habitat.
set -euo pipefail

SCENARIO="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$SCENARIO/../.." && pwd)"
DEFAULT_EMOS_ROOT="$(dirname "$REPO")/emos-baseline"
EMOS_ROOT="${ROBOGUIDE_EMOS_ROOT:-$DEFAULT_EMOS_ROOT}"
HABITAT_ENV="${ROBOGUIDE_HABITAT_CONDA_ENV:-habitat}"
RUN="/tmp/roboguide-habitat-c1-s0-cancel"
SERVER="$REPO/target/debug/integration-server"
NODE="$REPO/target/debug/roboguide-node"
MISSION_ID="mission-c1-s0-habitat-cancel"
PIDS=()

cleanup() {
    # Stop only the exact processes launched by this smoke.
    for pid in "${PIDS[@]:-}"; do
        kill "$pid" 2>/dev/null || true
    done
}
trap cleanup EXIT

wait_http() {
    # Wait for one HTTP route within the supplied number of seconds.
    local url="$1" budget="$2"
    for _ in $(seq 1 "$budget"); do
        if curl -sf -o /dev/null "$url"; then
            return 0
        fi
        sleep 1
    done
    echo "timeout waiting for $url" >&2
    return 1
}

wait_node() {
    # Wait until Controller inventory contains the cancellation smoke node.
    for _ in $(seq 1 90); do
        if curl -sf http://127.0.0.1:28060/v1/inventory \
            | grep -q '"habitat-spot-c1-s0-cancel"'; then
            return 0
        fi
        sleep 1
    done
    echo "timeout waiting for Habitat cancellation node" >&2
    return 1
}

wait_local_running() {
    # Wait for the durable bridge handle to enter true local RUNNING state.
    for _ in $(seq 1 800); do
        if python3 - "$RUN/habitat-bridge.sqlite3" <<'PYEOF'
import sqlite3
import sys

try:
    connection = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
    row = connection.execute("SELECT state FROM executions").fetchone()
    connection.close()
except sqlite3.Error:
    raise SystemExit(1)
raise SystemExit(0 if row is not None and row[0] == "RUNNING" else 1)
PYEOF
        then
            return 0
        fi
        sleep 0.05
    done
    echo "timeout waiting for local RUNNING state" >&2
    return 1
}

wait_cancelled() {
    # Wait until terminal local evidence lets orchestration finalize cancellation.
    local output="$1"
    for _ in $(seq 1 120); do
        curl -sf "http://127.0.0.1:28060/v1/missions/$MISSION_ID" -o "$output" || true
        if python3 - "$output" <<'PYEOF'
import json
import sys

try:
    status = json.load(open(sys.argv[1], encoding="utf-8"))["status"]
except (FileNotFoundError, KeyError, json.JSONDecodeError):
    raise SystemExit(1)
if status == "Cancelled":
    raise SystemExit(0)
if status in {"Completed", "Failed"}:
    print(f"Mission terminated unexpectedly: {status}", file=sys.stderr)
    raise SystemExit(2)
raise SystemExit(1)
PYEOF
        then
            return 0
        else
            status_code=$?
            if [[ "$status_code" -eq 2 ]]; then
                return 2
            fi
        fi
        sleep 0.25
    done
    echo "timeout waiting for Mission cancellation" >&2
    return 1
}

if [[ "$RUN" != "/tmp/roboguide-habitat-c1-s0-cancel" ]]; then
    echo "unsafe smoke run directory" >&2
    exit 1
fi
rm -rf -- "$RUN"
mkdir -p "$RUN/mpl" "$RUN/artifacts"

HABITAT_PYTHON="$(conda run -n "$HABITAT_ENV" which python)"
(
    cd "$EMOS_ROOT"
    exec env \
        CUDA_VISIBLE_DEVICES="${ROBOGUIDE_HABITAT_CUDA_DEVICE:-1}" \
        HABITAT_SIM_LOG=quiet \
        MAGNUM_LOG=quiet \
        MPLCONFIGDIR="$RUN/mpl" \
        PYTHONPATH="$REPO/integrations/habitat-local-eaios" \
        "$HABITAT_PYTHON" -u -m habitat_local_eaios \
        --port 28100 \
        --state-db "$RUN/habitat-bridge.sqlite3" \
        --habitat-config \
            "$EMOS_ROOT/habitat-baselines/habitat_baselines/config/multi_rearrange/llm_spot_fetch_mobility.yaml" \
        --episode-id 51 \
        --agent-id 0 \
        --max-steps 1000 \
        --step-period-ms 100
) >"$RUN/habitat-bridge.log" 2>&1 &
PIDS+=($!)
wait_http http://127.0.0.1:28100/v1/health 150

"$SERVER" 127.0.0.1:25060 "$RUN/controller.sqlite3" 127.0.0.1:28060 \
    127.0.0.1:28090 "$RUN/artifacts" >"$RUN/integration-server.log" 2>&1 &
PIDS+=($!)
wait_http http://127.0.0.1:28060/healthz 30

"$NODE" "$SCENARIO/node-cancel.toml" >"$RUN/roboguide-node.log" 2>&1 &
PIDS+=($!)
wait_node

curl -sS -X POST http://127.0.0.1:28060/v1/missions \
    -H 'Content-Type: application/json' \
    --data-binary @"$SCENARIO/mission-plan-cancel.json" \
    -o "$RUN/post-body.json" \
    -w '%{http_code}' >"$RUN/post-status.txt"

wait_local_running
curl -sS -X POST "http://127.0.0.1:28060/v1/missions/$MISSION_ID/cancel" \
    --data '' \
    -o "$RUN/cancel-body.json" \
    -w '%{http_code}' >"$RUN/cancel-status.txt"

wait_cancelled "$RUN/mission.json"
curl -sf http://127.0.0.1:28060/v1/events -o "$RUN/events.json"

for pid in "${PIDS[@]}"; do
    kill -0 "$pid"
done

python3 "$SCENARIO/verify-cancel.py" "$RUN" >"$RUN/verdict.json"
cat "$RUN/verdict.json"
