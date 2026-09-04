# Mobile Navigation Turbo

This export contains the current `turbo` Android flavor of Mobile Navigation.

## Turbo configuration

- Flavor: `turbo`
- Semantic model: PIDNet-S Cityscapes TFLite
- Model input: `1024 x 1024`
- Semantic output: `128 x 128`
- Camera frame: full `640 x 480` RGB frame; no left/right crop
- Runtime: LiteRT GPU delegate when available, with CPU fallback
- Native mapping: optimized CMake build (`ELABRADOR_OPTIMIZED_NATIVE=ON`)
- ABI: `arm64-v8a`

## Build

This module stores the Turbo model and RealSense native library with Git LFS. Run `git lfs pull` after cloning or switching to this branch.

1. Install Android Studio/SDK with API 35, NDK and CMake.
2. Copy `local.properties.example` to `local.properties` and set `sdk.dir` to the local Android SDK.
3. Run `tools/prepare_vins_android_deps.ps1` once. It downloads and builds the OpenCV/Ceres dependencies used by VINS.
4. Build the Turbo debug APK:

```powershell
.\gradlew.bat :app:assembleTurboDebug
```

The export intentionally omits Gradle/build caches, generated CMake output, test captures, and host-only Python/Java tool bundles.

## Important runtime note

The Turbo flavor uses LiteRT GPU. The MediaTek NeuroPilot ROM Shim belongs to the separate `npu` flavor and is not required by Turbo.

## License and provenance

See the licenses included with the vendored VINS-Mono, OctoMap, RealSense and dependency sources. Model licensing should be checked before public redistribution.
