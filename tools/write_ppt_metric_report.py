"""Keep failed device runs and locally validated candidates explicitly separate."""
from pathlib import Path
import collections, csv, hashlib, json, re
root=Path(__file__).resolve().parents[1];out=root/'artifacts/d455f-device-20261104'
plan=json.loads((out/'plan-info.json').read_text(encoding='utf-8'))
rows=[]
def number(value):return '未取得' if value is None else f'{value:.2f}'
def mean(m,key):return m[key]['all']['mean'] if m[key]['all'] else None
def first(m,key):return m[key]['first20']['mean'] if m[key]['first20'] else None
for version in ['v9','v10']:
    folder=out/f'ppt-{version}-240s';m=json.loads((folder/'metrics.json').read_text(encoding='utf-8'))
    raw=(folder/'live.log').read_text(encoding='utf-8',errors='replace')
    reasons=collections.Counter(re.findall(r'CUE_AUDIT cue=停止 reason=(.*?) frame=',raw))
    rejects=[float(v) for v in re.findall(r'CAPTURE_REJECT .*skew_ms=([\d.]+)',raw)]
    (folder/'stop-reasons.json').write_text(json.dumps({'reasons':dict(reasons),'pair_reject_events':len(rejects),
        'max_rejected_pair_skew_ms':max(rejects) if rejects else None},ensure_ascii=False,indent=2),encoding='utf-8')
    refresh=m['live_map_refresh'];sem=m['semantic_refresh'];cpu=m['whole_device_busy_cpu_cores']
    rows.append({'version':version,'map_refresh_first20_ms':refresh['first20_intervals_ms']['mean'],
      'map_refresh_full_hz':refresh['hz'],'semantic_refresh_hz':sem['hz'],'planning_first20_ms':first(m,'live_planning_ms'),
      'astar_search_first20_ms':first(m,'astar_when_searching_ms'),
      'model_mean_ms':mean(m,'semantic_model_ms'),'model_inverse_mean_fps':1000/mean(m,'semantic_model_ms'),
      'device_busy_cores':cpu['mean'] if cpu else None,'app_pss_mean_mib':m['app_pss_mib']['mean'],
      'device_used_ram_mean_mib':m['whole_device_used_ram_mib']['mean'],
      'estimated_power_w':m['battery_discharge']['estimated_whole_device_w'],
      'capture_to_screen_ms':mean(m,'live_capture_to_draw_ms'),'moving_cue_events':m['moving_cue_events']})
    report=f'''# Swin-L {version}：PPT 各项指标对应实测

**本次功能验收未通过：{m['moving_cue_events']} 次行走提示，{m['stop_cue_events']} 次停止日志。速度数据不代表导盲已正常可用。**

2026-11-04/05，手机 2407FRK8EC，D455f 真 RGB/深度/IMU 与真实 VINS；无固定输入、假位姿。
独立包 `com.seaway.guideassistant.swinl`，原包 `com.seaway.guideassistant` 保留。
本组以现有磁盘缓存连续运行；未清缓存测冷启动，日志冷编译事件 {m['cold_compile_events']}。
放电窗口 {m['battery_discharge']['duration_seconds']:.2f} 秒，屏亮度原始设置在 `brightness.txt`，无充电。
采集窗口包含用户调整相机朝向，不能视作与 PPT 原场景严格控制变量的加速比实验。

## 与 PPT 每项对应

| PPT 指标 | 本次结果 | 口径 |
|---|---:|---|
| A* / 局部规划平均，20 次 | {number(first(m,'live_planning_ms'))} ms | 前 20 次确实进入规划的完整调用，含复用 |
| 单独 A* 搜索，最多 20 次 | {number(first(m,'astar_when_searching_ms'))} ms（{m['astar_when_searching_ms']['first20']['n'] if m['astar_when_searching_ms']['first20'] else 0} 次） | 排除复用，实际次数单列，可能含两轮候选 |
| 屏幕地图刷新间隔，20 次 | {number(refresh['first20_intervals_ms']['mean'])} ms | 实际 Canvas 绘制的不同采集帧，非重复重绘 |
| 整段实时深度地图刷新 | {number(refresh['hz'])} Hz | 含断更；与慢速语义地图分开 |
| Swin-L 语义地图刷新 / 吞吐 | {number(sem['hz'])} 帧/秒 | 完整语义点云/建图完成间隔 |
| Swin-L 单帧模型平均 | {number(mean(m,'semantic_model_ms'))} ms | 现有编译缓存，整段，不等同冷启动 |
| 纯模型平均耗时倒数 | {number(1000/mean(m,'semantic_model_ms'))} 帧/秒 | 不包含采集/建图的理论处理率，不冒充完整吞吐 |
| CPU 占用 | {number(cpu['mean'] if cpu else None)} 核 | /proc/stat 整机忙碌等效核数，8 核手机，排除 idle/iowait |
| 内存：应用 PSS | {number(m['app_pss_mib']['mean'])} MiB | 240 秒内 25 次 dumpsys meminfo，均值 |
| 内存：整机已用 RAM | {number(m['whole_device_used_ram_mib']['mean'])} MiB | MemTotal−MemAvailable；PPT 490/530 MB 未明确为哪种指标 |
| 整机电池功耗估算 | {number(m['battery_discharge']['estimated_whole_device_w'])} W | 电荷计数差 × 报告电压 / 时长，非外接功率仪 |
| 模型大小 | {plan['decimal_mb']:.2f} MB（{plan['mib']:.2f} MiB） | 实际 .plan 文件，不是 APK 或运行内存 |
| NPU 节点分配 | {plan['operations']}/{plan['operations']}，{plan['subgraphs']} 子图 | 运行器逐图检查完全接受，失败即报错，禁止 CPU 回退 |
| 精度/数据类型 | FP16 浮点运算，INT32 索引/类别输出 | 非 INT8；分辨率 384×288，完整 Swin-L |

当前 Swin-L 的 7514 个低级 NNAPI 算子与 PPT PIDNet 的 195 个图节点是两张不同的图。
不可把“193/195、2 个 AvgPool 留 CPU”套到 Swin-L。当前完整模型结果均经过指定 MDLA 驱动，
RGB、深度、VINS、点云、A* 和 UI 仍包含 CPU 工作，不能称为整个 App 全部在 NPU 上执行。
算子逐项构成见上一级 `plan-info.json`，模型 MD5 `{plan['md5']}`。

## 补充端到端分解

| 阶段 | 样本数 | 中位数 ms | 平均 ms | P90 ms |
|---|---:|---:|---:|---:|
'''
    for label,key in [('模型','semantic_model_ms'),('预处理（整数日志）','preprocess_ms'),('解码（整数日志）','decode_ms'),
      ('语义点云','semantic_cloud_ms'),('OctoMap 插入','octomap_insert_ms'),('语义网格','semantic_grid_ms'),
      ('实时深度地图构建','depth_map_build_ms'),('完整局部规划','live_planning_ms'),
      ('采集至语义建图','semantic_capture_to_map_ms'),('采集至实际绘制','live_capture_to_draw_ms')]:
        s=m[key]['all']
        if s:report+=f"| {label} | {s['n']} | {s['median']:.2f} | {s['mean']:.2f} | {s['p90']:.2f} |\n"
    report+='''
整数 0 ms 表示该外层日志未分辨出 1 ms，不能写成零成本。完整样本、前 20 次读数与原始日志
分别为 `metrics.json`、`events.json` 与 `live.log`。CPU/电池原始数据为 `resources.jsonl`。
采集帧率以实际 RGB 计数计算，稀疏日志不足以给出物理 USB 精确丢帧数。
`CAPTURE_REJECT` 表示 RGB/深度时间错配而被拒绝，不能直接当成 USB 丢失。

## 停止原因

'''
    report+='\n'.join(f'- {reason or "等待安全路径恢复确认"}：{count} 条' for reason,count in reasons.most_common())
    report+=f'\n\nRGB/深度配对拒绝事件：{len(rejects)}，最大错配 {max(rejects) if rejects else 0:.2f} ms。\n'
    (folder/'REPORT.zh-CN.md').write_text(report,encoding='utf-8')
with (out/'PPT-metrics.csv').open('w',encoding='utf-8-sig',newline='') as f:
    writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
tests=json.loads((out/'v11-validation/tests.json').read_text());timing=json.loads((out/'v11-validation/measured-pose-delay.json').read_text())
apk=sorted((root/'app/build/outputs/apk/debug').glob('*v11*.apk'),key=lambda p:p.stat().st_mtime)[-1]
with apk.open('rb') as f:sha=hashlib.file_digest(f,'sha256').hexdigest()
manifest={'version':'1.4-swinl-npu-384-v11','apk':str(apk),'apk_bytes':apk.stat().st_size,'sha256':sha,
 'plan_md5':plan['md5'],'tests':tests,'installed':False,'device_acceptance_passed':False}
(out/'v11-validation/candidate.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
validation=f'''# v11 候选本机验证（尚未安装）

## 已完成

- APK 编译成功；{tests['tests']} 项测试通过，{tests['failures']} 失败、{tests['errors']} 错误、{tests['skipped']} 跳过。
- 117 个实机 VINS 延迟样本：最小 {timing['min_ms']} ms、中位数 {timing['median_ms']} ms、最大 {timing['max_ms']} ms。
  其中 {timing['within_250ms']} 个位于原 250 ms 等待范围内，{timing['within_1500ms']} 个位于 1500 ms 内。
  用这些实测延迟驱动新的 10 Hz 调度测试，117/117 保留到对应位姿可用时，未使用外推位姿。
  这不是“旧方案 7 帧/新方案 117 帧”的真机吞吐对比：日志只抽样部分位姿，原代码还会偶然追上。
- 另测过期帧丢弃、位姿空档禁止插值、队列最大 16 帧、世界坐标系重置和单帧只能消费一次。
- 25 种倾角/航向组合下解析平地均可识别；坑洞仍标为未观测，头部高度的已观测障碍仍阻止通行。
  这些是解析几何测试，另有下述真实点云回放。
- 通过现有 v10 进程诊断提取 12 帧真实 D455f 点云、对应深度和完整位姿历史；
  用每个有效深度值逐项匹配点云 Z，确认帧身份，再以原采集时间插值原始位姿。
  12 帧重跑候选几何逻辑：8 帧源码路径通过深度障碍检查，4 帧实际障碍仍被拦住。
  这验证几何与规划规则，不包含重新运行 NPU，也不是实时导航/步行验收。
- 恢复源码对未知格的中等代价规则。先前新增“脚前必须逐格看见地面”会卡住相机近处盲区，
  已移除其额外停止条件；未知格继续保持 -1/灰色，不伪装成已观测地面。
- 地图语义重投影每帧只计算一组姿态变换，省去 6400 次重复姿态矩阵和三角函数计算。
- Swin-L 的 RGB/深度对齐统一在导航采样帧处理，减少为一个慢速模型重复对齐待替换输入。
- 原实时深度 1500 ms 有效期保留；异步 Swin-L 语义软参考仍使用自己的原采集时间和 10000 ms 上限，
  不允许旧语义覆盖当前硬障碍。原规划器允许未知格参与规划，不等于已确认未知区地面。
- 模型计划 MD5 `{plan['md5']}` 保持，完整模型算子与输入分辨率均未再缩减。

## 尚未证明

已取得有限真实深度帧、点云和连续位姿，完成几何回放；未在电脑重跑完整 RGB-D/VINS/NPU 链路。
尚未在手机验证 v11 的持续通路提示、实际障碍停止、断流停止和 240 秒速度/功耗。
因此 v11 仍是候选，不能把本机测试通过写成软件已经恢复正常。

APK：`{apk}`；SHA256 `{sha}`。
手机上最后确认安装版本仍为 v10，原包保留。
'''
(out/'v11-validation/REPORT.zh-CN.md').write_text(validation,encoding='utf-8')
print(json.dumps({'candidate':manifest,'device_runs':rows},ensure_ascii=False,indent=2))
