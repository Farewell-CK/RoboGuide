#!/usr/bin/env bash
# C0-C1 exact operation HTTP smoke.
# Server + fake-EAIOS node advertising compute.infer@v1, then a real
# POST /v1/missions with the R2 n4 deterministic fixture.
set -euo pipefail
source "$(dirname "$0")/common.sh"

RUN="$C0C_DIR/run/c0c1"
mkdir -p "$RUN"
cd "$RUN"
record_head "$C0C_DIR/logs/c0c1-head.txt"

# 1. fake Local EAIOS on 28081
python3 "$C0C_DIR/fake-eaios/fake_eaios.py" --port 28081 --log-file "$RUN/eaios-infer.jsonl" \
    > "$C0C_DIR/logs/c0c1-eaios.log" 2>&1 &
PIDS+=($!)

# 2. production integration-server (fresh sqlite + artifact root)
"$SERVER_BIN" 127.0.0.1:25051 "$RUN/controller.sqlite3" 127.0.0.1:28080 127.0.0.1:28090 "$RUN/artifacts" \
    > "$C0C_DIR/logs/c0c1-server.log" 2>&1 &
PIDS+=($!)
wait_http_ok "http://127.0.0.1:28080/healthz"

# 3. production roboguide-node with the C0-C1 config
"$NODE_BIN" "$C0C_DIR/node-configs/node-infer-c0c1.toml" \
    > "$C0C_DIR/logs/c0c1-node-infer.log" 2>&1 &
PIDS+=($!)
wait_node_in_inventory "http://127.0.0.1:28080" "c0c-node-infer" 60
curl -sf "http://127.0.0.1:28080/v1/inventory" -o "$C0C_DIR/responses/c0c1-inventory.json"

# 4. real submission through the production HTTP entrance
FIXTURE="$C0C_DIR/fixtures/n4-edge-inference-plan-v0.7.json"
cp "$REPO/evaluation/control_dry_run/fixtures/n4-edge-inference-plan-v0.7.json" "$FIXTURE"
MISSION_ID=$(python3 -c 'import json;print(json.load(open("'"$FIXTURE"'"))["mission"]["id"])')
echo "mission_id=$MISSION_ID" >> "$C0C_DIR/logs/c0c1-head.txt"
curl -sS -X POST "http://127.0.0.1:28080/v1/missions" \
    -H 'Content-Type: application/json' --data-binary @"$FIXTURE" \
    -D "$C0C_DIR/responses/c0c1-post-headers.txt" \
    -o "$C0C_DIR/responses/c0c1-post-body.json" \
    -w '%{http_code}' > "$C0C_DIR/responses/c0c1-post-status.txt"

# 5. settle: wait until the task leaves the Ready/transient phase
wait_task_status_not "http://127.0.0.1:28080" "$MISSION_ID" "Ready" 90 \
    "$C0C_DIR/responses/c0c1-mission.json" || \
    curl -sf "http://127.0.0.1:28080/v1/missions/$MISSION_ID" -o "$C0C_DIR/responses/c0c1-mission.json"
sleep 2
curl -sf "http://127.0.0.1:28080/v1/missions/$MISSION_ID" -o "$C0C_DIR/responses/c0c1-mission.json"

# 6. observable authority evidence
curl -sf "http://127.0.0.1:28080/v1/events" -o "$C0C_DIR/events/c0c1-events.json"
curl -sf "http://127.0.0.1:28080/v1/execution-attempts" -o "$C0C_DIR/responses/c0c1-execution-attempts.json"
curl -sf "http://127.0.0.1:28080/v1/scheduling-reservations" -o "$C0C_DIR/responses/c0c1-scheduling-reservations.json"

echo "C0-C1 run complete"
