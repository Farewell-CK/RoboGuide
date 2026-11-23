$ErrorActionPreference = 'Stop'
Push-Location (Join-Path $PSScriptRoot '..')
try {
    & .\gradlew.bat :naviindoor:testDebugUnitTest :app:testDebugUnitTest :app:verifyNaviDebugApk -PswinlFast=true -ParchiveTest=true -PnaviIntegrationTest=true @args
    if ($LASTEXITCODE -ne 0) { throw 'Native NAVI verification/build failed' }
} finally { Pop-Location }
