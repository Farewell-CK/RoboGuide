#!/usr/bin/env bash
# E1-I shared-world production smoke: two RoboGuide Nodes over ONE Habitat world.
# Usage: run-shared-world.sh <paired|cancel|negative|single> <run-dir-abs-path>
set -euo pipefail

SCENARIO="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$SCENARIO/../.." && pwd)"
DEFAULT_EMOS_ROOT="$(dirname "$REPO")/emos-baseline"
EMOS_ROOT="${ROBOGUIDE_EMOS_ROOT:-$DEFAULT_EMOS_ROOT}"
HABITAT_ENV="${ROBOGUIDE_HABITAT_CONDA_ENV:-habitat}"
MODE="${1:?usage: run-shared-world.sh <paired|cancel|negative|single> <run-dir>}"
RUN="${2:?usage: run-shared-world.sh <paired|cancel|negative|single> <run-dir>}"
SERVER="$REPO/target/debug/integration-server"
NODE="$REPO/target/debug/roboguide-node"
MISSION_ID="mission-e1-i-shared-world-episode-51"
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
        if curl -sf -o /dev/null "$url"; then return 0; fi
        sleep 1
    done
    echo "timeout waiting for $url" >&2
    return 1
}

wait_nodes() {
    # Wait until the inventory contains every requested node id.
    local -a ids=("$@")
    for _ in $(seq 1 120); do
        local missing=0
        for id in "${ids[@]}"; do
            if ! curl -sf http://127.0.0.1:28060/v1/inventory | grep -q "\"$id\""; then
                missing=1
            fi
        done
        if [[ "$missing" == "0" ]]; then return 0; fi
        sleep 1
    done
    echo "timeout waiting for node registration" >&2
    return 1
}

wait_mission_terminal() {
    # Poll one mission id to any terminal status within the budget.
    local mission="$1" output="$2" budget="$3"
    for _ in $(seq 1 "$budget"); do
        curl -sf "http://127.0.0.1:28060/v1/missions/$mission" -o "$output" || true
        if python3 - "$output" <<'PYEOF'
import json
import sys

try:
    status = json.load(open(sys.argv[1], encoding="utf-8"))["status"]
except (FileNotFoundError, KeyError, json.JSONDecodeError):
    raise SystemExit(1)
if status in {"Completed", "Failed", "Cancelled"}:
    raise SystemExit(0)
raise SystemExit(1)
PYEOF
        then
            return 0
        fi
        sleep 1
    done
    echo "timeout waiting for mission $mission" >&2
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

PLAN="$SCENARIO/mission-plan.json"
PAIR_WAIT=420
MISSION_BUDGET=480
NODES_TO_WAIT=(e1-shared-spot-a e1-shared-spot-b)
if [[ "$MODE" == "negative" ]]; then
    PLAN="$SCENARIO/mission-plan-negative.json"
    MISSION_ID="mission-e1-i-shared-world-negative"
elif [[ "$MODE" == "single" ]]; then
    PAIR_WAIT=45
    NODES_TO_WAIT=(e1-shared-spot-a)
fi

rm -rf -- "$RUN"
mkdir -p "$RUN/mpl" "$RUN/artifacts" "$RUN/evidence"
for n in a b; do
    sed "s|NODE_STATE_PLACEHOLDER|$RUN/node-state-$n|" "$SCENARIO/node-$n.toml" > "$RUN/node-$n.toml"
done

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
        --subtask-mode entity-grounded \
        --port-b 28102 \
        --state-db "$RUN/bridge-a.sqlite3" \
        --state-db-b "$RUN/bridge-b.sqlite3" \
        --agent-id 0 \
        --agent-b-id 1 \
        --pair-wait-s "$PAIR_WAIT" \
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
if [[ "$MODE" != "single" ]]; then
    "$NODE" "$RUN/node-b.toml" >"$RUN/node-b.log" 2>&1 &
    PIDS+=($!)
fi
wait_nodes "${NODES_TO_WAIT[@]}"
curl -sf http://127.0.0.1:28060/v1/inventory -o "$RUN/inventory.json"

curl -sS -X POST http://127.0.0.1:28060/v1/missions \
    -H 'Content-Type: application/json' \
    --data-binary @"$PLAN" \
    -o "$RUN/post-body.json" -w '%{http_code}' >"$RUN/post-status.txt"

if [[ "$MODE" == "cancel" ]]; then
    # Cancel only after BOTH bridge endpoints report true local RUNNING; the
    # mission-lifecycle Running label alone precedes task dispatch.
    for _ in $(seq 1 240); do
        STATE=$(curl -sf "http://127.0.0.1:28060/v1/missions/$MISSION_ID" \
            | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])' || echo unreachable)
        echo "$STATE" >> "$RUN/cancel-pre-states.txt"
        RUNNING_COUNT=$(python3 - "$RUN" <<'PYEOF'
import sqlite3, sys

count = 0
for suffix in ("a", "b"):
    try:
        connection = sqlite3.connect(f"file:{sys.argv[1]}/bridge-{suffix}.sqlite3?mode=ro", uri=True)
        rows = connection.execute("SELECT state FROM executions").fetchall()
        connection.close()
    except sqlite3.Error:
        continue
    if any(row[0] in {"RUNNING", "COMPLETED", "CANCELLED", "FAILED"} for row in rows):
        count += 1
print(count)
PYEOF
        )
        if [[ "$RUNNING_COUNT" == "2" ]]; then
            sleep 1
            curl -sS -X POST "http://127.0.0.1:28060/v1/missions/$MISSION_ID/cancel" \
                -H 'Content-Type: application/json' -d '{}' \
                -o "$RUN/cancel-body.json" -w '%{http_code}' >"$RUN/cancel-status.txt" || true
            break
        fi
        sleep 1
    done
fi

wait_mission_terminal "$MISSION_ID" "$RUN/mission.json" "$MISSION_BUDGET"
sleep 3
curl -sf "http://127.0.0.1:28060/v1/missions/$MISSION_ID" -o "$RUN/mission.json" || true
curl -sf http://127.0.0.1:28060/v1/events -o "$RUN/events.json" || true
curl -sf http://127.0.0.1:28060/v1/execution-attempts -o "$RUN/execution-attempts.json" || true

for pid in "${PIDS[@]}"; do
    kill -0 "$pid"
done

python3 "$SCENARIO/verify-shared-world.py" "$RUN" "$MODE" >"$RUN/verdict.json"
cat "$RUN/verdict.json"
