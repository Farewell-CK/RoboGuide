param(
    [string]$AndroidSdk = $env:ANDROID_HOME,
    [string]$AndroidNdk,
    [string]$AndroidCmake,
    [string]$Abi = "arm64-v8a",
    [int]$ApiLevel = 24
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$dependencyRoot = Join-Path $projectRoot "third_party\vins_android_deps"
$downloadRoot = Join-Path $dependencyRoot "downloads"
$sourceRoot = Join-Path $dependencyRoot "sources"
New-Item -ItemType Directory -Force $downloadRoot, $sourceRoot | Out-Null

if (-not $AndroidSdk) {
    $AndroidSdk = Join-Path $env:LOCALAPPDATA "Android\Sdk"
}
if (-not $AndroidNdk) {
    $AndroidNdk = (Get-ChildItem (Join-Path $AndroidSdk "ndk") -Directory |
        Sort-Object Name -Descending | Select-Object -First 1).FullName
}
if (-not $AndroidCmake) {
    $AndroidCmake = (Get-ChildItem (Join-Path $AndroidSdk "cmake") -Directory |
        Sort-Object Name -Descending | Select-Object -First 1).FullName
}
if (-not (Test-Path -LiteralPath $AndroidNdk) -or
        -not (Test-Path -LiteralPath $AndroidCmake)) {
    throw "Android NDK/CMake was not found under $AndroidSdk"
}
$cmakeExe = Join-Path $AndroidCmake "bin\cmake.exe"
$ninjaExe = Join-Path $AndroidCmake "bin\ninja.exe"
$toolchain = Join-Path $AndroidNdk "build\cmake\android.toolchain.cmake"
function Convert-ToCMakePath([string]$Path) {
    return $Path.Replace('\', '/')
}

function Get-Archive([string]$Url, [string]$Destination) {
    if (-not (Test-Path $Destination)) {
        try {
            Invoke-WebRequest -Uri $Url -OutFile $Destination
        } catch {
            Remove-Item -LiteralPath $Destination -Force -ErrorAction SilentlyContinue
            $curl = Join-Path $env:SystemRoot "System32\curl.exe"
            if (-not (Test-Path -LiteralPath $curl)) { throw }
            & $curl --fail --location --retry 3 --output $Destination $Url
            if ($LASTEXITCODE -ne 0) {
                Remove-Item -LiteralPath $Destination -Force -ErrorAction SilentlyContinue
                throw "Download failed: $Url"
            }
        }
    }
}

function Expand-ZipArchive([string]$Archive, [string]$Destination) {
    $tar = Join-Path $env:SystemRoot "System32\tar.exe"
    if (-not (Test-Path -LiteralPath $tar)) {
        Expand-Archive $Archive $Destination -Force
        return
    }
    New-Item -ItemType Directory -Force $Destination | Out-Null
    & $tar -xf $Archive -C $Destination
    if ($LASTEXITCODE -ne 0) {
        throw "Archive extraction failed: $Archive"
    }
}

function Move-ExtractedDirectory([string]$Source, [string]$Destination) {
    [System.IO.Directory]::Move($Source, $Destination)
}

$eigenArchive = Join-Path $downloadRoot "eigen-3.3.7.zip"
$ceresArchive = Join-Path $downloadRoot "ceres-1.14.0.zip"
$opencvArchive = Join-Path $downloadRoot "opencv-4.5.5-android-sdk.zip"
$boostArchive = Join-Path $downloadRoot "boost-1.69.0.zip"
Get-Archive "https://gitlab.com/libeigen/eigen/-/archive/3.3.7/eigen-3.3.7.zip" $eigenArchive
Get-Archive "https://github.com/ceres-solver/ceres-solver/archive/refs/tags/1.14.0.zip" $ceresArchive
Get-Archive "https://github.com/opencv/opencv/releases/download/4.5.5/opencv-4.5.5-android-sdk.zip" $opencvArchive
Get-Archive "https://archives.boost.io/release/1.69.0/source/boost_1_69_0.zip" $boostArchive

if (-not (Test-Path (Join-Path $dependencyRoot "eigen3\Eigen\Core"))) {
    Expand-ZipArchive $eigenArchive $sourceRoot
    Move-ExtractedDirectory (Join-Path $sourceRoot "eigen-3.3.7") (Join-Path $dependencyRoot "eigen3")
}
if (-not (Test-Path (Join-Path $dependencyRoot "opencv\sdk\native\jni\OpenCVConfig.cmake"))) {
    Expand-ZipArchive $opencvArchive $sourceRoot
    Move-ExtractedDirectory (Join-Path $sourceRoot "OpenCV-android-sdk") (Join-Path $dependencyRoot "opencv")
}
if (-not (Test-Path (Join-Path $dependencyRoot "boost-header-stage\boost_1_69_0\boost\shared_ptr.hpp"))) {
    $boostStage = Join-Path $dependencyRoot "boost-header-stage"
    New-Item -ItemType Directory -Force $boostStage | Out-Null
    $tar = Join-Path $env:SystemRoot "System32\tar.exe"
    & $tar -xf $boostArchive -C $boostStage "boost_1_69_0/boost"
    if ($LASTEXITCODE -ne 0) { throw "Boost header extraction failed" }
}
if (-not (Test-Path (Join-Path $sourceRoot "ceres-solver-1.14.0\CMakeLists.txt"))) {
    Expand-ZipArchive $ceresArchive $sourceRoot
}

$ceresSource = Join-Path $sourceRoot "ceres-solver-1.14.0"
$schurEliminator = Join-Path $ceresSource "internal\ceres\schur_eliminator_impl.h"
$schurSource = Get-Content -LiteralPath $schurEliminator -Raw
if ($schurSource.Contains("    std::random_shuffle(chunks_.begin(), chunks_.end());")) {
    $schurSource = $schurSource.Replace(
        "    std::random_shuffle(chunks_.begin(), chunks_.end());",
        "    random_shuffle(chunks_.begin(), chunks_.end());")
    Set-Content -LiteralPath $schurEliminator -Value $schurSource -NoNewline
}
$ceresBuild = Join-Path $dependencyRoot "ceres-build-$Abi"
$ceresInstall = Join-Path $dependencyRoot "ceres\$Abi"
$cmakeToolchain = Convert-ToCMakePath $toolchain
$cmakeNinja = Convert-ToCMakePath $ninjaExe
$cmakeInstall = Convert-ToCMakePath $ceresInstall
$cmakeEigen = Convert-ToCMakePath (Join-Path $dependencyRoot "eigen3")
& $cmakeExe -S $ceresSource -B $ceresBuild -G Ninja `
    "-DCMAKE_TOOLCHAIN_FILE=$cmakeToolchain" `
    "-DANDROID_ABI=$Abi" `
    "-DANDROID_PLATFORM=android-$ApiLevel" `
    "-DANDROID_STL=c++_shared" `
    "-DCMAKE_MAKE_PROGRAM=$cmakeNinja" `
    "-DCMAKE_BUILD_TYPE=Release" `
    "-DCMAKE_CXX_FLAGS=-D_LIBCPP_ENABLE_CXX17_REMOVED_RANDOM_SHUFFLE" `
    "-DCMAKE_POLICY_DEFAULT_CMP0057=NEW" `
    "-DCMAKE_INSTALL_PREFIX=$cmakeInstall" `
    "-DEIGEN_INCLUDE_DIR=$cmakeEigen" `
    "-DMINIGLOG=ON" `
    "-DBUILD_TESTING=OFF" `
    "-DBUILD_EXAMPLES=OFF" `
    "-DBUILD_BENCHMARKS=OFF" `
    "-DSUITESPARSE=OFF" `
    "-DCXSPARSE=OFF" `
    "-DLAPACK=OFF"
if ($LASTEXITCODE -ne 0) { throw "Ceres configure failed" }
& $cmakeExe --build $ceresBuild --target install --parallel 4
if ($LASTEXITCODE -ne 0) { throw "Ceres Android build failed" }

Write-Host "VINS Android dependencies are ready at $dependencyRoot"
