#!/usr/bin/env bash
# C0-C3 same-mission independent parallel smoke.
# One server hosting two fake-EAIOS nodes (c0c-node-a: compute.infer@v1,
# c0c-node-b: mobility.move@v1), then one real POST /v1/missions whose
# plan carries two depends_on-free tasks. Both tasks must bind to their
# own node inside the single mission-level Execution Group.
set -euo pipefail
source "$(dirname "$0")/common.sh"

RUN="$C0C_DIR/run/c0c3"
mkdir -p "$RUN"
cd "$RUN"
record_head "$C0C_DIR/logs/c0c3-head.txt"

python3 "$C0C_DIR/fake-eaios/fake_eaios.py" --port 28085 --log-file "$RUN/eaios-node-a.jsonl" \
    > "$C0C_DIR/logs/c0c3-eaios-a.log" 2>&1 &
PIDS+=($!)
python3 "$C0C_DIR/fake-eaios/fake_eaios.py" --port 28086 --log-file "$RUN/eaios-node-b.jsonl" \
    > "$C0C_DIR/logs/c0c3-eaios-b.log" 2>&1 &
PIDS+=($!)

"$SERVER_BIN" 127.0.0.1:25053 "$RUN/controller.sqlite3" 127.0.0.1:28084 127.0.0.1:28094 "$RUN/artifacts" \
    > "$C0C_DIR/logs/c0c3-server.log" 2>&1 &
PIDS+=($!)
wait_http_ok "http://127.0.0.1:28084/healthz"

"$NODE_BIN" "$C0C_DIR/node-configs/node-a-c0c3.toml" \
    > "$C0C_DIR/logs/c0c3-node-a.log" 2>&1 &
PIDS+=($!)
"$NODE_BIN" "$C0C_DIR/node-configs/node-b-c0c3.toml" \
    > "$C0C_DIR/logs/c0c3-node-b.log" 2>&1 &
PIDS+=($!)
wait_node_in_inventory "http://127.0.0.1:28084" "c0c-node-a" 60
wait_node_in_inventory "http://127.0.0.1:28084" "c0c-node-b" 60
curl -sf "http://127.0.0.1:28084/v1/inventory" -o "$C0C_DIR/responses/c0c3-inventory.json"

FIXTURE="$C0C_DIR/fixtures/c0c3-independent-parallel-plan-v0.7.json"
MISSION_ID=$(python3 -c 'import json;print(json.load(open("'"$FIXTURE"'"))["mission"]["id"])')
echo "mission_id=$MISSION_ID" >> "$C0C_DIR/logs/c0c3-head.txt"
curl -sS -X POST "http://127.0.0.1:28084/v1/missions" \
    -H 'Content-Type: application/json' --data-binary @"$FIXTURE" \
    -D "$C0C_DIR/responses/c0c3-post-headers.txt" \
    -o "$C0C_DIR/responses/c0c3-post-body.json" \
    -w '%{http_code}' > "$C0C_DIR/responses/c0c3-post-status.txt"

wait_task_status_not "http://127.0.0.1:28084" "$MISSION_ID" "Ready" 120 \
    "$C0C_DIR/responses/c0c3-mission.json" || \
    curl -sf "http://127.0.0.1:28084/v1/missions/$MISSION_ID" -o "$C0C_DIR/responses/c0c3-mission.json"
sleep 3
curl -sf "http://127.0.0.1:28084/v1/missions/$MISSION_ID" -o "$C0C_DIR/responses/c0c3-mission.json"

curl -sf "http://127.0.0.1:28084/v1/events" -o "$C0C_DIR/events/c0c3-events.json"
curl -sf "http://127.0.0.1:28084/v1/execution-attempts" -o "$C0C_DIR/responses/c0c3-execution-attempts.json"
curl -sf "http://127.0.0.1:28084/v1/scheduling-reservations" -o "$C0C_DIR/responses/c0c3-scheduling-reservations.json"

echo "C0-C3 run complete"
