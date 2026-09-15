#!/usr/bin/env bash
# C0C-B1 post-fix revalidation driver (evaluation-only).
#
# Runs the FROZEN smoke evaluation/control_dry_run/c0c/commands/run-c0c3.sh
# twice from completely fresh runtime state (fresh controller sqlite, fresh
# node journals, fresh EAIOS logs), snapshots each run's evidence into
# postfix/run-N/, and then restores the pre-fix failure evidence at the
# original paths via git so the 390a1cc record stays intact.
# The frozen script itself is not modified in any way.
set -euo pipefail
source "$(dirname "$0")/../commands/common.sh"

HERE="$C0C_DIR/postfix"
mkdir -p "$HERE"
record_head "$HERE/head.txt"
{
    echo "baseline_sha=$(git -C "$REPO" rev-parse HEAD)"
    echo "server_binary=$SERVER_BIN"
    echo "node_binary=$NODE_BIN"
    echo "frozen_script=$C0C_DIR/commands/run-c0c3.sh (unmodified, md5=$(md5sum "$C0C_DIR/commands/run-c0c3.sh" | cut -d' ' -f1))"
    echo "common_helper_md5=$(md5sum "$C0C_DIR/commands/common.sh" | cut -d' ' -f1)"
    echo "fixture_md5=$(md5sum "$C0C_DIR/fixtures/c0c3-independent-parallel-plan-v0.7.json" | cut -d' ' -f1)"
} >> "$HERE/head.txt"

fresh_state() {
    # Removes every durable artifact the smoke run can touch.
    rm -rf "$C0C_DIR/run/c0c3" \
        "$C0C_DIR/node-configs/node-state/c0c3" \
        "$C0C_DIR/node-configs/artifact-cache/c0c3-a" \
        "$C0C_DIR/node-configs/artifact-cache/c0c3-b"
}

snapshot_evidence() {
    # Copies one finished run's evidence into postfix/run-N/.
    local dest="$1"
    mkdir -p "$dest/responses" "$dest/events" "$dest/logs"
    cp "$C0C_DIR"/responses/c0c3-* "$dest/responses/"
    cp "$C0C_DIR/events/c0c3-events.json" "$dest/events/"
    cp "$C0C_DIR"/logs/c0c3-* "$dest/logs/"
    cp -r "$C0C_DIR/run/c0c3" "$dest/run"
    cp -r "$C0C_DIR/node-configs/node-state/c0c3" "$dest/node-journals"
}

restore_prefix_evidence() {
    # Restores the committed 390a1cc failure evidence at the original paths
    # and removes untracked files the new run created there. Raw logs are
    # repo-gitignored (logs/ rule) and were never part of the committed
    # pre-fix evidence; the fatal signature is preserved verbatim in
    # FINDINGS-C0C.md, so logs are not restored here.
    rm -rf "$C0C_DIR/run/c0c3" "$C0C_DIR/node-configs/node-state/c0c3"
    git -C "$REPO" checkout -- \
        evaluation/control_dry_run/c0c/run/c0c3 \
        evaluation/control_dry_run/c0c/node-configs/node-state/c0c3 \
        evaluation/control_dry_run/c0c/responses \
        evaluation/control_dry_run/c0c/events
    rm -f "$C0C_DIR/events/c0c3-events.json" \
        "$C0C_DIR/responses/c0c3-execution-attempts.json" \
        "$C0C_DIR/responses/c0c3-scheduling-reservations.json"
}

for run_index in 1 2; do
    echo "=== C0-C3 post-fix fresh run $run_index ==="
    fresh_state
    bash "$C0C_DIR/commands/run-c0c3.sh" > "$HERE/run-$run_index-smoke-stdout.log" 2>&1
    echo "smoke exit=$?"
    snapshot_evidence "$HERE/run-$run_index"
    uv run python "$HERE/verify-run.py" "$HERE/run-$run_index" \
        > "$HERE/run-$run_index/verdict.json"
    cat "$HERE/run-$run_index/verdict.json"
    restore_prefix_evidence
done

echo "post-fix revalidation runs complete"
