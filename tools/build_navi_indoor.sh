#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"
bash gradlew :naviindoor:testDebugUnitTest :app:testDebugUnitTest :app:verifyNaviDebugApk -PswinlFast=true -ParchiveTest=true -PnaviIntegrationTest=true "$@"
