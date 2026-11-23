#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root/navi_indoor"
flutter pub get
python3 "$root/tools/prepare_navi_android.py"
flutter analyze --no-fatal-infos --no-fatal-warnings
flutter test --reporter expanded
flutter build aar --no-debug --no-profile --target-platform android-arm64 --build-number 1.0
printf '%s\n' 'NAVI AAR ready. Build the host with ./gradlew :app:verifyNaviDebugApk -PswinlFast=true -ParchiveTest=true -PnaviIntegrationTest=true'
