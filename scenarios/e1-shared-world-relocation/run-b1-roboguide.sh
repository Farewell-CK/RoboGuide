#!/usr/bin/env bash
# Select the reviewed relocation deployment; the common B1 runner alone submits MI input.
# A frozen workload must be supplied explicitly, never borrowed from the navigation scenario.
set -euo pipefail
SCENARIO="$(cd "$(dirname "$0")" && pwd)"
RUN="${1:?usage: run-b1-roboguide.sh <new-run-dir> <frozen-input-json>}"
INPUT="${2:?supply an explicit frozen relocation B1 input}"
export ROBOGUIDE_B1_SCENARIO="$SCENARIO"
exec bash "$SCENARIO/../e1-shared-world-episode-51/run-b1-roboguide.sh" "$RUN" "$INPUT"
