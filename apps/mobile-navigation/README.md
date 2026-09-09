# Mobile Navigation NPU

本目录是生成 `black.apk.1` 的 Android NPU 源码：

- application id：`com.elabrador.mobilenavigation.npu`
- versionCode：`85`
- versionName：`0.9.60-pidnet-neuron-cloud-cache`
- ABI：`arm64-v8a`
- 语义模型：PIDNet-S NPU（`app/src/npu/assets/models/`）

该应用是实验性的手机端 Local System，负责本地感知、D455F 深度建图、语义代价图、
A* 规划、导航呈现和即时安全；它不拥有 RoboGuide 的 Control、Runtime、State 或
Node Protocol 权威。

## 获取源码

克隆分支后先获取 Git LFS 对象：

```powershell
git lfs pull
```

PIDNet NPU 模型、RealSense arm64 原生库以及 `libelabrador_native.so`、
`libvins_feature_tracker.so`、`libvins_estimator.so` 三个应用原生库由 Git LFS 管理。
OpenCV、Eigen、Boost 和 Ceres Android 依赖仅在重新编译应用原生库时由准备脚本在本地生成，
不进入 Git。

## 构建

默认构建需要 Android SDK API 35 和 Java 17。将 `local.properties.example` 复制为
`local.properties`，填写 `sdk.dir`；需要语音功能时还需填写 `asr.token` 和
`tts.apiKey`。该文件已被 Git 忽略，不要提交真实凭据。默认使用预编译 VINS 库，macOS、
Linux 和 Windows 都可以直接构建：

```powershell
Copy-Item local.properties.example local.properties
.\gradlew.bat :app:testNpuDebugUnitTest :app:assembleNpuDebug
```

macOS 或 Linux 使用：

```bash
cp local.properties.example local.properties
./gradlew :app:testNpuDebugUnitTest :app:assembleNpuDebug
```

生成的 APK 位于 `app/build/outputs/apk/npu/debug/app-npu-debug.apk`。依赖已缓存时可为
Gradle 命令增加 `--offline`。

构建会检查三个应用预编译库是否为有效 ELF；缺少文件或只有 Git LFS 指针时会提示执行
`git lfs pull` 并中止，防止生成无效 APK。只有修改 C++ 源码并重新生成这些库时才需要
Android NDK 和 CMake；在 Windows 上执行准备脚本，再显式启用源码构建：

```powershell
.\tools\prepare_vins_android_deps.ps1
.\gradlew.bat :app:assembleNpuDebug -PbuildNativeFromSource=true
```

NPU 运行库仅在兼容的 MediaTek Neuron 设备上生效。桌面构建无法验证 D455F 真机、
Neuron 可用性、GPS 或高德地图服务。

## 来源核验

已通过包名、版本号、文件大小及 SHA-256 将本源码与 `black.apk.1` 对应。提交前仅将
硬编码的 ASR/TTS 凭据改为由 Git 忽略的 `local.properties` 注入。原 APK 的
SHA-256 为 `BE76C6C4982AD4DCD8F8B19F3D9B32FF9FA2A1A62A63F415206DBB2DA2E7719C`。
APK、构建产物和本地采集数据不提交；再分发前请阅读随附的 VINS-Mono、OctoMap 和
RealSense 许可证。
