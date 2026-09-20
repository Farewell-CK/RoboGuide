#!/usr/bin/env bash
# E1 Protocol B1 RoboGuide arm: high-level text instruction through the real
# production Mission Intelligence (Interpreter -> Planner -> Reviewer/Repair ->
# approval) -> Controller -> shared-world RNS -> original EMOS Stage2.
# No pre-authored MissionPlan is submitted; the static plan remains B2-only.
# The executed workload (episode, seed, dataset identity) is extracted from
# the frozen B1 input document, so any E1 population episode runs unchanged.
# The deployment-owned Mission execution profile supplies shared-world space
# capacity to production Mission Intelligence; the script never patches plans.
# Usage: run-b1-roboguide.sh <run-dir-abs-or-rel> [input-json]
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

COMPONENTS=()
REQUEST_ID=""
FAILURE_OWNER=EXTERNAL_INFRA
FAILURE_COMPONENT=environment
FAILURE_REASON=deployment_setup_failed

clean_port() {
    # A conflicting listener is external evidence; never kill another run.
    local port="$1"
    if ss -tln | grep -q ":$port "; then
        FAILURE_REASON="configured_port_occupied:$port"
        echo "$FAILURE_REASON" >&2
        exit 1
    fi
}

finish_run() {
    # Archive evidence before stopping our exact child processes, including early failures.
    local code=$? owner=NONE component="" reason="" index
    trap - EXIT
    set +e
    if [[ "$code" != 0 ]]; then
        owner="$FAILURE_OWNER"
        component="$FAILURE_COMPONENT"
        reason="$FAILURE_REASON"
    fi
    for index in "${!PIDS[@]}"; do
        if ! kill -0 "${PIDS[$index]}" 2>/dev/null && [[ "$owner" == NONE ]]; then
            owner=SUT_SYSTEM
            component="${COMPONENTS[$index]}"
            reason="sut_process_exited_before_collection"
        fi
    done
    uv run --project "$REPO" python -m roboguide_eval.b1_artifacts "$RUN" \
        --request-id "$REQUEST_ID" --failure-owner "$owner" \
        --component "$component" --reason "$reason"
    local archive_code=$?
    for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
    if [[ "$archive_code" != 0 ]]; then
        echo "B1 evidence collection failed; no admission can be claimed" >&2
        exit "$archive_code"
    fi
    exit "$code"
}

wait_http() {
    # A reachable health route may still report OFFLINE while Habitat initializes.
    local url="$1" budget="$2" expected_state="${3:-}" response
    for _ in $(seq 1 "$budget"); do
        if response=$(curl --max-time 5 -sf "$url"); then
            if [[ -z "$expected_state" ]]; then return 0; fi
            if printf '%s' "$response" | python3 -c '
import json, sys
try:
    value = json.load(sys.stdin)
except (ValueError, TypeError):
    raise SystemExit(1)
raise SystemExit(0 if isinstance(value, dict) and value.get("state") == sys.argv[1] else 1)
' "$expected_state"; then return 0; fi
        fi
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
    echo "observation budget exhausted waiting for mission $MISSION_ID" >&2
    return 1
}

mkdir -p "$RUN"
RUN="$(cd "$RUN" && pwd)"
# Preserve existing archives and Harness-owned manifest/log files.
if [[ -e "$RUN/b1-input-used.json" || -e "$RUN/controller.sqlite3" ]]; then
    echo "refusing to overwrite an existing B1 run: $RUN" >&2
    exit 1
fi
cp "$INPUT_JSON" "$RUN/b1-input-used.json"
INPUT_JSON="$RUN/b1-input-used.json"
mkdir -p "$RUN/mpl" "$RUN/artifacts" "$RUN/evidence"
trap finish_run EXIT
# The workload (episode, seed, dataset identity) comes from the frozen B1
# input itself — never from a scenario-embedded episode. The extractor
# fails with a stable field-level reason before any component launches.
WORKLOAD="$(uv run --project "$REPO" python -m roboguide_eval.b1_workload "$INPUT_JSON")" \
    || { FAILURE_REASON=invalid_b1_workload; exit 1; }
EPISODE_ID="$(printf '%s\n' "$WORKLOAD" | sed -n 's/^episode_id=//p')"
SEED="$(printf '%s\n' "$WORKLOAD" | sed -n 's/^seed=//p')"
if [[ ! -x "$SERVER" || ! -x "$NODE" ]]; then
    FAILURE_REASON=required_sut_binary_missing
    exit 1
fi
if [[ ! -d "$EMOS_ROOT" ]]; then
    FAILURE_REASON=emos_checkout_missing
    exit 1
fi
for n in a b; do
    sed "s|NODE_STATE_PLACEHOLDER|$RUN/node-state-$n|" "$SCENARIO/node-$n.toml" > "$RUN/node-$n.toml"
done
sed -e "s|STATE_DB_PLACEHOLDER|$RUN/mission-service.sqlite3|" \
    -e "s|SEMANTIC_EVIDENCE_PLACEHOLDER|$RUN/evidence/authoritative-semantic-evidence.json|" \
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
        --run-id "$(basename "$RUN")" \
        --habitat-config \
            "$EMOS_ROOT/habitat-baselines/habitat_baselines/config/multi_rearrange/llm_spot_fetch_mobility.yaml" \
        --episode-id "$EPISODE_ID" \
        --seed "$SEED" \
        --max-steps 3000 \
        --step-period-ms 20
) >"$RUN/shared-bridge.log" 2>&1 &
PIDS+=($!)
COMPONENTS+=(local_eaios)
FAILURE_OWNER=SUT_SYSTEM
FAILURE_COMPONENT=local_eaios
FAILURE_REASON=local_eaios_startup_failed
# ONLINE is published only after the child writes authoritative semantic evidence.
# Wait before MI freezes its one immutable grounding snapshot.
wait_http http://127.0.0.1:28100/v1/health 240 ONLINE
wait_http http://127.0.0.1:28102/v1/health 30 ONLINE

"$SERVER" 127.0.0.1:25060 "$RUN/controller.sqlite3" 127.0.0.1:28060 \
    127.0.0.1:28090 "$RUN/artifacts" >"$RUN/integration-server.log" 2>&1 &
PIDS+=($!)
COMPONENTS+=(controller)
FAILURE_COMPONENT=controller
FAILURE_REASON=controller_startup_failed
wait_http http://127.0.0.1:28060/healthz 30

"$NODE" "$RUN/node-a.toml" >"$RUN/node-a.log" 2>&1 &
PIDS+=($!)
COMPONENTS+=(node)
"$NODE" "$RUN/node-b.toml" >"$RUN/node-b.log" 2>&1 &
PIDS+=($!)
COMPONENTS+=(node)
FAILURE_COMPONENT=node
FAILURE_REASON=node_registration_failed
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
COMPONENTS+=(mission_service)
FAILURE_COMPONENT=mission_service
FAILURE_REASON=mission_service_startup_failed
wait_http http://127.0.0.1:8070/healthz 120
cd "$RUN"
INSTRUCTION=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["instruction"])' "$INPUT_JSON")
FAILURE_REASON=mission_ingress_failed
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
    # A Ready/Deferred Mission may outlive this observation budget. Only actual
    # process exit or persisted terminal failure evidence can attribute SUT failure.
    FAILURE_OWNER=NONE
    FAILURE_COMPONENT=""
    FAILURE_REASON=""
    wait_mission_terminal "$RUN/mission.json" 1800
    sleep 3
    curl -sf "http://127.0.0.1:28060/v1/missions/$MISSION_ID" -o "$RUN/mission.json" || true
fi
# Event evidence is collected only by the bounded, completeness-checked EXIT collector.
curl -sf http://127.0.0.1:28060/v1/execution-attempts -o "$RUN/execution-attempts.json" || true

# EXIT always collects observations, provenance and canonical B1 admission before cleanup.
