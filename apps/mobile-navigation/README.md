# Mobile Navigation NPU

本目录是生成 `black.apk.1` 的 Android NPU 源码：

- application id：`com.elabrador.mobilenavigation.npu`
- versionCode：`89`
- versionName：`0.9.64-pidnet-neuron-cloud-cache`
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
`local.properties`，填写 `sdk.dir`。该文件已被 Git 忽略，不要提交真实凭据。
默认使用预编译应用原生库，macOS、
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

提交或分发前可用 Python 3.11+ 检查预编译库及最终 APK，避免源码里有 `.so`、
安装包却漏装的情况（该工具不依赖 Android NDK）：

```bash
python3 tools/verify_native_package.py app/build/outputs/apk/npu/debug/app-npu-debug.apk
```

三个应用原生库放在 `app/src/main/jniLibs/arm64-v8a/`，RealSense 库放在
`librealsense/src/main/jniLibs/arm64-v8a/`。另外四个推理库由 Gradle 的 AAR 依赖提供，
不要再复制一份到 `jniLibs`，以免重复打包。检查工具核对这四个预编译文件，以及 APK
中的全部八个库，检查 ELF 格式和 AArch64 架构；缺文件或只有 LFS 指针即报错退出。

构建会检查三个应用预编译库是否为有效 ELF；缺少文件或只有 Git LFS 指针时会提示执行
`git lfs pull` 并中止，防止生成无效 APK。只有修改 C++ 源码并重新生成这些库时才需要
Android NDK 和 CMake；在 Windows 上执行准备脚本，再显式启用源码构建：

```powershell
.\tools\prepare_vins_android_deps.ps1
.\gradlew.bat :app:assembleNpuDebug -PbuildNativeFromSource=true
```

NPU 运行库仅在兼容的 MediaTek Neuron 设备上生效。桌面构建无法验证 D455F 真机、
Neuron 可用性、GPS 或高德地图服务。

## 导航交互

输入目的地并点击搜索结果后，自动规划步行路线并启动导航、A* 和局部地图刷新。
也可输入完整地址后按键盘“完成”。定位、路线请求或 VINS 未就绪时明确显示等待或错误；
新请求会使旧请求回调失效，避免快速切换目的地后导航到旧地点。保留“结束导航”。

方向提示带 10° 精度的转角（如"左转 30°"），每个新规划结果只进入提示状态机一次。
地图结果携带投影位姿，目标角与历史路径使用同一坐标系：旧路径先经世界坐标变换到新
相机网格，不能原样套用；相机相对目标角不跨不同位姿做中值平均。历史路径惩罚只影响
搜索代价，不把可通行格提升为硬障碍。纯跟踪按路径实际长度选取前视点，检查沿途与
斜角穿越。对整段可通行且有一格硬障碍间距的低代价直线通道优先走直线；其他情况
仍按当前代价网格绕障，不把所有路线强制拉直。保留稠密路径点用于逐格碰撞和显示。
新障碍立即参与规划，受阻或缺少有效规划输入时显示停止，不再沿用旧行走提示。
提示文字稳定切换时最多每秒切换一次；相同内容不重复更新。"停止"立即显示。
该节流仅作用于提示，不降低语义、地图或 A* 的刷新频率。内置录音、ASR、TTS、语音命令与凭据配置已移除，
由集成方统一实现语音。

进入应用时，请将手机顶部与 D455F 镜头前方对齐并保持几秒：VINS 初始化稳定且罗盘
读数稳定后，自动完成一次北向对齐（状态行显示"自动标定成功"），之后手机与镜头即可
分开携带——对齐关系登记在相机自身的 VINS 坐标系里，不再依赖手机朝向。
走路途中 VINS 重启时自动初始化，但标定改为手动：状态行提示对齐手机与 D455F，
界面出现"重新标定"按钮，对齐后点击即完成并隐藏按钮。对齐完成前暂停方向规划并
显示等待地理方向对齐，不能把镜头正前方当作高德路线方向。标定数值会保存以供诊断，
但不跨坐标系复用。

## 彩色摄像头画面

界面新增 D455F 实时彩色画面，保留深度画面。直接使用既有 640×480 RGB8 流，
不另开相机管线；初始化前也能预览。界面每秒最多更新 5 次，缩小到 320×240，
按实际行跨度转换 RGB 通道，不镜像、不改变语义/VINS 输入。只排队一帧，退后台或
断开相机时清除预览。RGB 原始数据仍在 MainActivity 视频采集入口，可供后续应用集成。

## 0.9.66 验证范围

83 个单元测试通过，覆盖直路小代价扰动、路径坐标变换、历史惩罚与硬障碍隔离、
新硬障碍立即拦截、绕障前视点、RGB 通道与行跨度。APK 的八个 arm64-v8a 库通过
tools/verify_native_package.py 检查。未连接手机做本版实走测试；语义误分类、VINS
漂移、GPS 路线偏差仍需现场观察，本版不声称能让任意场景的路径完全静止。

## 来源核验

最初导入版本通过包名、版本号、文件大小及 SHA-256 与 `black.apk.1` 对应；
当前版本在该基础上修改了导航交互并移除内置语音。原 APK 的
SHA-256 为 `BE76C6C4982AD4DCD8F8B19F3D9B32FF9FA2A1A62A63F415206DBB2DA2E7719C`。
APK、构建产物和本地采集数据不提交；再分发前请阅读随附的 VINS-Mono、OctoMap 和
RealSense 许可证。
