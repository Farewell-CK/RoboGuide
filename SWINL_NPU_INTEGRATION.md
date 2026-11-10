# 导盲助手 Swin-L NPU 集成

> 最新按用户要求只汇总两个指标，重新完整实测240.40秒，v15与已有缓存不变：72次单帧推理平均2.952秒/中位2.917秒；71个实际新图刷新间隔平均3.296秒/中位3.258秒。报告在 `artifacts/d455f-source-swinl-20261105/v15-current-pose/remeasure-240s-02/TWO_METRICS.zh-CN.md`。本轮比上一轮慢，所有慢帧均保留，不能用旧结果替代本轮。

> **2026-11-05 最新：手机已安装 `1.4-swinl-npu-384-v15-source`，原版包保留。**
> 继续用户现有 eLabrador 移植版，保留用户类别代价修改；恢复官方语义OctoMap代价/过滤/膨胀，撤掉Java快速深度地图和另造的障碍记忆。完整Swin-L 384×288计划/权重未改。
> v14已完成240.01秒真实D455f导航采样：模型平均2.107秒，新语义地图约0.423Hz，采集→Canvas平均2.552秒，放电功耗估算7.42W。它们是不同指标，不能把2.107秒当转动反应时间。
> v15通过236项本机测试：在每张新语义图完成时采用最新真实VINS位姿投影已融合world图，原始采集证据不续期、不增加语义帧数。最新v15重新实测完整240.28秒：92次推理平均2.278秒/中位2.306秒；90个新图绘制间隔平均2.621秒/中位2.647秒；91帧采集→绘制平均2.819秒/中位2.856秒。保留已有编译缓存，没有删慢帧。该轮未同步标记转动起点，不能当旋转反应时间。
> **最新均值/中位数报告：** `artifacts/d455f-source-swinl-20261105/v15-current-pose/remeasure-240s-01/MEAN_MEDIAN.zh-CN.md`；原始日志、每帧配对、CSV和完整240秒清单在同目录。此前v15的10帧短日志不是完整60秒测试，不再用于代表稳定速度。
> 规则与240秒结果：`artifacts/d455f-source-swinl-20261105/`；v15 APK与验证：其下 `v15-current-pose/`。安装状态见 `device-status.json`。
> 快速源规则版构建须加 `-PswinlFast=true`。位姿1.5秒检查保持；语义图按源码跟踪端适配为原始采集起10秒，已明确记录行为变化，不代表安全验收通过。
> 以下v12/v8/v4等均为历史，**不要重新启用已撤下的LiveDepthMap/WorldObstacleMemory**，不要卸载 `com.seaway.guideassistant`。

## 历史 v12 状态

> 2026-11-05 当时手机安装 `1.4-swinl-npu-384-v12`，ADB 更新为 `192.168.0.109:39337`。
> Swin-L 仍为已做 2000 张精度评估的 384×288 计划，MD5 `d7fda40af91b1e7e4e0a2ef54517c16d`。
> v12 已通过 232 项本机测试：增加世界坐标障碍记忆；只有连续真实深度射线确认才移除；
> 新鲜深度继续使用原采集时间与 1500 ms 上限。未知格仍按源码保留中等代价。
> 地图实际绘制频率、断更时长、深度/语义帧龄分开显示；VINS 重置会明确提示重建地图。
> **v12 真机移开/转回镜头验收尚在进行，不能把本机测试当成实际导航已通过。**
> v11 的 240 秒日志有行走/转向提示，但发生过 29.255 秒断更；手机充电，功耗未取得。
> 最新代码、APK 和验证说明在 `artifacts/d455f-device-20261104/v12-map-memory/`。
> 下方 v8 等结果是历史记录，原版 `com.seaway.guideassistant` 必须保留，不能卸载。

> 2026-11-04 最新安装为 `1.4-swinl-npu-384-v8`，保留经过全量精度评估的 384×288 计划
> MD5 `d7fda40af91b1e7e4e0a2ef54517c16d`。D455f 实测已完成：模型中位数 2.344 秒，
> 采集至绘制 2.635 秒，地图约 0.4 Hz；USB/相机授权、点云、地图与诊断路径显示已修复。
> **20 张结果全部超过原有 1.5 秒有效期，实时语义避障未通过验收，不能称为正常实时导盲。**
> 完整结果、原始日志与收尾 APK 在 `artifacts/d455f-device-20261104/REPORT.zh-CN.md`。
> 本文下方 v4/v5/v6 内容为历史过程；构建快速收尾版仍需 `-PswinlFast=true`。

基于 `Farewell-CK/RoboGuide` 的 `feat/guide-assistant` 分支，起始提交
`0e76585`，在独立目录 `D:/RoboGuide-guide-assistant` 中开发。

## 历史 v4 模型与实现

- 应用包名：`com.seaway.guideassistant.swinl`；桌面名称：`导盲助手 Swin-L`。
- 默认语义模型由 PIDNet-S 改为 Mask2Former Swin-L，640×480、65 类 Mapillary。
- 模型：`navoutdoor/src/swinl/assets/models/swinl-npu-fast-20261103-v4.plan`，
  471638235 字节，MD5 `5675f5d9cd96882c7d3f35538b5aaaa9`。
- 模型包含输入归一化，Java 提供 RGB 0–255、FP16 NCHW，输出 INT32 mask 与 FP16 confidence。
- 使用经过验证的 build25 NNAPI 执行核心，锁定 `mtk-mdla_shim`，79 子图、11153 算子。
  编译或执行失败即报错，无 CPU 回退。
- 保留共享权重/张量、16 槽 cost 模板缓存、编译缓存、下一模型预加载和帧间复用。
- v5 执行器保留 FAST_SINGLE_ANSWER；完整应用实测表明 Burst 反而略慢，因此正式路径关闭 Burst。
  高优先级也默认关闭。
- v5 在 JNI 中用 ARM NEON 将 RGB8 转为 FP16 NCHW，并将 FP16 confidence 解码到 Java float 数组。
  非 640×480 输入仍执行与原 Java 路径相同的最近邻缩放。
- 正式推理关闭逐子图硬件计时采集，只保留总耗时；这减少 79 次 NNAPI driver timing bookkeeping，
  调试报告仍保留 wall-clock、compile 和 compute 统计。
- `SwinlNpuEngine` 同时供 `SemanticSegmenter` 与设备测试页使用；PIDNet 的 19→65 类映射
  不再应用于 Swin-L 输出。
- Android 最低版本调整为 API 29。当前仅打包 arm64-v8a；此运行路径要求手机提供 MDLA 驱动。

## 编译、安装、测量

```powershell
cd D:/RoboGuide-guide-assistant
$env:ANDROID_HOME='C:/Users/27728/AppData/Local/Android/Sdk'
.\gradlew.bat --offline --no-daemon '-Dorg.gradle.jvmargs=-Xmx2g -XX:+UseSerialGC -Dfile.encoding=UTF-8' --max-workers=2 :app:assembleDebug

$adb='C:/Users/27728/AppData/Local/Android/Sdk/platform-tools/adb.exe'
$device='192.168.5.6:36223'
& $adb connect $device
$apk=Get-ChildItem app/build/outputs/apk/debug/swinl_*.apk | Sort-Object LastWriteTime -Descending | Select-Object -First 1
& $adb -s $device install -r $apk.FullName
& $adb -s $device shell am start -n com.seaway.guideassistant.swinl/com.elabrador.mobilenavigation.SwinlBenchmarkActivity
& $adb -s $device shell run-as com.seaway.guideassistant.swinl cat files/swinl-app-benchmark.json
```

测试入口仅存在于 `swinlDebug`。每次测试运行固定参考 RGB 图像 5 次，首轮单列，后 4 轮取中位数。
统计范围为 RGB 预处理、完整模型和 mask/confidence 解码；不包含摄像头采集、深度、VINS 或地图。
每一轮都要求两个输出的字节校验与之前的 verified170 完全一致：
mask FNV `14479554717533701158`，confidence FNV `7172329502057442931`。

## 使用边界

首次安装需生成编译缓存，耗时远高于预热后的单帧时间。
Swin-L 的秒级推理仍不适合实时避障。原导航证据的 1.5 秒有效期检查继续生效，
因此不能用模型测试成功代表实时导航链路可用；相机、深度和 VINS 联调需另行验证。

真机结果与 APK 校验信息在 `artifacts/swinl-device/` 保存。

## 2026-11-03 首次应用内实测

手机 `2407FRK8EC`，连接地址 `192.168.5.6:36223`。
首次模型复制/加载 2811 ms；首帧生成全部 79 个编译缓存并推理 274174 ms，
其中编译 267257 ms。未从独立测试器导入缓存。

预热后四帧总时长为 6154.077 / 5744.155 / 5781.488 / 5795.348 ms，
中位数 **5788.418 ms（0.173 FPS）**。最后一轮模型自身 5734.383 ms、
RGB 预处理 44.447 ms、解码 16.517 ms。五轮两个输出均与 verified170 字节校验一致。

历史同模型 CPU 稳定基线为 6036 ms，测量时间和统计范围不同，不能将本轮差值
作为严格的同期加速比，更不能以此推断比 PIDNet 更快。

原版 `com.seaway.guideassistant` 已用本机原 APK 恢复，保留数据；
Swin-L 使用独立包名，两个应用同时安装。

导航接口冒烟检查另外发现原生 OctoMap 对空点云调用 `nativeInsert` 会空指针崩溃。
Java 侧已对空点云跳过插入，树保持原状。测试使用固定 RGB、空深度与明确标注的
测试位姿，仅验证 `SemanticSegmenter` 的模型初始化、推理、解码和结果回调，
不作为真实相机/位姿/导航验收。

修复后接口检查通过：`MediaTek-MDLA-SwinL-FP16`，单帧结果回调 6046 ms，
中心类别为“停车区”。这是新进程启动、磁盘编译缓存已有时的一帧，不是连续帧中位数。
最终 APK 的 native 库与五轮测速使用的库 SHA256 相同；最终正常启动进入登录页，
独立包名需要用户单独登录。

## 2026-11-04 执行器优化（历史 v5）

当前 app 版本为 `1.3-swinl-npu-v5`，仍使用上述 v4 模型计划；v4 APK 与原始记录保留在
`artifacts/swinl-device/`。本轮仅使用固定输入验证，D455f 真实采集测试及自动跟进已取消。

独立 NPU runner 的 Burst 实验 burst173 为 5364.355 ms，但完整应用中的 Burst
组合候选未显示相同收益，原因尚未确认；应用最终采用普通 compute。

高优先级 priority171 为 5468.838 ms，与普通模式差距较小且首次产生独立编译缓存，
因此不作为默认设置。小张量内存池实验只完成部分帧，时延约 7.2 秒，已撤回；
缺乏同期对照，不能确定慢因。最终代码按原生命周期释放中间张量。

应用内可复现的顺序对照命令：

```powershell
python tools/bench_swinl_app.py --device 192.168.5.6:36223 --output artifacts/swinl-speed-20261104
```

测试入口的 `--ez baseline true` 使用原 Java 前后处理、逐子图硬件计时和普通 compute；
缺省使用 NEON 前后处理、关闭硬件计时与 Burst。每组均执行 5 帧、排除首帧取中位数，
并额外校验原生 RGB 转换、Java mask/confidence 解码及模型输出。

同一 APK 的顺序对照结果：baseline-before 5642.957 ms，optimized 5553.284 ms，
baseline-after 5635.595 ms；两侧基线中位数 5639.276 ms，优化后降低 **1.525%**。
模型推理仍占绝大部分耗时，优化主要降低前后处理和 NNAPI 计时管理开销，不能把
1.525% 解释成模型计算本身降低了同样比例。

## 2026-11-04 快速版达到 3 秒以内（历史 v6）

当前手机安装 `1.4-swinl-npu-384-v6`。原版应用仍保留，Swin-L 使用独立包名。
快速版采用相同 Swin-L 检查点、完整网络和 65 类，输入为 384×288。
计划 44 子图、7514 算子全部由 MDLA 接受，CPU fallback 关闭。
MD5 `d7fda40af91b1e7e4e0a2ef54517c16d`，459572610 字节。

应用内范围为 RGB 预处理、模型和解码：无缓存首帧 74.716 秒（其中编译
72.469 秒），后四帧中位数 **2.087 秒**；最终 APK 已有磁盘缓存的新进程
首帧 2.491 秒，后四帧中位数 **1.970 秒**，十帧输出校验一致。
同一独立 runner 配置下，修正后的 640×480 为 5.483 秒，384×288 为
2.361 秒，耗时下降约 56.9%。本轮没有同期 CPU 对照。

384 相对于同尺寸 FP32 的暗光真实 RGB 样本分类一致率 99.9014%，
confidence 余弦 0.999968；但降分辨率后与 640 FP32 对齐仅 83.7015%，
有细节和类别损失。不能将转换一致率当成数据集精度或导航可靠性。
本次没有 D455f 实时采集，1.5 秒导航证据有效期未放宽。

**旧精度参考作废：**本次发现旧最终 `qc,qhw->chw` 改写遗漏 transpose，
把 `[100,65]` 直接 reshape 成 `[1,65,100]`，旧 verified170 哈希持续复现了
错误分类。上文历史哈希和 98.2% 精度比较不能证明与原模型一致。
已新增修正的 640×480 计划并完成手机执行验证，历史文件保留。
直接对未改写 FP32 图回归时，PyTorch 640 分类逐像素 100% 一致。

快速构建在原命令追加 `-PswinlFast=true`；`-PswinlFast=false` 构建修正的
640×480 配置。默认构建已切换为修正的 640 配置，避免继续打包旧错误计划。
对应资产在 `src/swinlFast` / `src/swinlCorrected`，历史 `src/swinl` 保留。
转换代码与重建说明在 `tools/swinl_plan_export/README.zh-CN.md`；仓库内代码
已独立重建并确认计划 MD5 一致。

最终 APK、冷热启动日志、每帧 JSON、画质对照及中文报告位于
`artifacts/swinl-under3s-20261104/`。后续以该报告为准，旧 v4/v5 记录仅供历史追溯。

## 2026-11-05 eLabrador代价恢复（v14已实测，v15修复投影）

以用户现有移植工程继续修复，按官方 c758acd 核对障碍代价；保留用户道路0.2、辅路0.1、自行车道0.05。Swin恢复语义点云→原生OctoMap→官方类别/高度平均、median、Sobel和world膨胀，显示膨胀前地图。撤掉 LiveDepthMap/自建障碍记忆，不再把高频深度/UI刷新说成Swin速度。地图旁显示真实单帧模型时间、完整语义建图时间与真实新图间隔。

语义有效期调整为原始采集起10秒，对应源码跟踪端10秒接收有效期；位姿保持1.5秒检查，不通过重绘延长证据。此项是明确的行为改变，不能用单元测试代替实际使用验证。原始低分辨率模型与已测数据集精度不变。

当前详细规则、移植差异与验证记录：`artifacts/d455f-source-swinl-20261105/SOURCE_RULES.zh-CN.md`。上文 v6及更早“当前安装”均为历史记录；真实安装状态以新目录 `device-status.json` 为准。仅更新测试包 `com.seaway.guideassistant.swinl`，原版绝不卸载。
