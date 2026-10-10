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

SCRIPT_SCENARIO="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$SCRIPT_SCENARIO/../.." && pwd)"
SCENARIO="${ROBOGUIDE_B1_SCENARIO:-$SCRIPT_SCENARIO}"
SCENARIO="$(cd "$SCENARIO" && pwd)"
DEFAULT_EMOS_ROOT="$(dirname "$REPO")/emos-baseline"
EMOS_ROOT="${ROBOGUIDE_EMOS_ROOT:-$DEFAULT_EMOS_ROOT}"
HABITAT_ENV="${ROBOGUIDE_HABITAT_CONDA_ENV:-habitat}"
RUN="${1:?usage: run-b1-roboguide.sh <run-dir> [input-json]}"
INPUT_JSON="${2:-${ROBOGUIDE_B1_INPUT:-$SCENARIO/b1-input.json}}"
MISSION_CONFIG="${ROBOGUIDE_MISSION_CONFIG:-$SCENARIO/mission-config-b1.toml}"
SERVER="$REPO/target/debug/integration-server"
NODE="$REPO/target/debug/roboguide-node"
PIDS=()
AUX_PIDS=()
VIDEO_ARGS=()
LIVE_VIEW_ARGS=()
GOAL_REGION_ARGS=()
ROUTE_SUPPORT_CHECK_ARGS=()
PROGRESS_ARGS=()
RETENTION_ARGS=()
RELOCATION_ARGS=()
PREPARE_ONLY="${ROBOGUIDE_B1_PREPARE_ONLY:-0}"
case "$PREPARE_ONLY" in
    0|1) ;;
    *) echo "ROBOGUIDE_B1_PREPARE_ONLY must be 0 or 1" >&2; exit 1 ;;
esac
case "${ROBOGUIDE_B1_RETAIN_STOPPED_SESSION:-0}" in
    0) ;;
    1) RETENTION_ARGS=(--retain-stopped-session) ;;
    *) echo "ROBOGUIDE_B1_RETAIN_STOPPED_SESSION must be 0 or 1" >&2; exit 1 ;;
esac

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
    trap '' TERM INT
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
        --controller-endpoint "http://127.0.0.1:${CONTROLLER_PORT}" \
        --mission-endpoint "http://127.0.0.1:${MISSION_PORT}" \
        --request-id "$REQUEST_ID" --failure-owner "$owner" \
        --component "$component" --reason "$reason"
    local archive_code=$?
    for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
    for pid in "${AUX_PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
    if [[ "$archive_code" != 0 ]]; then
        echo "B1 evidence collection failed; no admission can be claimed" >&2
        exit "$archive_code"
    fi
    exit "$code"
}

stop_run() {
    # External observation interruption is harness evidence, never an invented SUT failure.
    FAILURE_OWNER=EXTERNAL_INFRA
    FAILURE_COMPONENT=harness
    FAILURE_REASON=runner_interrupted
    exit "$1"
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
    # Observe both exact configured Node identities, independent of the deployment's robot types.
    for _ in $(seq 1 120); do
        if curl -sf http://127.0.0.1:${CONTROLLER_PORT}/v1/inventory \
            | python3 -c '
import json, sys
nodes = json.load(sys.stdin)["nodes"]
raise SystemExit(0 if {sys.argv[1], sys.argv[2]} <= {n["node_id"] for n in nodes} else 1)
' "$NODE_A_ID" "$NODE_B_ID"; then
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
        curl -sf "http://127.0.0.1:${CONTROLLER_PORT}/v1/missions/$MISSION_ID" -o "$output" || true
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
# All endpoints are startup-owned; separate runs may use disjoint port sets.
PORT_ASSIGNMENTS="$(uv run --project "$REPO" python -m roboguide_eval.b1_ports)"
eval "$PORT_ASSIGNMENTS"
# Preserve existing archives and Harness-owned manifest/log files.
if [[ -e "$RUN/b1-input-used.json" || -e "$RUN/controller.sqlite3" ]]; then
    echo "refusing to overwrite an existing B1 run: $RUN" >&2
    exit 1
fi
cp "$INPUT_JSON" "$RUN/b1-input-used.json"
INPUT_JSON="$RUN/b1-input-used.json"
mkdir -p "$RUN/mpl" "$RUN/artifacts" "$RUN/evidence"
if [[ "${ROBOGUIDE_B1_EXECUTION_PROGRESS:-0}" == 1 ]]; then
    PROGRESS_ARGS=(--progress-directory "$RUN/evidence/execution-progress")
fi
# E1 keeps human-reviewable visual evidence for every run by default.  The
# generic Habitat adapter remains opt-in, and deployments may set this to 0
# only when a pre-registered paired protocol disables capture for both arms.
if [[ "${ROBOGUIDE_HABITAT_CAPTURE_VIDEO:-1}" == 1 ]]; then
    VIDEO_ARGS=(
        --video-path "$RUN/evidence/episode-video.mp4"
        --video-fps "${ROBOGUIDE_HABITAT_VIDEO_FPS:-30}"
    )
fi
if [[ "${ROBOGUIDE_B1_LIVE_VIEW:-0}" == 1 ]]; then
    LIVE_VIEW_ARGS=(
        --live-preview-path "$RUN/live/latest-frame.jpg"
        --live-preview-period-steps "${ROBOGUIDE_HABITAT_LIVE_PREVIEW_PERIOD_STEPS:-5}"
    )
fi
if [[ "${ROBOGUIDE_B1_GOAL_REGION_NAVIGATION:-0}" == 1 ]]; then
    GOAL_REGION_ARGS=(--goal-region-navigation)
fi
if [[ "${ROBOGUIDE_B1_STEP_AWARE_NAVMESH:-0}" == 1 ]]; then
    if [[ "${ROBOGUIDE_B1_GOAL_REGION_NAVIGATION:-0}" != 1 ]]; then
        echo "step-aware navmesh requires ROBOGUIDE_B1_GOAL_REGION_NAVIGATION=1" >&2
        exit 1
    fi
    GOAL_REGION_ARGS+=(--step-aware-navmesh)
fi
if [[ "${ROBOGUIDE_B1_SPATIAL_NAVIGATION_ARRIVAL:-0}" == 1 ]]; then
    if [[ "${ROBOGUIDE_B1_STEP_AWARE_NAVMESH:-0}" != 1 ]]; then
        echo "spatial navigation arrival requires ROBOGUIDE_B1_STEP_AWARE_NAVMESH=1" >&2
        exit 1
    fi
    GOAL_REGION_ARGS+=(--spatial-navigation-arrival)
fi
if [[ "${ROBOGUIDE_B1_GOAL_AWARE_NAVIGATION_ARRIVAL:-0}" == 1 ]]; then
    if [[ "${ROBOGUIDE_B1_SPATIAL_NAVIGATION_ARRIVAL:-0}" != 1 ]]; then
        echo "goal-aware arrival requires ROBOGUIDE_B1_SPATIAL_NAVIGATION_ARRIVAL=1" >&2
        exit 1
    fi
    GOAL_REGION_ARGS+=(--goal-aware-navigation-arrival)
fi
if [[ "${ROBOGUIDE_B1_RESET_ROUTE_SUPPORT:-0}" == 1 ]]; then
    if [[ "${ROBOGUIDE_B1_GOAL_REGION_NAVIGATION:-0}" != 1 ]]; then
        echo "reset route support requires ROBOGUIDE_B1_GOAL_REGION_NAVIGATION=1" >&2
        exit 1
    fi
    GOAL_REGION_ARGS+=(--reset-route-support)
    ROUTE_SUPPORT_CHECK_ARGS=(--require-route-support)
fi
if [[ "${ROBOGUIDE_B1_RESET_ROUTE_GEOMETRY:-0}" == 1 ]]; then
    if [[ "${ROBOGUIDE_B1_RESET_ROUTE_SUPPORT:-0}" != 1 ]]; then
        echo "reset route geometry requires ROBOGUIDE_B1_RESET_ROUTE_SUPPORT=1" >&2
        exit 1
    fi
    GOAL_REGION_ARGS+=(--reset-route-geometry)
    ROUTE_SUPPORT_CHECK_ARGS+=(--require-route-geometry)
fi
if [[ "$PREPARE_ONLY" == 0 ]]; then
    trap finish_run EXIT
    trap 'stop_run 143' TERM
    trap 'stop_run 130' INT
fi
INITIAL_PREFERENCES_PATH=""
INITIAL_PREFERENCES_SOURCE=""
INITIAL_SUPPORT_FLAG="${ROBOGUIDE_B1_INITIAL_SUPPORT_ASSESSMENT:-0}"
DEPLOYMENT_RECOVERY_ATTEMPTS="${ROBOGUIDE_B1_DEPLOYMENT_RECOVERY_ATTEMPTS:-0}"
case "$DEPLOYMENT_RECOVERY_ATTEMPTS" in
    0|1|2|3) ;;
    *) echo "deployment recovery attempts must be between 0 and 3" >&2; exit 1 ;;
esac
if [[ "$DEPLOYMENT_RECOVERY_ATTEMPTS" != 0 && "$INITIAL_SUPPORT_FLAG" != 1 ]]; then
    echo "deployment recovery requires initial support assessment" >&2
    exit 1
fi
if [[ "$INITIAL_SUPPORT_FLAG" != 0 && "$INITIAL_SUPPORT_FLAG" != 1 ]]; then
    echo "initial support assessment flag must be 0 or 1" >&2
    exit 1
fi
if [[ "$INITIAL_SUPPORT_FLAG" == 1 && ( \
    "${ROBOGUIDE_B1_INITIAL_CANDIDATE_PREFERENCES:-0}" != 1 || \
    "${ROBOGUIDE_B1_RESET_ROUTE_GEOMETRY:-0}" != 1 || \
    "${ROBOGUIDE_B1_SPATIAL_NAVIGATION_ARRIVAL:-0}" != 1 ) ]]; then
    echo "initial support assessment requires geometry, preferences and spatial arrival" >&2
    exit 1
fi
if [[ "${ROBOGUIDE_B1_INITIAL_CANDIDATE_PREFERENCES:-0}" == 1 \
    && "${ROBOGUIDE_B1_RESET_ROUTE_SUPPORT:-0}" != 1 ]]; then
    echo "initial candidate preferences require ROBOGUIDE_B1_RESET_ROUTE_SUPPORT=1" >&2
    exit 1
fi
# The workload (episode, seed, dataset identity) comes from the frozen B1
# input itself — never from a scenario-embedded episode. The extractor
# fails with a stable field-level reason before any component launches.
WORKLOAD="$(uv run --project "$REPO" python -m roboguide_eval.b1_workload "$INPUT_JSON")" \
    || { FAILURE_REASON=invalid_b1_workload; exit 1; }
EPISODE_ID="$(printf '%s\n' "$WORKLOAD" | sed -n 's/^episode_id=//p')"
SEED="$(printf '%s\n' "$WORKLOAD" | sed -n 's/^seed=//p')"
DEPLOYMENT_ARGS=()
if [[ -n "${ROBOGUIDE_B1_DEPLOYMENT:-}" ]]; then
    DEPLOYMENT_ARGS=(--declaration "$ROBOGUIDE_B1_DEPLOYMENT")
fi
DEPLOYMENT_ASSIGNMENTS="$(uv run --project "$REPO" python -m roboguide_eval.b1_deployment \
    --run "$RUN" --scenario "$SCENARIO" --emos-root "$EMOS_ROOT" "${DEPLOYMENT_ARGS[@]}")" \
    || { FAILURE_REASON=invalid_b1_deployment; exit 1; }
eval "$DEPLOYMENT_ASSIGNMENTS"
if [[ "$RELOCATION_ENABLED" == 1 && ( ${#GOAL_REGION_ARGS[@]} != 0 \
    || ${#RETENTION_ARGS[@]} != 0 ) ]]; then
    FAILURE_REASON=unsupported_relocation_profile_combination
    echo "$FAILURE_REASON" >&2
    exit 1
fi
uv run --project "$REPO" python -m roboguide_eval.b1_planning_source "$RUN" \
    || { FAILURE_REASON=planning_world_source_declaration_failed; exit 1; }
cp "$MISSION_CONFIG" "$RUN/mission-config-used.toml"
MISSION_CONFIG="$RUN/mission-config-used.toml"
uv run --project "$REPO" python -m roboguide_eval.b1_ports \
    --run "$RUN" --scenario "$SCENARIO" > "$RUN/deployment-ports.env"
PYTHONPATH="$REPO/integrations/habitat-local-eaios" uv run --project "$REPO" python -m \
    habitat_local_eaios.recovery_deployment \
    --node "$RUN/node-a.toml" --node "$RUN/node-b.toml" \
    --snapshot "$RUN/recovery-deployment.json" "${RETENTION_ARGS[@]}" \
    || { FAILURE_REASON=recovery_deployment_configuration_invalid; exit 1; }
PYTHONPATH="$REPO/integrations/habitat-local-eaios" uv run --project "$REPO" python -m \
    habitat_local_eaios.spatial_feasibility \
    --node-a "$RUN/node-a.toml" --node-b "$RUN/node-b.toml" \
    --output "$RUN/spatial-profile.json"
if [[ "$RELOCATION_ENABLED" == 1 ]]; then
    PYTHONPATH="$REPO/integrations/habitat-local-eaios" uv run --project "$REPO" python -m \
        habitat_local_eaios.relocation_deployment \
        --node "0=$RUN/node-a.toml" --node "1=$RUN/node-b.toml" \
        --output "$RUN/relocation-registration-profile.json" \
        || { FAILURE_REASON=relocation_registration_invalid; exit 1; }
    RELOCATION_ARGS=(--enable-relocation --relocation-profile "$RUN/relocation-registration-profile.json")
    if [[ "$RELOCATION_COMPLETION_BINDING" == 1 ]]; then
        RELOCATION_ARGS+=(--relocation-completion-binding)
    fi
fi
if [[ "$INITIAL_SUPPORT_FLAG" == 1 ]]; then
    sed -i 's/^controller_preflight_enabled = false$/controller_preflight_enabled = true/' \
        "$RUN/mission-service-b1.toml"
fi
sed -i "s/^max_deployment_recovery_attempts = 0$/max_deployment_recovery_attempts = $DEPLOYMENT_RECOVERY_ATTEMPTS/" \
    "$RUN/mission-service-b1.toml"

# This offline boundary prepares the real command's files and exits without
# touching ports, starting a process, reading credentials or submitting MI.
if [[ "$PREPARE_ONLY" == 1 ]]; then
    echo "B1 deployment prepared; no SUT component started: $RUN"
    exit 0
fi
if [[ ! -x "$SERVER" || ! -x "$NODE" ]]; then
    FAILURE_REASON=required_sut_binary_missing
    exit 1
fi
for n in a b; do
    "$NODE" --validate "$RUN/node-$n.toml" > "$RUN/node-conformance-$n.json" \
        || { FAILURE_REASON=node_configuration_invalid; exit 1; }
done

clean_port "${CONTROLLER_GRPC_PORT}"
clean_port "${CONTROLLER_PORT}"
clean_port "${ARTIFACT_PORT}"
clean_port "${HABITAT_PORT}"
clean_port "${HABITAT_PORT_B}"
clean_port "${MISSION_PORT}"
if [[ "${ROBOGUIDE_B1_LIVE_VIEW:-0}" == 1 ]]; then
    LIVE_VIEW_PORT="${ROBOGUIDE_B1_LIVE_VIEW_PORT:-28110}"
    clean_port "$LIVE_VIEW_PORT"
    uv run --project "$REPO" python -m roboguide_eval.b1_live_view \
        --run-dir "$RUN" --port "$LIVE_VIEW_PORT" >"$RUN/live-view.log" 2>&1 &
    AUX_PIDS+=($!)
    wait_http "http://127.0.0.1:$LIVE_VIEW_PORT/healthz" 30
    echo "B1 live operator view: http://127.0.0.1:$LIVE_VIEW_PORT/" >&2
fi
HABITAT_PYTHON="$(conda run -n "$HABITAT_ENV" which python)"
(
    cd "$EMOS_ROOT"
    exec env \
        CUDA_VISIBLE_DEVICES="${ROBOGUIDE_HABITAT_CUDA_DEVICE:-1}" \
        HABITAT_SIM_LOG=quiet MAGNUM_LOG=quiet MPLCONFIGDIR="$RUN/mpl" \
        PYTHONPATH="$REPO/integrations/habitat-local-eaios:$EMOS_ROOT/habitat-lab:$EMOS_ROOT/habitat-baselines:$EMOS_ROOT/habitat-mas" \
        "$HABITAT_PYTHON" -u -m habitat_local_eaios \
        --port "${HABITAT_PORT}" \
        --backend shared-emos-stage2 \
        "${PROGRESS_ARGS[@]}" \
        "${GOAL_REGION_ARGS[@]}" \
        "${RETENTION_ARGS[@]}" \
        "${RELOCATION_ARGS[@]}" \
        --subtask-mode natural-objective \
        --port-b "${HABITAT_PORT_B}" \
        --state-db "$RUN/bridge-a.sqlite3" \
        --state-db-b "$RUN/bridge-b.sqlite3" \
        --agent-id 0 \
        --agent-b-id 1 \
        --pair-wait-s 1200 \
        --evidence-dir "$RUN/evidence" \
        --spatial-profile "$RUN/spatial-profile.json" \
        --run-id "$(basename "$RUN")" \
        --habitat-config "$HABITAT_CONFIG" \
        --episode-id "$EPISODE_ID" \
        --seed "$SEED" \
        --max-steps "$MAX_STEPS" \
        --step-period-ms 20 \
        "${VIDEO_ARGS[@]}" \
        "${LIVE_VIEW_ARGS[@]}"
) >"$RUN/shared-bridge.log" 2>&1 &
PIDS+=($!)
COMPONENTS+=(local_eaios)
FAILURE_OWNER=SUT_SYSTEM
FAILURE_COMPONENT=local_eaios
FAILURE_REASON=local_eaios_startup_failed
# ONLINE is published only after the child writes authoritative semantic evidence.
# Wait before MI freezes its one immutable grounding snapshot.
wait_http http://127.0.0.1:${HABITAT_PORT}/v1/health 240 ONLINE
wait_http http://127.0.0.1:${HABITAT_PORT_B}/v1/health 30 ONLINE
PYTHONPATH="$REPO/integrations/habitat-local-eaios" uv run --project "$REPO" python -m \
    habitat_local_eaios.recovery_deployment \
    --snapshot "$RUN/recovery-deployment.json" --verify-live \
    || { FAILURE_OWNER=EXTERNAL_INFRA; FAILURE_COMPONENT=environment; \
         FAILURE_REASON=recovery_deployment_support_mismatch; exit 1; }
uv run --project "$REPO" python -m roboguide_eval.b1_planning_source \
    "$RUN" --check-artifact \
    || { FAILURE_REASON=planning_world_evidence_unavailable; exit 1; }
PYTHONPATH="$REPO/integrations/habitat-local-eaios" uv run --project "$REPO" python -m \
    habitat_local_eaios.spatial_feasibility \
    --node-a "$RUN/node-a.toml" --node-b "$RUN/node-b.toml" \
    --output "$RUN/spatial-profile.json" --verify-sources \
    || { FAILURE_REASON=spatial_profile_source_mismatch; exit 1; }
if [[ "$RELOCATION_ENABLED" == 1 ]]; then
    PYTHONPATH="$REPO/integrations/habitat-local-eaios" uv run --project "$REPO" python -m \
        habitat_local_eaios.relocation_preflight --run "$RUN" \
        || { FAILURE_OWNER=EXTERNAL_INFRA; FAILURE_COMPONENT=harness; \
             FAILURE_REASON=relocation_reset_evidence_invalid; exit 1; }
fi
uv run --project "$REPO" python -m roboguide_eval.b1_deployment_feasibility "$RUN" \
    || { FAILURE_REASON=preassignment_feasibility_unavailable; exit 1; }
if [[ "${#ROUTE_SUPPORT_CHECK_ARGS[@]}" != 0 ]]; then
    uv run --project "$REPO" python -m roboguide_eval.b1_deployment_feasibility \
        "$RUN" "${ROUTE_SUPPORT_CHECK_ARGS[@]}" \
        || { FAILURE_OWNER=EXTERNAL_INFRA; FAILURE_COMPONENT=harness; \
             FAILURE_REASON=reset_route_support_archive_invalid; exit 1; }
fi

if [[ "${ROBOGUIDE_B1_INITIAL_CANDIDATE_PREFERENCES:-0}" == 1 ]]; then
    INITIAL_PREFERENCES_PATH="$RUN/evidence/initial-operation-preferences.json"
    INITIAL_PREFERENCES_SOURCE="$RUN/evidence/reset-route-support.json"
    PYTHONPATH="$REPO/integrations/habitat-local-eaios" python3 -m \
        habitat_local_eaios.initial_operation_preferences \
        --route-support "$RUN/evidence/reset-route-support.json" \
        --feasibility "$RUN/evidence/preassignment-feasibility.json" \
        --output "$INITIAL_PREFERENCES_PATH" \
        || { FAILURE_OWNER=EXTERNAL_INFRA; FAILURE_COMPONENT=harness; \
             FAILURE_REASON=initial_operation_preferences_invalid; exit 1; }
fi

ROBOGUIDE_INITIAL_OPERATION_PREFERENCES_PATH="$INITIAL_PREFERENCES_PATH" \
ROBOGUIDE_INITIAL_OPERATION_ASSESSMENT="$INITIAL_SUPPORT_FLAG" \
ROBOGUIDE_INITIAL_OPERATION_PREFERENCES_SOURCE_PATH="$INITIAL_PREFERENCES_SOURCE" \
ROBOGUIDE_DEPLOYMENT_FEASIBILITY_PATH="$RUN/evidence/preassignment-feasibility.json" \
ROBOGUIDE_TASK_VERIFIER_SOURCE_PATH="$RUN/evidence/task-verifier-source.json" \
ROBOGUIDE_TASK_VERIFIER_VERDICT_PATH="$RUN/evidence/task-verifier-verdict.json" \
"$SERVER" 127.0.0.1:${CONTROLLER_GRPC_PORT} "$RUN/controller.sqlite3" 127.0.0.1:${CONTROLLER_PORT} \
    127.0.0.1:${ARTIFACT_PORT} "$RUN/artifacts" >"$RUN/integration-server.log" 2>&1 &
PIDS+=($!)
COMPONENTS+=(controller)
FAILURE_COMPONENT=controller
FAILURE_REASON=controller_startup_failed
wait_http http://127.0.0.1:${CONTROLLER_PORT}/healthz 30

"$NODE" "$RUN/node-a.toml" >"$RUN/node-a.log" 2>&1 &
PIDS+=($!)
COMPONENTS+=(node)
"$NODE" "$RUN/node-b.toml" >"$RUN/node-b.log" 2>&1 &
PIDS+=($!)
COMPONENTS+=(node)
FAILURE_COMPONENT=node
FAILURE_REASON=node_registration_failed
wait_nodes
curl -sf http://127.0.0.1:${CONTROLLER_PORT}/v1/inventory -o "$RUN/inventory.json"

# Production Mission Intelligence ingress (no static plan anywhere in this path).
cd "$REPO"
# The frozen relay endpoint is plain HTTP on a remote host; the MI provider
# config requires this explicit opt-out (same relay the EMOS arm uses).
ROBOGUIDE_ALLOW_INSECURE_LLM_HTTP=1 uv run python "$REPO/apps/mission-service/main.py" \
    --mission-config "$MISSION_CONFIG" \
    --service-config "$RUN/mission-service-b1.toml" \
    --repository-root "$REPO" >"$RUN/mission-service.log" 2>&1 &
PIDS+=($!)
COMPONENTS+=(mission_service)
FAILURE_COMPONENT=mission_service
FAILURE_REASON=mission_service_startup_failed
wait_http http://127.0.0.1:${MISSION_PORT}/healthz 120
cd "$RUN"
INSTRUCTION=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["instruction"])' "$INPUT_JSON")
FAILURE_REASON=mission_ingress_failed
SUBMITTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "submission_start_utc=$SUBMITTED_AT" > "$RUN/b1-timing.txt"

# One frozen observation budget covers the synchronous POST and any polling.
# Mission Service executes the whole MI chain before its POST returns, so
# this client bound — not the server's per-call timeouts — decides when the
# runner stops observing. The value is a deployment-chosen experiment
# boundary (default 1800s; the MI worst-case derivation is archived beside
# it for reasoning, never used as the wait). Frozen before submission.
MI_OBSERVATION_BUDGET_SECONDS="${ROBOGUIDE_B1_MI_OBSERVATION_BUDGET_SECONDS:-1800}"
MI_WAIT_BUDGET_JSON="$(uv run --project "$REPO" python -m roboguide_eval.b1_runner_wait \
    --budget-only --mission-config "$MISSION_CONFIG" \
    --service-config "$RUN/mission-service-b1.toml")" \
    || { FAILURE_REASON=mi_wait_budget_derivation_failed; exit 1; }
MI_WAIT_BUDGET_SECONDS=$(printf '%s\n' "$MI_WAIT_BUDGET_JSON" \
    | python3 -c 'import json,sys;print(json.load(sys.stdin)["total_seconds"])')
python3 - "$MI_OBSERVATION_BUDGET_SECONDS" "$MI_WAIT_BUDGET_JSON" <<'PYEOF'
import json, sys
deployment_budget = {
    "schema_version": "roboguide.e1.mi-observation-budget/v0.1",
    "observation_budget_seconds": float(sys.argv[1]),
    "basis": "deployment-chosen experiment observation boundary; not the MI "
    "theoretical worst-case and not a claim about MI execution time",
    "mi_worst_case_derivation": json.loads(sys.argv[2]),
}
with open("mi-wait-budget.json", "w", encoding="utf-8") as output:
    json.dump(deployment_budget, output, ensure_ascii=False, indent=2, sort_keys=True)
    output.write("\n")
PYEOF

# Submit and observe under the one budget. A POST client timeout never
# fails MI: this run's request store recovers the identity and observation
# continues; the runner never resubmits the instruction.
MI_PID="${PIDS[${#PIDS[@]}-1]}"
uv run --project "$REPO" python -m roboguide_eval.b1_runner_wait \
    --submit-and-wait --endpoint http://127.0.0.1:${MISSION_PORT} \
    --instruction "$INSTRUCTION" \
    --budget-seconds "$MI_OBSERVATION_BUDGET_SECONDS" \
    --service-pid "$MI_PID" --store-path "$RUN/mission-service.sqlite3" \
    --log-path "$RUN/mi-wait-log.jsonl" --poll-interval-seconds 2 \
    > "$RUN/mi-wait-outcome.json" &
MI_OBSERVER_PID=$!
AUX_PIDS+=("$MI_OBSERVER_PID")
# Bash's builtin wait is interruptible, so TERM can archive while services stay alive.
# The client still performs exactly one POST and retains its original request identity.
wait "$MI_OBSERVER_PID" || MI_WAIT_EXIT=$?
# A reaped observer no longer belongs in cleanup; its PID may be reused during execution.
unset "AUX_PIDS[$((${#AUX_PIDS[@]}-1))]"
MI_WAIT_JSON=$(cat "$RUN/mi-wait-outcome.json")
MI_OUTCOME=$(printf '%s\n' "$MI_WAIT_JSON" \
    | python3 -c 'import json,sys;print(json.load(sys.stdin)["outcome"])' 2>/dev/null || echo invalid_response)
LIFECYCLE=$(printf '%s\n' "$MI_WAIT_JSON" \
    | python3 -c 'import json,sys;print(json.load(sys.stdin)["lifecycle"])' 2>/dev/null || echo unknown)
REQUEST_ID=$(printf '%s\n' "$MI_WAIT_JSON" \
    | python3 -c 'import json,sys;print(json.load(sys.stdin)["request_id"])' 2>/dev/null || echo "")
if [[ -n "$REQUEST_ID" ]]; then
    echo "request_id=$REQUEST_ID" >> "$RUN/b1-timing.txt"
    curl -sf "http://127.0.0.1:${MISSION_PORT}/v1/mission-requests/$REQUEST_ID" \
        -o "$RUN/b1-request-record.json" || true
    MISSION_ID=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["mission_id"])' \
        "$RUN/b1-request-record.json" 2>/dev/null || echo "")
else
    echo "request_id=unavailable" >> "$RUN/b1-timing.txt"
    MISSION_ID=""
fi
echo "lifecycle=$LIFECYCLE" >> "$RUN/b1-timing.txt"
echo "mi_wait_outcome=$MI_OUTCOME" >> "$RUN/b1-timing.txt"

case "$MI_OUTCOME" in
    accepted)
        # MI submitted Control; keep the existing mission observation budget,
        # starting from this moment, with its own attribution semantics.
        FAILURE_OWNER=NONE
        FAILURE_COMPONENT=""
        FAILURE_REASON=""
        wait_mission_terminal "$RUN/mission.json" "$MISSION_OBSERVATION_BUDGET_SECONDS"
        sleep 3
        curl -sf "http://127.0.0.1:${CONTROLLER_PORT}/v1/missions/$MISSION_ID" -o "$RUN/mission.json" || true
        ;;
    mi_terminal|awaiting_interaction)
        # Real MI terminal or interaction-required states are archived as
        # themselves; the run result reflects MI's own state and evidence.
        ;;
    observation_timeout)
        # Runner observation timeout is external infra evidence, never a
        # fabricated MI failure; collection proceeds before any cleanup.
        FAILURE_OWNER=EXTERNAL_INFRA
        FAILURE_COMPONENT=harness
        FAILURE_REASON=runner_mi_observation_timeout
        ;;
    request_id_unavailable)
        # The synchronous POST exceeded the client budget before any
        # response and no persisted identity matched this instruction.
        # The server may still be executing; nothing is resubmitted and
        # no MI failure is invented.
        FAILURE_OWNER=EXTERNAL_INFRA
        FAILURE_COMPONENT=harness
        FAILURE_REASON=runner_post_observation_timeout_request_id_unavailable
        ;;
    http_unusable)
        FAILURE_OWNER=SUT_SYSTEM
        FAILURE_COMPONENT=mission_service
        FAILURE_REASON=mission_request_submit_http_unusable
        ;;
    service_exited)
        FAILURE_OWNER=SUT_SYSTEM
        FAILURE_COMPONENT=mission_service
        FAILURE_REASON=mission_service_process_exited_before_terminal_lifecycle
        ;;
    *)
        # Unknown outcome or malformed response: fail closed with the raw
        # observed value retained in mi-wait-outcome.json.
        FAILURE_OWNER=SUT_SYSTEM
        FAILURE_COMPONENT=mission_service
        FAILURE_REASON="mission_request_unusable_response:$LIFECYCLE"
        ;;
esac
# Event evidence is collected only by the bounded, completeness-checked EXIT collector.
curl -sf http://127.0.0.1:${CONTROLLER_PORT}/v1/execution-attempts -o "$RUN/execution-attempts.json" || true

# EXIT always collects observations, provenance and canonical B1 admission before cleanup.
