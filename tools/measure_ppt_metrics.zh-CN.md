# Swin-L / eLabrador 实机测量工具

`measure_ppt_metrics.py` 只读手机采集/导航日志与系统资源；不会重装、删缓存、重启应用、注入图像/位姿、改变电池状态或调整屏幕亮度。只在已安装候选通过本机验证后，使用真实 D455f 和用户已开始的导航采集。

## 下次240秒实测命令

在 `D:\RoboGuide-guide-assistant` 执行，使用单独新目录保留每轮证据：

```powershell
python tools/measure_ppt_metrics.py --device 192.168.0.109:39337 --output artifacts/d455f-source-swinl-20261105/run-240s --seconds 240 --wait-for-navigation --cache-state as-observed --condition-label "eLabrador规则；D455f真实导航；当前缓存" --plan-info artifacts/d455f-device-20261104/plan-info.json
```

地址如变更需换成手机当前无线调试地址。该命令不是现在的运行授权；工具修改期间没有调用ADB或Gradle。`--plan-info` 应指向与待测APK模型一致的已核验元数据，它只记录电脑端模型信息，不构成手机字节和NPU分配证明。

开始条件为完成一帧 `FRAME_AUDIT`，且有与其 generation/frame 匹配的 `PLAN_AUDIT` 或 `MAP_DRAW_AUDIT`。不再等待已删除的 `LIVE_PLAN_DRAW`。等待期间日志保留在live.log，但通过measurement.json字节窗口排除出240秒连续资源/吞吐统计。首次观测会单列；等待前的启动阶段不能假称进入240秒样本。

如果专门记录首次运行，从启动前开始采集并省略 `--wait-for-navigation`。可用 `--cache-state cold-requested` 标明操作意图，但工具不删除缓存；是否冷编译必须看真实 `cold_compiles` 日志和独立操作证据。重新打开App也可能复用磁盘编译缓存。

## 指标口径

- **模型耗时**：精确 `SWIN_INFERENCE_AUDIT.model_ms`，Swin-L NPU整网runner调用，包含主机调度/必要缓存编译；排除RGB预处理、输出解码和建图。模型硬件compute、compile另列，不能相加后冒充整个端到端耗时。若旧日志没有该事件，明确回退到FRAME整数日志。
- **地图新图间隔**：`FRAME_AUDIT` 的不同 `(pid,generation,frame)` 完图时间；重复帧、跨代重置分开。模型成功但未建图的帧仍计入推理次数，不冒充地图。
- **真实绘制间隔**：仅第一次 `MAP_DRAW_AUDIT` 与已完成语义帧匹配的实际Canvas绘制。UI重复重绘、旧实时深度帧、不能匹配的采集身份不计入语义刷新。当前源码只有新的语义结果触发地图提交，不靠位姿回调制造新帧。
- **规划UI提交**：`PLAN_AUDIT` 与Canvas分开；显示其plan_ms、ui_queue_ms、map_to_plan_ms、capture_to_display_ms。当前没有PLAN_DRAW_AUDIT，这不是缺项，实际绘制来自MAP_DRAW_AUDIT。
- **A***：只统计 `ASTAR_AUDIT.searches>0` 的search_ms，复用/无搜索单列；完整规划调用另列。前20次不足20次时只报告实际n，不补数。
- **端到端年龄**：采集→推理完成、采集→建图完成、采集→UI提交、采集→实际绘制分开；保留原采集时间。没有把重投影时刻当采集时刻。
- **连续与首次**：每代首次观测与后续帧分开；首次观测不等于冷启动。缓存命中与冷编译优先使用SWIN_INFERENCE_AUDIT逐帧cache_hits/cold_compiles；旧日志才回退SwinLPlan complete，二者不叠加。发生冷编译的调用次数和编译子图总数分开，整个记录（含等待）与测量窗口分开。
- **资源**：2秒一次/proc/stat、meminfo、电池采样；每5次采样读取应用PSS/RSS。CPU是整机忙碌等效核数；整机RAM、应用PSS/RSS分开。
- **功耗**：保持至少240秒、手机放电且AC/USB/无线/底座充电均未启用、电荷计数单调下降且字段齐全，才给出`电荷差×平均报告电压/时长`整机W估算；充电/异常/缺项均留空。该估算包含手机供给相机的负载，不是外接功率仪。不要用dumpsys battery unplug伪造放电。
- **丢帧**：稀疏CAPTURE计数可估相机RGB率；待处理输入替换、物理USB丢失需独立计数，不能由UI或低推理帧率推断。
- **历史阈值诊断**：MAP_DRAW_AUDIT的within_legacy_1500ms（兼容旧名within_1500ms）只统计旧1500毫秒标准是否满足，不表示当前Swin地图有效期，也不据此宣布获准行走。SEMANTIC_MAP_DROP和SOURCE_GROUND_AUDIT完整保存于events.json，便于核对丢弃原因与源码地面处理。

每轮应保持亮度/自动亮度模式、屏幕状态、相机场景、供电与温度条件可比较。起止亮度、热状态、USB状态、包版本和屏幕截图自动存档。工具不声明未实测的安全可用性，也不把无行走提示的数据当验收通过。

## 离线分析与验证

```powershell
python tools/measure_ppt_metrics.py --analyze-only --output artifacts/d455f-source-swinl-20261105/run-240s
python -m unittest discover -s tools -p test_measure_ppt_metrics.py -v
```

离线分析不要求设备参数、不调用ADB；没有资源文件也可分析日志，资源指标留空。输出：`metrics.json`（所有样本统计、前20个原值、首次/连续）、`events.json`（逐条原始日志）、`REPORT.zh-CN.md`（中文PPT对应指标表）。`model_artifact.json`在提供元数据时复制保存。

旧 `write_ppt_metric_report.py` 专用于历史v9/v10报告，不应拿它生成新的源码规则版本报告；本工具自带报告生成，避免混入已删除的LiveDepthMap口径。
