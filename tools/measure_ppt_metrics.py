"""Measure real D455f/Swin-L navigation and PPT resource metrics; never inject input.

Analysis is importable and can run without ADB. See measure_ppt_metrics.zh-CN.md.
"""
import argparse
import json
import math
import re
import statistics
import subprocess
import time
from pathlib import Path

ADB = r'C:\Users\27728\AppData\Local\Android\Sdk\platform-tools\adb.exe'
PACKAGE = 'com.seaway.guideassistant.swinl'
TAGS = ('SWIN_INFERENCE_AUDIT', 'FRAME_AUDIT', 'MAP_QUEUE_AUDIT',
        'SEMANTIC_MAP_DROP', 'SOURCE_GROUND_AUDIT', 'SOURCE_PROJECTION_AUDIT',
        'MAP_DRAW_AUDIT', 'STALE_MAP_DRAW_AUDIT', 'PLAN_DRAW_AUDIT',
        'ASTAR_AUDIT', 'PLAN_AUDIT', 'PLAN_DISPLAY_STALE', 'CAPTURE_AUDIT',
        'CAPTURE_REJECT', 'CUE_AUDIT', 'SESSION_START')


def number(value):
    try:
        value = float(str(value).removesuffix('ms'))
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def stats(values):
    values = [v for v in values if v is not None and math.isfinite(v)]
    if not values:
        return None
    ordered = sorted(values)
    return dict(n=len(values), min=min(values), median=statistics.median(values),
                mean=statistics.mean(values), max=max(values),
                p90=ordered[min(len(values)-1, int(len(values)*.9))])


def parse_events(text):
    events = {tag: [] for tag in TAGS}
    for index, line in enumerate(text.splitlines()):
        # Anchored tags prevent STALE_MAP_DRAW_AUDIT matching MAP_DRAW_AUDIT.
        match = re.search(r'(?:^|\s)(' + '|'.join(TAGS) + r')(?:\s|$)', line)
        if not match:
            continue
        record = dict(re.findall(r'(\w+)=([^\s]+)', line))
        stamp = re.search(r'(?:NavigationAudit:\s*|^)(\d{12,})\s+' + match[1], line)
        if stamp:
            record['wall_ms'] = int(stamp[1])
        pid = re.match(r'\d\d-\d\d\s+\S+\s+(\d+)\s+\d+\s+\w\s+', line)
        record['pid'] = pid[1] if pid else 'unknown'
        record['line'] = index + 1
        record['raw'] = line
        events[match[1]].append(record)
    return events


def identity(record):
    frame = number(record.get('frame'))
    if frame is None:
        return None
    return record.get('pid', 'unknown'), record.get('generation', 'unknown'), frame


def distinct(records):
    seen, result = set(), []
    for record in records:
        key = identity(record)
        if key is None or key in seen:
            continue
        seen.add(key)
        result.append(record)
    return result


def stage(records, key):
    values = [number(r.get(key)) for r in records]
    values = [v for v in values if v is not None and v >= 0]
    first_by_generation, continuous, seen = [], [], set()
    for r in records:
        value = number(r.get(key))
        if value is None or value < 0:
            continue
        session = r.get('pid', 'unknown'), r.get('generation', 'unknown')
        (continuous if session in seen else first_by_generation).append(value)
        seen.add(session)
    return dict(all=stats(values), first20=stats(values[:20]), first20_values=values[:20],
                first_observed_per_generation=stats(first_by_generation),
                continuous=stats(continuous),
                first_observed_note='First observed in this recording, not proof of cold start.')


def refresh(records, clock='wall_ms', scale=1):
    intervals, resets = [], 0
    for prev, cur in zip(records, records[1:]):
        if (prev.get('pid'), prev.get('generation')) != (cur.get('pid'), cur.get('generation')):
            resets += 1
            continue
        left, right = number(prev.get(clock)), number(cur.get(clock))
        if left is None or right is None:
            continue
        delta = (right-left)*scale
        if delta > 0:
            intervals.append(delta)
        else:
            resets += 1
    mean = statistics.mean(intervals) if intervals else None
    return dict(distinct_frames=len(records), interval_ms=stats(intervals),
                first20_intervals_ms=stats(intervals[:20]),
                first20_values_ms=intervals[:20], hz=1000/mean if mean else None,
                clock=clock, discontinuities_excluded=resets)


def matching_draws(records, semantic_frames):
    """Old live-depth/UI frames cannot qualify as a Swin semantic refresh."""
    matched, unmatched, seen = [], [], set()
    for r in records:
        frame = number(r.get('frame'))
        # FRAME uses %.6f, Canvas uses Double.toString: allow only its rounding
        # error (one microsecond), far below a D455f frame interval.
        candidates = [f for f in semantic_frames if frame is not None
                      and number(f.get('frame')) is not None
                      and abs(number(f.get('frame'))-frame) <= .000001
                      and f.get('pid') == r.get('pid')
                      and ('generation' not in r or f.get('generation') == r['generation'])]
        # An absent generation is acceptable only when identity is unambiguous.
        keys = {identity(f) for f in candidates}
        if len(keys) != 1:
            unmatched.append(r)
            continue
        key = keys.pop()
        if key in seen:
            continue
        seen.add(key)
        resolved = dict(r, generation=key[1])
        matched.append(resolved)
    return matched, unmatched


def navigation_ready(text):
    events = parse_events(text)
    frames = distinct(events['FRAME_AUDIT'])
    draws, _ = matching_draws(events['MAP_DRAW_AUDIT'] + events['PLAN_DRAW_AUDIT'], frames)
    plans, _ = matching_draws(events['PLAN_AUDIT'], frames)
    return bool(frames and (draws or plans))


def parse_resources(lines):
    resources = []
    for line in lines.splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        raw = r.get('raw', '')
        cpu = re.search(r'^cpu\s+([\d ]+)(?:\r?$)', raw, re.M)
        r['cpu_ticks'] = [int(v) for v in cpu[1].split()] if cpu else None
        r['cpu_cores'] = len(re.findall(r'^cpu\d+\s', raw, re.M))
        uptime = re.match(r'\s*(\d+(?:\.\d+)?)', raw)
        r['uptime'] = float(uptime[1]) if uptime else None
        r['battery'] = {}
        for key in ('Charge counter', 'voltage', 'temperature', 'level', 'status',
                    'AC powered', 'USB powered', 'Wireless powered', 'Dock powered'):
            m = re.search(r'^\s*' + re.escape(key) + r':\s*(\S+)', raw, re.M)
            if m:
                r['battery'][key] = m[1]
        mem = dict((k, int(v)) for k, v in re.findall(r'^(MemTotal|MemAvailable):\s+(\d+)', raw, re.M))
        if len(mem) == 2:
            r['device_used_mib'] = (mem['MemTotal']-mem['MemAvailable'])/1024
        for label, key in (('TOTAL PSS', 'pss_mib'), ('TOTAL RSS', 'rss_mib')):
            m = re.search(label + r':\s+(\d+)', r.get('app_memory', ''))
            if m:
                r[key] = int(m[1])/1024
        resources.append(r)
    return resources


def resource_metrics(resources):
    cores = []
    for prev, cur in zip(resources, resources[1:]):
        if not prev['cpu_ticks'] or not cur['cpu_ticks']:
            continue
        delta = [b-a for a, b in zip(prev['cpu_ticks'][:8], cur['cpu_ticks'][:8])]
        if len(delta) >= 5 and min(delta) >= 0 and sum(delta) > 0:
            cores.append((sum(delta)-delta[3]-delta[4])/sum(delta)*cur['cpu_cores'])
    elapsed = None
    if len(resources) >= 2 and resources[0]['uptime'] is not None and resources[-1]['uptime'] is not None:
        elapsed = resources[-1]['uptime']-resources[0]['uptime']
    charge = [number(r['battery'].get('Charge counter')) for r in resources]
    voltage = [number(r['battery'].get('voltage')) for r in resources]
    valid_counters = len(resources) >= 2 and all(v is not None for v in charge+voltage)
    discharging = bool(resources) and all(r['battery'].get('status') == '3' and
        all(r['battery'].get(k) == 'false' for k in ('AC powered', 'USB powered', 'Wireless powered')) and
        r['battery'].get('Dock powered', 'false') == 'false' for r in resources)
    delta = charge[0]-charge[-1] if valid_counters else None
    volts = statistics.mean(voltage)/1000 if valid_counters else None
    counter_monotonic = valid_counters and all(b <= a for a, b in zip(charge, charge[1:]))
    power_valid = bool(discharging and counter_monotonic and elapsed is not None and elapsed >= 240 and delta > 0)
    power = dict(valid_discharge_window=power_valid, duration_seconds=elapsed,
        ppt_240s_window=bool(elapsed is not None and elapsed >= 240),
        minimum_duration_seconds=240,
        charge_delta_uah=delta, reported_voltage_mean_v=volts,
        estimated_whole_device_w=delta/1e6*volts*3600/elapsed if power_valid else None,
        all_samples_discharging=discharging, charge_counter_monotonic=bool(counter_monotonic),
        method='Whole-device charge-counter difference × reported mean voltage / elapsed time; not an external meter. Includes attached camera load supplied by phone.')
    return dict(whole_device_busy_cpu_cores=stats(cores),
        app_pss_mib=stats([r['pss_mib'] for r in resources if 'pss_mib' in r]),
        app_rss_mib=stats([r['rss_mib'] for r in resources if 'rss_mib' in r]),
        whole_device_used_ram_mib=stats([r['device_used_mib'] for r in resources if 'device_used_mib' in r]),
        battery_discharge=power)


def analyze(text, resource_text='', manifest=None, full_text=None):
    manifest = manifest or {}
    full_text = text if full_text is None else full_text
    events = parse_events(text)
    frames = distinct(events['FRAME_AUDIT'])
    inference = distinct(events['SWIN_INFERENCE_AUDIT'])
    # Legacy logs lack the precise inference event. State the lower-precision fallback.
    model_records = inference or frames
    map_draws, unmatched = matching_draws(events['MAP_DRAW_AUDIT'], frames)
    optional_plan_draws, unmatched_plans = matching_draws(events['PLAN_DRAW_AUDIT'], frames)
    plans = events['PLAN_AUDIT']
    actual_searches = [r for r in events['ASTAR_AUDIT'] if (number(r.get('searches')) or 0) > 0]
    captures = events['CAPTURE_AUDIT']
    sensor_rates = []
    for left, right in zip(captures, captures[1:]):
        dt = (number(right.get('frame')) or 0)-(number(left.get('frame')) or 0)
        dr = (number(right.get('rgb')) or 0)-(number(left.get('rgb')) or 0)
        if left.get('pid') == right.get('pid') and dt > 0 and dr > 0:
            sensor_rates.append(dr/dt)
    raw_models = [dict(re.findall(r'(\w+)=([^\s]+)', line)) for line in text.splitlines()
                  if 'inference returned backend=' in line]
    runner = [dict(re.findall(r'(\w+)=([^\s]+)', line)) for line in text.splitlines()
              if 'SwinLPlan:' in line and 'complete ms=' in line]
    precise_cache = [r for r in inference if 'cold_compiles' in r]
    cache_records = precise_cache or runner
    full_inference = distinct(parse_events(full_text)['SWIN_INFERENCE_AUDIT'])
    precise_full_cache = [r for r in full_inference if 'cold_compiles' in r]
    full_runner = [dict(re.findall(r'(\w+)=([^\s]+)', line)) for line in full_text.splitlines()
                   if 'SwinLPlan:' in line and 'complete ms=' in line]
    full_cache_records = precise_full_cache or full_runner
    cold_events = lambda records: sum((number(r.get('cold_compiles')) or 0) > 0 for r in records)
    cold_total = lambda records: sum(int(number(r.get('cold_compiles')) or 0) for r in records)
    cue_lines = [r['raw'] for r in events['CUE_AUDIT']]
    freshness = [r for r in map_draws if 'within_legacy_1500ms' in r or 'within_1500ms' in r]
    legacy_freshness = dict(checked_draws=len(freshness),
        within_legacy_1500ms=sum(r.get('within_legacy_1500ms', r.get('within_1500ms')) == 'true' for r in freshness),
        scope='Legacy 1500 ms diagnostic only; does not describe the Swin semantic-map policy or movement authorization.',
        used_for_movement_authorization=False)
    report = dict(schema_version=2, package=PACKAGE, recording=manifest,
        measurement_scope='Physical RGB-D navigation logs; acquisition evidence is reported, not synthesized.',
        actual_rgb_depth_evidence=bool(captures), rgb_depth_same_clock=all(r.get('same_clock') == 'true' for r in captures) if captures else None,
        sensor_fps=statistics.mean(sensor_rates) if sensor_rates else None,
        rgb_depth_skew_ms=stage(captures, 'skew_ms'),
        semantic_model_ms=stage(model_records, 'model_ms'),
        inference_metric_source='SWIN_INFERENCE_AUDIT precise milliseconds' if inference else 'FRAME_AUDIT integer milliseconds (legacy fallback)',
        model_timing_scope='Swin-L NPU whole-network runner call, including host dispatch/cache compile; excludes RGB preprocessing, output decode and mapping. Not bare silicon time.',
        preprocess_ms=stage(model_records, 'prep_ms'), decode_ms=stage(model_records, 'decode_ms'),
        semantic_refresh=refresh(frames), inference_completion_refresh=refresh(inference),
        semantic_input_interval=refresh(frames, 'frame', 1000),
        semantic_map_new_draw_refresh=refresh(map_draws),
        plan_draw_refresh=refresh(map_draws),
        plan_ui_submission_refresh=refresh(distinct(plans)),
        optional_plan_draw_refresh=refresh(optional_plan_draws),
        draw_all_events_refresh=refresh(events['MAP_DRAW_AUDIT']),
        plan_all_draw_events_refresh=refresh(events['PLAN_DRAW_AUDIT']),
        semantic_capture_to_map_ms=stage(frames, 'age_ms'),
        inference_capture_age_ms=stage(inference, 'capture_age_ms'),
        semantic_cloud_ms=stage(frames, 'cloud_ms'), octomap_insert_ms=stage(frames, 'insert_ms'),
        semantic_grid_ms=stage(frames, 'grid_ms'), semantic_map_ms=stage(frames, 'map_ms'),
        input_queue_ms=stage(frames, 'queue_ms'), pose_wait_ms=stage(frames, 'pose_wait_ms'),
        map_queue_ms=stage(events['MAP_QUEUE_AUDIT'], 'map_queue_ms'),
        capture_to_semantic_draw_ms=stage(map_draws, 'capture_to_draw_ms'),
        planning_attempt_ms=stage(plans, 'plan_ms'),
        planning_executed_ms=stage([r for r in plans if r.get('planned') == 'true'], 'plan_ms'),
        plan_ui_queue_ms=stage(plans, 'ui_queue_ms'), map_to_plan_ms=stage(plans, 'map_to_plan_ms'),
        capture_to_plan_ui_submission_ms=stage(plans, 'capture_to_display_ms'),
        astar_when_searching_ms=stage(actual_searches, 'search_ms'),
        astar_search_call_counts=stats([number(r.get('searches')) for r in events['ASTAR_AUDIT']]),
        astar_success_events=sum(r.get('success') == 'true' for r in actual_searches),
        astar_reuse_or_no_search_events=sum((number(r.get('searches')) or 0) == 0 for r in events['ASTAR_AUDIT']),
        backend_observed=sorted({r.get('backend') for r in raw_models if r.get('backend')}),
        runner_compile_ms=stage(raw_models, 'compile'), runner_compute_ms=stage(raw_models, 'compute'),
        runner_cache_hits=stats([number(r.get('cache_hits')) for r in cache_records]),
        runner_cold_compiles=stats([number(r.get('cold_compiles')) for r in cache_records]),
        cache_metric_source='SWIN_INFERENCE_AUDIT per-frame counters' if precise_cache else 'SwinLPlan complete counters (legacy fallback)',
        cold_compile_events=cold_events(full_cache_records),
        cold_compiled_subgraphs_total=cold_total(full_cache_records),
        cold_compile_events_measurement=cold_events(cache_records),
        cold_compiled_subgraphs_measurement=cold_total(cache_records),
        cold_compile_event_scope='Whole recording, including the wait preamble; measurement-window counts are separate. Never add duplicate SwinLPlan and SWIN_INFERENCE_AUDIT records.',
        cpu_fallback_observed='cpu_fallback=true' in full_text,
        cache_state_claim=manifest.get('cache_state', 'unknown; cold startup not controlled'),
        first_run_note='First observed sample and later samples are separate. Cache hits/cold_compiles are observed separately; absence of cold compile logs is not proof of a controlled cold run.',
        moving_cue_events=sum(bool(re.search(r'cue=(?:直走|向左|向右|左转|右转)', s)) for s in cue_lines),
        stop_cue_events=sum('cue=停止' in s for s in cue_lines),
        capture_reject_events=len(events['CAPTURE_REJECT']),
        legacy_1500ms_diagnostic=legacy_freshness,
        semantic_map_drop_events=len(events['SEMANTIC_MAP_DROP']),
        source_ground_audit_events=len(events['SOURCE_GROUND_AUDIT']),
        unmatched_draw_events=len(unmatched), unmatched_plan_draw_events=len(unmatched_plans),
        duplicate_frame_events=len(events['FRAME_AUDIT'])-len(frames),
        duplicate_inference_events=len(events['SWIN_INFERENCE_AUDIT'])-len(inference),
        event_counts={k: len(v) for k, v in events.items()}, fatal_error='FATAL EXCEPTION' in full_text,
        drop_accounting='CAPTURE_AUDIT is sampled. RGB counters estimate sensor rate. Input replacement, USB losses, and full model drops require explicit counters; UI redraws/reprojections never count as new semantic inference.')
    # Compatibility for existing report readers, now explicitly semantic-only.
    report['live_map_refresh'] = report['semantic_map_new_draw_refresh']
    report['live_publish_refresh'] = report['plan_draw_refresh']
    report['live_capture_to_draw_ms'] = report['capture_to_semantic_draw_ms']
    report['live_planning_ms'] = report['planning_executed_ms']
    report['live_planning_attempt_ms'] = report['planning_attempt_ms']
    report.update(resource_metrics(parse_resources(resource_text)))
    report['limitations'] = []
    if not frames:
        report['limitations'].append('No completed semantic FRAME_AUDIT maps observed.')
    if not inference:
        report['limitations'].append('Precise SWIN_INFERENCE_AUDIT unavailable; model summary uses completed maps only and misses inferences discarded before mapping.')
    if unmatched:
        report['limitations'].append('Unmatched MAP_DRAW events excluded from semantic refresh (may be legacy depth-only frames, generation ambiguity, or map completed outside recording).')
    if not map_draws:
        report['limitations'].append('No matched MAP_DRAW_AUDIT: actual Canvas drawing interval unavailable.')
    return report, events


def write_summary(output, report):
    def cell(value):
        return '未取得' if value is None else f'{value:.2f}'
    def mean(key, group='all'):
        value = report[key].get(group)
        return value.get('mean') if value else None
    def interval(key):
        value = report[key].get('first20_intervals_ms')
        return value.get('mean') if value else None
    power = report['battery_discharge']
    rows = [
        ('Swin-L NPU整网调用', mean('semantic_model_ms'), 'ms；排除RGB预处理、解码、点云；包含运行器调度/缓存编译'),
        ('连续帧Swin-L整网调用', mean('semantic_model_ms', 'continuous'), 'ms；每代排除首次观测，不冒充冷启动测试'),
        ('不同语义地图完成间隔，前20个间隔', interval('semantic_refresh'), 'ms；FRAME_AUDIT原始帧身份去重'),
        ('不同语义地图完成吞吐', report['semantic_refresh']['hz'], 'Hz；完整语义点云/建图完成'),
        ('不同语义地图实际绘制间隔，前20个间隔', interval('semantic_map_new_draw_refresh'), 'ms；仅与完成语义帧匹配的首次绘制'),
        ('规划结果UI提交间隔，前20个间隔', interval('plan_ui_submission_refresh'), 'ms；PLAN_AUDIT，不是实际Canvas绘制'),
        ('完整规划平均，前20次实际进入规划', mean('planning_executed_ms', 'first20'), 'ms；包含A*之外规划工作'),
        ('单独A*，前20次发生搜索的调用', mean('astar_when_searching_ms', 'first20'), 'ms；不把复用的0ms混入搜索平均'),
        ('采集至语义地图完成', mean('semantic_capture_to_map_ms'), 'ms；原采集时间，不是重投影时刻'),
        ('采集至语义地图实际绘制', mean('capture_to_semantic_draw_ms'), 'ms；只含匹配语义图'),
        ('CPU整机忙碌等效核数', (report['whole_device_busy_cpu_cores'] or {}).get('mean'), '核；整机/proc/stat，含相机及系统'),
        ('应用PSS', (report['app_pss_mib'] or {}).get('mean'), 'MiB；不能与整机RAM混用'),
        ('应用RSS', (report['app_rss_mib'] or {}).get('mean'), 'MiB'),
        ('整机已用RAM', (report['whole_device_used_ram_mib'] or {}).get('mean'), 'MiB；MemTotal−MemAvailable'),
        ('整机放电功耗估算', power['estimated_whole_device_w'], 'W；充电或计数器异常时留空，非外接功率仪'),
        ('有效资源采样时长', power['duration_seconds'], '秒；PPT窗口要求至少240秒'),
    ]
    text = '# D455f + Swin-L 真实导航测量\n\n'
    text += '本报告只描述记录到的指标；功能可用性须结合停止原因、实际路径和场景复核。\n\n'
    text += '| 指标 | 结果 | 口径 |\n|---|---:|---|\n'
    text += ''.join(f'| {name} | {cell(value)} | {note} |\n' for name, value, note in rows)
    text += '\n所有“前20次”均为最多20次，样本不足不补齐；实际n、原值、中位数、P90见metrics.json。\n\n'
    text += '| 阶段 | n | 平均ms | P90ms |\n|---|---:|---:|---:|\n'
    stages = [('预处理', 'preprocess_ms'), ('解码', 'decode_ms'), ('输入排队', 'input_queue_ms'),
              ('地图排队', 'map_queue_ms'), ('点云', 'semantic_cloud_ms'), ('等位姿', 'pose_wait_ms'),
              ('OctoMap插入', 'octomap_insert_ms'), ('栅格', 'semantic_grid_ms'), ('完整建图', 'semantic_map_ms')]
    for name, key in stages:
        s = report[key]['all'] or {}
        text += f"| {name} | {s.get('n', 0)} | {cell(s.get('mean'))} | {cell(s.get('p90'))} |\n"
    text += '\n模型后端观测：' + (', '.join(report['backend_observed']) or '未记录，不能仅凭包名证明') + '。\n'
    text += f"\n整个记录（含等待）发生冷编译的推理调用：{report['cold_compile_events']}次，共{report['cold_compiled_subgraphs_total']}个子图；测量窗口内为{report['cold_compile_events_measurement']}次。优先逐帧SWIN计数，未叠加重复运行器日志。缓存条件标签：{report['cache_state_claim']}。首次观测不等于清缓存冷启动。\n"
    text += f"\n资源窗口达到240秒：{power['ppt_240s_window']}；全程放电且电荷计数有效：{power['valid_discharge_window']}。\n"
    artifact = report.get('model_artifact') or {}
    if artifact:
        text += '\n电脑端模型元数据：\n\n| 项 | 值 |\n|---|---|\n'
        for label, key in [('文件', 'file'), ('大小MB', 'decimal_mb'), ('大小MiB', 'mib'),
                           ('低级算子数量', 'operations'), ('子图数', 'subgraphs'), ('MD5', 'md5')]:
            text += f"| {label} | {artifact.get(key, '未提供')} |\n"
    text += '\n模型文件大小、算子数量和数据类型由model_artifact.json（如提供）记录；它是电脑端元数据，不能代替手机实际分配/安装字节校验。\n'
    text += '\nFP16/INT32等数据类型应据模型计划解析；CPU仍参与采集、VINS、点云、规划和UI，“禁止模型CPU回退”不等于整个App只用NPU。\n'
    text += f"\n未匹配语义帧的绘图事件：{report['unmatched_draw_events']}，已排除出语义刷新速度。\n"
    text += f"\n行走提示：{report['moving_cue_events']}；停止提示：{report['stop_cue_events']}。\n"
    text += '\nwithin_legacy_1500ms（旧日志within_1500ms）仅用于历史1500毫秒阈值诊断，不表示当前Swin地图有效期或获准行走。\n'
    text += f"\n语义地图丢弃日志：{report['semantic_map_drop_events']}；源码地面处理审计：{report['source_ground_audit_events']}。原始字段见events.json。\n"
    if report['limitations']:
        text += '\n限制：\n\n' + ''.join('- '+item+'\n' for item in report['limitations'])
    text += '\n原始证据：live.log、resources.jsonl、measurement.json、thermal-start/end.txt、brightness.txt、screen.png。\n'
    (output/'REPORT.zh-CN.md').write_text(text, encoding='utf-8')


def collect(args):
    output = args.output
    def adb(*command):
        result = subprocess.run([args.adb, '-s', args.device, *command], capture_output=True, timeout=30)
        if result.returncode:
            raise RuntimeError(result.stderr.decode(errors='replace'))
        return result.stdout
    def save(name, *command):
        (output/name).write_bytes(adb(*command))
    def resource_sample(memory=False):
        start = time.monotonic()
        raw = adb('shell', 'cat /proc/uptime; cat /proc/stat; cat /proc/meminfo; dumpsys battery').decode(errors='replace')
        record = dict(host_monotonic=(start+time.monotonic())/2, raw=raw)
        if memory:
            record['app_memory'] = adb('shell', 'dumpsys', 'meminfo', PACKAGE).decode(errors='replace')
        return record
    save('brightness.txt', 'shell', 'settings get system screen_brightness; settings get system screen_brightness_mode')
    save('package.txt', 'shell', 'dumpsys', 'package', PACKAGE)
    save('original-package.txt', 'shell', 'pm', 'path', 'com.seaway.guideassistant')
    save('usb.txt', 'shell', 'dumpsys', 'usb')
    manifest = dict(schema_version=2, device=args.device, requested_seconds=args.seconds,
        cache_state=args.cache_state, condition_label=args.condition_label,
        sample_interval_seconds=2, app_memory_every_n_samples=5,
        no_input_or_pose_injection=True, no_battery_state_simulation=True,
        recording_start_host_wall_ms=int(time.time()*1000))
    log_path = output/'live.log'
    with log_path.open('wb') as log, (output/'resources.jsonl').open('w', encoding='utf-8') as records:
        logger = subprocess.Popen([args.adb, '-s', args.device, 'logcat', '-v', 'threadtime', '-T', '1', '-s',
            'NavigationAudit:I', 'SemanticPipeline:I', 'SwinLPlan:I', 'MobileNavigation:I', 'AndroidRuntime:E'],
            stdout=log, stderr=subprocess.STDOUT)
        try:
            if args.wait_for_navigation:
                print('WAITING_FOR_NAVIGATION: completed semantic map plus matching map/plan event', flush=True)
                deadline = time.monotonic()+600
                while not navigation_ready(log_path.read_text(encoding='utf-8', errors='replace')):
                    if logger.poll() is not None:
                        raise RuntimeError('ADB logger disconnected while waiting')
                    if time.monotonic() > deadline:
                        raise TimeoutError('No completed Swin semantic map/navigation drawing within ten minutes')
                    time.sleep(1)
            save('thermal-start.txt', 'shell', 'dumpsys', 'thermalservice')
            manifest['measurement_log_start_byte'] = log_path.stat().st_size
            manifest['measurement_start_host_wall_ms'] = int(time.time()*1000)
            print('MEASUREMENT_START', flush=True)
            start = time.monotonic()
            next_sample, index = start, 0
            while True:
                if logger.poll() is not None:
                    raise RuntimeError('ADB logger disconnected during measurement')
                record = resource_sample(memory=index % 5 == 0)
                records.write(json.dumps(record, ensure_ascii=False)+'\n')
                records.flush()
                index += 1
                elapsed = time.monotonic()-start
                if elapsed >= args.seconds:
                    break
                if index % 15 == 0:
                    print('PROGRESS_SECONDS', round(elapsed, 1), flush=True)
                next_sample += 2
                time.sleep(max(0, min(next_sample-time.monotonic(), args.seconds-elapsed)))
            manifest['measurement_log_end_byte'] = log_path.stat().st_size
            manifest['measurement_elapsed_host_seconds'] = time.monotonic()-start
            manifest['measurement_complete'] = True
        finally:
            logger.terminate()
            try:
                logger.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.kill()
                logger.wait(timeout=5)
            (output/'measurement.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    save('thermal-end.txt', 'shell', 'dumpsys', 'thermalservice')
    save('screen.png', 'exec-out', 'screencap', '-p')
    save('brightness-end.txt', 'shell', 'settings get system screen_brightness; settings get system screen_brightness_mode')
    print('MEASUREMENT_COMPLETE', flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', help='Wireless ADB IP:port; required only for collection')
    parser.add_argument('--adb', default=ADB)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=float, default=240)
    parser.add_argument('--analyze-only', action='store_true')
    parser.add_argument('--wait-for-navigation', action='store_true')
    parser.add_argument('--cache-state', choices=('as-observed', 'warm-requested', 'cold-requested'), default='as-observed',
                        help='Label only: this tool never deletes model caches or restarts apps.')
    parser.add_argument('--condition-label', default='')
    parser.add_argument('--plan-info', type=Path, help='Optional preverified computer-side model metadata JSON; never treated as device allocation proof')
    args = parser.parse_args(argv)
    if not args.analyze_only and not args.device:
        parser.error('--device is required for collection')
    if args.seconds <= 0:
        parser.error('--seconds must be positive')
    args.output.mkdir(parents=True, exist_ok=True)
    if not args.analyze_only:
        collect(args)
    manifest_path = args.output/'measurement.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig')) if manifest_path.exists() else {}
    data = (args.output/'live.log').read_bytes()
    start, end = manifest.get('measurement_log_start_byte', 0), manifest.get('measurement_log_end_byte', len(data))
    text = data[start:end].decode('utf-8', errors='replace')
    resources_path = args.output/'resources.jsonl'
    resource_text = resources_path.read_text(encoding='utf-8-sig') if resources_path.exists() else ''
    report, events = analyze(text, resource_text, manifest, data.decode('utf-8', errors='replace'))
    if args.plan_info:
        info = json.loads(args.plan_info.read_text(encoding='utf-8-sig'))
        (args.output/'model_artifact.json').write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
    artifact = args.output/'model_artifact.json'
    report['model_artifact'] = json.loads(artifact.read_text(encoding='utf-8-sig')) if artifact.exists() else None
    brightness = args.output/'brightness.txt'
    brightness_end = args.output/'brightness-end.txt'
    report['display_conditions'] = dict(
        start=brightness.read_text(errors='replace').strip() if brightness.exists() else None,
        end=brightness_end.read_text(errors='replace').strip() if brightness_end.exists() else None,
        unchanged=brightness.read_bytes() == brightness_end.read_bytes() if brightness.exists() and brightness_end.exists() else None,
        note='Raw brightness and auto-brightness mode recorded; no display settings changed. Keep screen state and scene controlled for comparisons.')
    (args.output/'metrics.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (args.output/'events.json').write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding='utf-8')
    write_summary(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
