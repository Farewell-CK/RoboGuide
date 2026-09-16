#!/usr/bin/env bash
# E1-I Pilot pair runner: one paired episode through the unified harness contract.
# The RoboGuide arm's B1 input JSON is injected via $ROBOGUIDE_B1_INPUT (resolved by
# the harness from the spawning shell; never committed).
# Usage: run-pair.sh <episode-id> <seed> <input-json-abs> <results-root> [first-arm]
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
EPISODE="${1:?usage: run-pair.sh <episode-id> <seed> <input-json> <results-root> [first-arm]}"
SEED="${2:?missing seed}"
INPUT="$(cd "$(dirname "$3")" && pwd)/$(basename "$3")"
RESULTS="${4:?missing results root}"
FIRST="${5:-emos}"
export ROBOGUIDE_B1_INPUT="$INPUT"

cd "$REPO"
set -a; source /data/workspace/code/emos-baseline/emos.env; set +a

run_arm() {
    # Run one arm through the unified harness; identity is verified afterwards.
    local system="$1" seed="$2"
    uv run roboguide-eval run \
        --spec evaluation/specs/e1/mobility-smoke.yaml \
        --system "$system" \
        --seed "$seed" \
        --results "$RESULTS" 2>&1 | tail -2
}

if [[ "$FIRST" == "roboguide" ]]; then
    run_arm roboguide "$SEED"
    run_arm emos "$SEED"
else
    run_arm emos "$SEED"
    run_arm roboguide "$SEED"
fi
