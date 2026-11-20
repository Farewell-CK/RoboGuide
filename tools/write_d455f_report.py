"""Write a traceable Chinese report from the saved live-device measurements."""
import hashlib
import json
from pathlib import Path
import re
import statistics

root = Path(__file__).resolve().parents[1]
out = root/'artifacts/d455f-device-20261104'
m = json.loads((out/'metrics.json').read_text(encoding='utf-8'))
raw = (out/'live.log').read_text(encoding='utf-8', errors='replace')
selected = [s for s in raw.splitlines() if re.search(
    r'\b(?:SwinLPlan|SemanticPipeline|NavigationAudit|MobileNavigation|SwinAudit)\s*:', s)]
(out/'live-filtered.log').write_text('\n'.join(selected)+'\n', encoding='utf-8')
sensor = {}
for stream in ['Color', 'Depth']:
    values = [int(v) for v in re.findall(r'FrameAccepted,'+stream+r',Counter,(\d+),', raw)]
    sensor[stream] = dict(events=len(values), first=values[0] if values else None,
                         last=values[-1] if values else None,
                         gaps=sum(max(0,y-x-1) for x,y in zip(values,values[1:])),
                         resets=sum(y<x for x,y in zip(values,values[1:])))
m['sdk_logged_frame_counters'] = sensor
(out/'metrics.json').write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding='utf-8')
apk=out/'GuideAssistant-SwinL-384-v8.apk'
with apk.open('rb') as stream:
    apk_hash=hashlib.file_digest(stream, 'sha256').hexdigest()
manifest=dict(version='1.4-swinl-npu-384-v8',package=m['package'],apk=apk.name,
              apk_sha256=apk_hash, apk_bytes=apk.stat().st_size,
              plan_md5='d7fda40af91b1e7e4e0a2ef54517c16d',plan_bytes=459572610,
              subgraphs=44, operations=7514,tests=204, failures=0,
              original_package_preserved='com.seaway.guideassistant')
(out/'apk-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')

def row(label, key, scale=1):
    s=m[key]
    return f'| {label} | {s["n"]} | {s["median"]*scale:.2f} | {s["mean"]*scale:.2f} | {s["p90"]*scale:.2f} | {s["max"]*scale:.2f} |'

first=m['first_completed_frame']
text=f'''# D455f + Swin-L v8 手机实测

2026-11-04，手机 2407FRK8EC，ADB 192.168.5.6:36223。
安装版本：`1.4-swinl-npu-384-v8`，独立包名 `{m['package']}`。

**相机采集、语义点云、OctoMap、地图显示和 A* 计算已运行；实时语义避障验收未通过。**
20 张完成的语义地图全部超过 1.5 秒原有有效期，方向播报保持安全停止。
地图和青色路径仅供查看，不代表已授权按这条路径行走。没有延长有效期或改写采集时间。

## 本次修复

- 先申请相机运行时权限，再请求 UVC/USB 设备授权；解决未授权时不弹窗且无限重试的问题。
- USB 回调在授权成功后才打开设备，拒绝后停止循环。安装保留登录数据，原版包未卸载。
- 秒级模型结果也显示真实代价地图与 A* 诊断路径，过期状态单独标明；语音安全门仍使用原始采集时间。
- 保留经过全量 2000 张验证集评估的 384×288 计划和普通 compute 配置。
  v3 和更小分辨率候选均未作为本次最终版本。

## 测量条件

用户在手机选择目的地并开始导航；输入来自实际 D455f RGB、深度和 IMU，位姿来自真实 VINS。
没有固定图片、空深度或测试位姿。日志窗口约 60 秒，其中有效采集时间跨度 {m['capture_span_seconds']:.2f} 秒，
完成 {m['completed_maps']} 张语义地图。此时已有磁盘编译缓存，记录中无冷编译事件。
该短时场景主要是正前方约 1.8 米的室内障碍，不能代替移动行走和动态行人验收。

第一张完成建图结果的模型耗时 {int(first['model_ms'])} ms，采集至建图 {int(first['age_ms'])} ms；
这只是本日志窗口第一张完成结果，不能当成“全新安装首次运行”。此前无缓存首次模型测试为 74.716 秒，
属于历史固定图片实验，本次没有清空缓存重新测冷启动。

## 真实链路速度

相机 RGB 帧号增长约 **{m['camera_rgb_counter_hz']:.2f} FPS**；RGB/深度处于同一时钟，
配对时间差中位数 **{m['rgb_depth_skew_ms']['median']:.3f} ms**。
语义地图更新约 **{m['map_refresh_hz']:.3f} Hz**，采集至屏幕显示中位数 **{m['capture_to_draw_ms']['median']/1000:.3f} 秒**。

下表单位均为 ms。P90 使用排序后 90% 位置；局部规划统计包含 A* 搜索或路径复用，
日志不足以把这两类完全拆开，不包括因标定未就绪返回的 7 次检查。

| 阶段 | 样本数 | 中位数 | 平均 | P90 | 最大 |
|---|---:|---:|---:|---:|---:|
{row('Swin-L 模型', 'model_ms')}
{row('输入排队', 'queue_ms')}
{row('语义点云', 'pointcloud_ms')}
{row('OctoMap 插入', 'octomap_insert_ms')}
{row('代价网格生成', 'grid_ms')}
{row('点云与建图合计', 'map_total_ms')}
{row('局部规划（含 A* / 路径复用）', 'planning_executed_ms')}
{row('采集至建图', 'capture_to_map_ms')}
{row('采集至绘制', 'capture_to_draw_ms')}

预处理和解码日志以整数 ms 记录，多数小于 1 ms；日志中的 0 不等于无耗时。
模型内部 compile 统计含从磁盘恢复/重建未驻留子图，中位数 {m['compile_ms']['median']:.1f} ms，
compute 中位数 {m['compute_ms']['median']:.1f} ms。编译预取和执行重叠，不能直接将两项相加当墙钟时延。
排除窗口第一张后，19 张连续模型中位数 {m['continuous_model_ms']['median']:.1f} ms，
采集至建图中位数 {m['continuous_capture_to_map_ms']['median']:.1f} ms。

13 次进入局部规划均返回了诊断路径，其余 7 次在进入规划前等待方向/位姿就绪。
**20/20 张语义地图过期，0 张通过 1.5 秒有效期，0 次行走/转向放行。**
这说明地图展示问题已解决，模型时延与当前安全策略的冲突仍未解决，不能称为正常实时导盲。

## 丢帧与资源

原始 SDK Color 日志 {sensor['Color']['events']} 条，帧号缺口 {sensor['Color']['gaps']}；
Depth 日志 {sensor['Depth']['events']} 条，帧号缺口 {sensor['Depth']['gaps']}，
两者帧号回退次数分别 {sensor['Color']['resets']} / {sensor['Depth']['resets']}。
这仅说明记录窗口内 SDK 已接受的帧号连续性，不证明物理 USB 链路完全无丢失。
推理有意只保留最新待处理 RGB/深度对；约 30 FPS 采集与 0.4 Hz 语义刷新差异主要是主动跳过模型输入，
不能把它写成 USB 丢帧率。尚未单独计数被替换的待推理帧。

窗口结束进程 PSS 约 721.3 MiB，RSS 约 856.2 MiB（驱动内存未必全部计入）。
HAL NPU 温度约从 48.0°C 上升至 71.7°C，系统未报告热节流状态；不能据此承诺长时间速度相同。
没有测整机功耗或 CPU 平均占用。

## 与用户 PPT 的口径

PPT 历史 Swin-L 刷新为 8182.4 ms / 0.122 Hz、A* 平均 273.5 ms；
本次语义地图完成间隔平均 {m['map_interval_ms']['mean']:.1f} ms / {m['map_refresh_hz']:.3f} Hz，
局部规划调用（含路径复用）平均 {m['planning_executed_ms']['mean']:.2f} ms，不能当成纯 A* 重搜时间。
场景、输入分辨率与计时边界不同，此处仅并列历史参考，不能写成严格同期加速比。
PPT 的 PIDNet NPU 8.98 Hz 也不能作为同模型基线。

## 产物与复现

- `GuideAssistant-SwinL-384-v8.apk`：已安装的最终收尾包；原版 `com.seaway.guideassistant` 仍在。
- `apk-manifest.json`：APK SHA256、原计划 MD5 与版本标识。
- `metrics.json`：逐帧数据与统计；`live.log`：原始日志；`live-filtered.log`：应用链路日志。
- `screen.png`：实际地图与路径界面；`usb.txt`、`thermal-*.txt`、`memory.txt`：设备状态。
- `build-v8.log`：构建及 204 项回归检查通过，包括过期计划不得解除安全停止的回归。

```powershell
python tools/measure_d455f_live.py --device 192.168.5.6:36223 --output artifacts/d455f-next --seconds 60
```

运行前需打开已安装 Swin-L 并由用户开始真实导航；脚本不注入图片、位姿或目的地。
APK SHA256：`{apk_hash}`。
模型计划 MD5：`d7fda40af91b1e7e4e0a2ef54517c16d`，44 子图、7514 算子，锁定 MDLA，CPU fallback 关闭。
'''
(out/'REPORT.zh-CN.md').write_text(text, encoding='utf-8')
print(json.dumps({'apk_sha256':apk_hash, 'sdk_counters':sensor,
                  'report':str(out/'REPORT.zh-CN.md')}, ensure_ascii=False, indent=2))
