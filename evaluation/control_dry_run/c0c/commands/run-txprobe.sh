#!/usr/bin/env bash
# C0-C transaction-boundary probe.
# Verifies that failures before authority acceptance return 400/409 without
# swapping the candidate controller into the live one: the rejected mission
# must remain unknown (GET 404) and must leave no event evidence.
# The 503 checkpoint-persistence branch cannot be triggered without
# sabotaging storage and is source-verified only (server.rs save_checkpoint).
set -euo pipefail
source "$(dirname "$0")/common.sh"

RUN="$C0C_DIR/run/txprobe"
mkdir -p "$RUN"
cd "$RUN"
record_head "$C0C_DIR/logs/txprobe-head.txt"

python3 "$C0C_DIR/fake-eaios/fake_eaios.py" --port 28088 --log-file "$RUN/eaios-txprobe.jsonl" \
    > "$C0C_DIR/logs/txprobe-eaios.log" 2>&1 &
PIDS+=($!)
"$SERVER_BIN" 127.0.0.1:25054 "$RUN/controller.sqlite3" 127.0.0.1:28087 127.0.0.1:28097 "$RUN/artifacts" \
    > "$C0C_DIR/logs/txprobe-server.log" 2>&1 &
PIDS+=($!)
wait_http_ok "http://127.0.0.1:28087/healthz"
"$NODE_BIN" "$C0C_DIR/node-configs/node-txprobe.toml" \
    > "$C0C_DIR/logs/txprobe-node.log" 2>&1 &
PIDS+=($!)
wait_node_in_inventory "http://127.0.0.1:28087" "c0c-node-txprobe" 60

probe() {
    # POST one fixture, capture status/headers/body under a probe label.
    local label="$1" fixture="$2"
    curl -sS -X POST "http://127.0.0.1:28087/v1/missions" \
        -H 'Content-Type: application/json' --data-binary @"$fixture" \
        -D "$C0C_DIR/responses/txprobe-$label-headers.txt" \
        -o "$C0C_DIR/responses/txprobe-$label-body.json" \
        -w '%{http_code}' > "$C0C_DIR/responses/txprobe-$label-status.txt" || true
}

probe malformed "$C0C_DIR/fixtures/txprobe-malformed.json"
probe no-timing "$C0C_DIR/fixtures/txprobe-no-timing.json"
# Provider absence is a scheduling condition, not a semantic rejection: the
# no-candidate mission must be accepted (202) and keep its Task pending.
probe no-candidate "$C0C_DIR/fixtures/txprobe-no-candidate.json"
curl -s -o "$C0C_DIR/responses/txprobe-no-candidate-mission-body.json" \
    -w '%{http_code}' > "$C0C_DIR/responses/txprobe-no-candidate-mission-status.txt" \
    "http://127.0.0.1:28087/v1/missions/mission-c0c-txprobe-no-candidate"
# A decoded-but-non-executable relation must fail implementation preflight and
# reject the whole submission.
probe relation-409 "$C0C_DIR/fixtures/txprobe-relation-409.json"

# Rollback proof: the 409 mission must be unknown and leave no event trace.
curl -s -o "$C0C_DIR/responses/txprobe-rollback-mission-body.json" \
    -w '%{http_code}' > "$C0C_DIR/responses/txprobe-rollback-mission-status.txt" \
    "http://127.0.0.1:28087/v1/missions/mission-c0c-txprobe-relation409"
curl -sf "http://127.0.0.1:28087/v1/events" -o "$C0C_DIR/events/txprobe-events.json"
for rejected in mission-c0c-txprobe-relation409 mission-c0c-txprobe-no-timing; do
    if grep -q "$rejected" "$C0C_DIR/events/txprobe-events.json"; then
        echo "ROLLBACK VIOLATION: rejected mission $rejected left event evidence" >&2
        exit 1
    fi
done
if ! grep -q "mission-c0c-txprobe-no-candidate" "$C0C_DIR/events/txprobe-events.json"; then
    echo "UNEXPECTED: accepted no-candidate mission left no event evidence" >&2
    exit 1
fi

echo "txprobe run complete"
