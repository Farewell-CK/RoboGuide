#!/usr/bin/env bash
# C1-S0 production happy-path smoke through Control, Node Protocol, and real Habitat.
set -euo pipefail

SCENARIO="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$SCENARIO/../.." && pwd)"
DEFAULT_EMOS_ROOT="$(dirname "$REPO")/emos-baseline"
EMOS_ROOT="${ROBOGUIDE_EMOS_ROOT:-$DEFAULT_EMOS_ROOT}"
HABITAT_ENV="${ROBOGUIDE_HABITAT_CONDA_ENV:-habitat}"
RUN="/tmp/roboguide-habitat-c1-s0"
SERVER="$REPO/target/debug/integration-server"
NODE="$REPO/target/debug/roboguide-node"
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
    # Wait until the production Controller inventory contains the Habitat node.
    for _ in $(seq 1 90); do
        if curl -sf http://127.0.0.1:28060/v1/inventory \
            | grep -q '"habitat-spot-c1-s0"'; then
            return 0
        fi
        sleep 1
    done
    echo "timeout waiting for Habitat node registration" >&2
    return 1
}

wait_mission() {
    # Wait until the real Mission reaches Completed or expose its terminal mismatch.
    local output="$1"
    for _ in $(seq 1 240); do
        curl -sf \
            http://127.0.0.1:28060/v1/missions/mission-c1-s0-habitat-navigation \
            -o "$output" || true
        if python3 - "$output" <<'PYEOF'
import json
import sys

try:
    status = json.load(open(sys.argv[1], encoding="utf-8"))["status"]
except (FileNotFoundError, KeyError, json.JSONDecodeError):
    raise SystemExit(1)
if status == "Completed":
    raise SystemExit(0)
if status in {"Failed", "Cancelled"}:
    print(f"Mission terminated unexpectedly: {status}", file=sys.stderr)
    raise SystemExit(2)
raise SystemExit(1)
PYEOF
        then
            return 0
        fi
        sleep 1
    done
    echo "timeout waiting for Mission completion" >&2
    return 1
}

if [[ "$RUN" != "/tmp/roboguide-habitat-c1-s0" ]]; then
    echo "unsafe smoke run directory" >&2
    exit 1
fi
rm -rf -- "$RUN"
mkdir -p "$RUN/mpl" "$RUN/artifacts"

if [[ ! -x "$SERVER" || ! -x "$NODE" ]]; then
    echo "build integration-server and roboguide-node before running this smoke" >&2
    exit 1
fi
if [[ ! -d "$EMOS_ROOT" ]]; then
    echo "EMOS checkout does not exist: $EMOS_ROOT" >&2
    exit 1
fi
HABITAT_PREFIX="$(conda run -n "$HABITAT_ENV" bash -c 'printf %s "$CONDA_PREFIX"')"
HABITAT_PYTHON="$HABITAT_PREFIX/bin/python"
if [[ ! -x "$HABITAT_PYTHON" ]]; then
    echo "Habitat interpreter does not exist: $HABITAT_PYTHON" >&2
    exit 1
fi

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
        --step-period-ms 20
) >"$RUN/habitat-bridge.log" 2>&1 &
PIDS+=($!)
wait_http http://127.0.0.1:28100/v1/health 150

"$SERVER" \
    127.0.0.1:25060 \
    "$RUN/controller.sqlite3" \
    127.0.0.1:28060 \
    127.0.0.1:28090 \
    "$RUN/artifacts" \
    >"$RUN/integration-server.log" 2>&1 &
PIDS+=($!)
wait_http http://127.0.0.1:28060/healthz 30

"$NODE" "$SCENARIO/node.toml" >"$RUN/roboguide-node.log" 2>&1 &
PIDS+=($!)
wait_node
curl -sf http://127.0.0.1:28060/v1/inventory -o "$RUN/inventory.json"

curl -sS -X POST http://127.0.0.1:28060/v1/missions \
    -H 'Content-Type: application/json' \
    --data-binary @"$SCENARIO/mission-plan.json" \
    -D "$RUN/post-headers.txt" \
    -o "$RUN/post-body.json" \
    -w '%{http_code}' >"$RUN/post-status.txt"

wait_mission "$RUN/mission.json"
curl -sf http://127.0.0.1:28060/v1/events -o "$RUN/events.json"
curl -sf http://127.0.0.1:28060/v1/execution-attempts -o "$RUN/execution-attempts.json"

for pid in "${PIDS[@]}"; do
    kill -0 "$pid"
done

python3 "$SCENARIO/verify-smoke.py" "$RUN" >"$RUN/verdict.json"
cat "$RUN/verdict.json"
