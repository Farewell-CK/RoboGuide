# Mobile Navigation Android

`mobile-navigation` is an experimental Android Local System for phone-side
perception, positioning, semantic mapping, route planning, and local navigation.
It retains the V2 `Immediate How` and final-safety responsibility. It is not a
RoboGuide Control, Runtime, State, Mission, or Node Protocol authority, and this
initial import does not yet provide a `roboguide-node` integration.

## Project layout

- `app/` contains the Android application, Java local-navigation logic, JNI
  bridges, unit tests, and the `r50`/`swinl` semantic-model flavors.
- `librealsense/` contains the Android Java bindings used by the application.
- `third_party/octomap/` and `third_party/vins_mono/` contain the vendored native
  source required by the JNI build.
- `tools/prepare_vins_android_deps.ps1` downloads and builds the generated VINS
  Android dependency tree under `third_party/vins_android_deps/`.
- The remaining scripts under `tools/` export and inspect semantic ONNX models.

## External assets

Regular Git cannot carry the current runtime models and native library because
each exceeds GitHub's 100 MB per-file limit. Obtain the exact reviewed artifacts
from the project owner and place them at:

```text
app/src/r50/assets/models/mask2former-R50-mapillary-semantic-320.onnx
app/src/swinl/assets/models/mask2former-swinL-mapillary-semantic-640x480.onnx
librealsense/src/main/jniLibs/arm64-v8a/librealsense2.so
```

The expected model sizes are declared in `app/build.gradle`. Do not substitute
models or native libraries without recording their provenance and compatibility.
The original local backup remains the source for these uncommitted artifacts.

## Local setup

1. Install Android SDK 35, an Android NDK, CMake, and JDK 17.
2. Let Android Studio create the untracked `local.properties`, or set
   `ANDROID_HOME`.
3. Stage the external assets listed above.
4. Prepare VINS dependencies from PowerShell:

   ```powershell
   .\tools\prepare_vins_android_deps.ps1
   ```

5. Run local JVM tests:

   ```powershell
   .\gradlew.bat test
   ```

Hardware, camera, model, and network checks are adapter/system checks and require
the corresponding Android device, RealSense camera, model assets, and service
credentials. The GaoDe Web Service key is entered at runtime and must not be
committed.
