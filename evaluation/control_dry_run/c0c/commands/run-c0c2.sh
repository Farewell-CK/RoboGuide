#!/usr/bin/env bash
# C0-C2 future timing HTTP smoke.
# Server + fake-EAIOS node advertising mobility.move@v1, then a real
# POST /v1/missions with the R2 t2 fixture keeping the untouched
# 3600000ms start window. The run never waits for activation; it only
# captures immediate scheduling-reservation evidence.
set -euo pipefail
source "$(dirname "$0")/common.sh"

RUN="$C0C_DIR/run/c0c2"
mkdir -p "$RUN"
cd "$RUN"
record_head "$C0C_DIR/logs/c0c2-head.txt"

python3 "$C0C_DIR/fake-eaios/fake_eaios.py" --port 28083 --log-file "$RUN/eaios-move.jsonl" \
    > "$C0C_DIR/logs/c0c2-eaios.log" 2>&1 &
PIDS+=($!)

"$SERVER_BIN" 127.0.0.1:25052 "$RUN/controller.sqlite3" 127.0.0.1:28082 127.0.0.1:28092 "$RUN/artifacts" \
    > "$C0C_DIR/logs/c0c2-server.log" 2>&1 &
PIDS+=($!)
wait_http_ok "http://127.0.0.1:28082/healthz"

"$NODE_BIN" "$C0C_DIR/node-configs/node-move-c0c2.toml" \
    > "$C0C_DIR/logs/c0c2-node-move.log" 2>&1 &
PIDS+=($!)
wait_node_in_inventory "http://127.0.0.1:28082" "c0c-node-move" 60
curl -sf "http://127.0.0.1:28082/v1/inventory" -o "$C0C_DIR/responses/c0c2-inventory.json"

FIXTURE="$C0C_DIR/fixtures/t2-start-window-plan-v0.7.json"
cp "$REPO/evaluation/control_dry_run/fixtures/t2-start-window-plan-v0.7.json" "$FIXTURE"
# Guard: the pristine 3600000ms window must be preserved before submission.
python3 - "$FIXTURE" <<'PYEOF'
import json, sys
timing = json.load(open(sys.argv[1]))["tasks"][0]["timing"]
assert timing["earliest_start_offset_ms"] == 3_600_000, timing
assert timing["latest_start_offset_ms"] == 3_600_000, timing
print("t2 timing guard passed: 3600000/3600000 preserved")
PYEOF
MISSION_ID=$(python3 -c 'import json;print(json.load(open("'"$FIXTURE"'"))["mission"]["id"])')
echo "mission_id=$MISSION_ID" >> "$C0C_DIR/logs/c0c2-head.txt"
curl -sS -X POST "http://127.0.0.1:28082/v1/missions" \
    -H 'Content-Type: application/json' --data-binary @"$FIXTURE" \
    -D "$C0C_DIR/responses/c0c2-post-headers.txt" \
    -o "$C0C_DIR/responses/c0c2-post-body.json" \
    -w '%{http_code}' > "$C0C_DIR/responses/c0c2-post-status.txt"

# Immediate evidence only; the run never advances the clock.
sleep 2
curl -sf "http://127.0.0.1:28082/v1/missions/$MISSION_ID" -o "$C0C_DIR/responses/c0c2-mission.json"
curl -sf "http://127.0.0.1:28082/v1/scheduling-reservations" -o "$C0C_DIR/responses/c0c2-scheduling-reservations.json"
curl -sf "http://127.0.0.1:28082/v1/execution-attempts" -o "$C0C_DIR/responses/c0c2-execution-attempts.json"
curl -sf "http://127.0.0.1:28082/v1/events" -o "$C0C_DIR/events/c0c2-events.json"

echo "C0-C2 run complete"
