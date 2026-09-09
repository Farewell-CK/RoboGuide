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

PIDNet NPU 模型和 RealSense arm64 原生库由 Git LFS 管理。OpenCV、Eigen、Boost 和
Ceres Android 依赖由准备脚本在本地生成，不进入 Git。

## 构建

安装 Android SDK API 35、NDK、CMake 和 Java 17。将 `local.properties.example` 复制为
`local.properties`，填写 `sdk.dir`；需要语音功能时还需填写 `asr.token` 和
`tts.apiKey`。该文件已被 Git 忽略，不要提交真实凭据。然后准备 VINS 依赖并构建：

```powershell
Copy-Item local.properties.example local.properties
.\tools\prepare_vins_android_deps.ps1
.\gradlew.bat :app:testNpuDebugUnitTest :app:assembleNpuDebug
```

生成的 APK 位于 `app/build/outputs/apk/npu/debug/app-npu-debug.apk`。依赖已缓存时可为
Gradle 命令增加 `--offline`。

NPU 运行库仅在兼容的 MediaTek Neuron 设备上生效。桌面构建无法验证 D455F 真机、
Neuron 可用性、GPS 或高德地图服务。

## 来源核验

已通过包名、版本号、文件大小及 SHA-256 将本源码与 `black.apk.1` 对应。提交前仅将
硬编码的 ASR/TTS 凭据改为由 Git 忽略的 `local.properties` 注入。原 APK 的
SHA-256 为 `BE76C6C4982AD4DCD8F8B19F3D9B32FF9FA2A1A62A63F415206DBB2DA2E7719C`。
APK、构建产物和本地采集数据不提交；再分发前请阅读随附的 VINS-Mono、OctoMap 和
RealSense 许可证。
