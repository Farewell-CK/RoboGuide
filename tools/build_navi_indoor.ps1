$ErrorActionPreference = 'Stop'
Push-Location (Join-Path $PSScriptRoot '../navi_indoor')
try {
    & flutter pub get
    if ($LASTEXITCODE -ne 0) { throw 'Flutter dependency resolution failed' }
    & python (Join-Path $PSScriptRoot 'prepare_navi_android.py')
    if ($LASTEXITCODE -ne 0) { throw 'Flutter Android preparation failed' }
    & flutter analyze --no-fatal-infos --no-fatal-warnings
    if ($LASTEXITCODE -ne 0) { throw 'NAVI analysis failed' }
    & flutter test --reporter expanded
    if ($LASTEXITCODE -ne 0) { throw 'NAVI tests failed' }
    & flutter build aar --no-debug --no-profile --target-platform android-arm64 --build-number 1.0
    if ($LASTEXITCODE -ne 0) { throw 'NAVI AAR build failed' }
} finally { Pop-Location }
