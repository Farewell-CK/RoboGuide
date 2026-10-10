#!/usr/bin/env bash
# Explicit deployment selection; production MI still generates the only submitted plan.
set -euo pipefail
SCENARIO="$(cd "$(dirname "$0")" && pwd)"
export ROBOGUIDE_B1_SCENARIO="$SCENARIO"
exec bash "$SCENARIO/../e1-shared-world-episode-51/run-b1-roboguide.sh" "$@"
