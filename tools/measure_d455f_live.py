"""Collect actual D455f + Swin-L logs without injecting frames, poses or routes."""
import argparse
import json
from pathlib import Path
import re
import statistics
import subprocess
import time

p = argparse.ArgumentParser()
p.add_argument('--device', required=True)
p.add_argument('--output', type=Path, required=True)
p.add_argument('--seconds', type=int, default=60)
p.add_argument('--analyze-only', action='store_true')
a = p.parse_args()
a.output.mkdir(parents=True, exist_ok=True)
adb = r'C:\Users\27728\AppData\Local\Android\Sdk\platform-tools\adb.exe'

def run(*args):
    return subprocess.run([adb, '-s', a.device, *args], capture_output=True, timeout=30)

def snapshot(name, *args):
    result = run(*args)
    (a.output / name).write_bytes(result.stdout)

def stats(values):
    if not values:
        return None
    ordered = sorted(values)
    return dict(n=len(values), min=min(values), median=statistics.median(values),
                mean=statistics.mean(values), p90=ordered[min(len(values)-1, int(.9*len(values)))],
                max=max(values))

log_path = a.output/'live.log'
def collect():
  with log_path.open('wb') as log:
    process = subprocess.Popen([adb, '-s', a.device, 'logcat', '-v', 'threadtime', '-T', '1', '-s',
        'SwinLPlan:I', 'SemanticPipeline:I', 'NavigationAudit:I', 'MobileNavigation:I',
        'UsbManager:E', 'UsbUserPermissionManager:I',
        'SwinAudit:I', 'AndroidRuntime:E'], stdout=log, stderr=subprocess.STDOUT)
    try:
        print('WAITING_FOR_NAVIGATION', flush=True)
        deadline = time.monotonic()+600
        while time.monotonic() < deadline:
            if b'CUE_AUDIT' in log_path.read_bytes():
                break
            if process.poll() is not None:
                raise RuntimeError('ADB logger stopped')
            time.sleep(1)
        else:
            raise TimeoutError('Navigation was not started')
        start = time.monotonic()
        print('RECORDING', a.seconds, flush=True)
        snapshot('thermal-start.txt', 'shell', 'dumpsys', 'thermalservice')
        snapshot('usb.txt', 'shell', 'dumpsys', 'usb')
        while time.monotonic()-start < a.seconds:
            if process.poll() is not None:
                raise RuntimeError('ADB logger stopped during capture')
            time.sleep(1)
        snapshot('thermal-end.txt', 'shell', 'dumpsys', 'thermalservice')
        snapshot('memory.txt', 'shell', 'dumpsys', 'meminfo', 'com.seaway.guideassistant.swinl')
        snapshot('screen.png', 'exec-out', 'screencap', '-p')
        snapshot('package.txt', 'shell', 'dumpsys', 'package', 'com.seaway.guideassistant.swinl')
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()

if not a.analyze_only:
    collect()

text = log_path.read_text(encoding='utf-8', errors='replace')
frames = []
plans = []
models = []
capture = []
draws = []
for line in text.splitlines():
    values = dict(re.findall(r'(\w+)=([^\s]+)', line))
    audit_time = re.search(r'NavigationAudit:\s+(\d+) ', line)
    if audit_time:
        values['audit_wall_ms'] = audit_time.group(1)
    if 'FRAME_AUDIT ' in line:
        frames.append(values)
    elif 'PLAN_DISPLAY_STALE ' in line or 'PLAN_AUDIT ' in line:
        plans.append(values)
    elif 'inference returned ' in line:
        models.append(values)
    elif 'CAPTURE_AUDIT ' in line:
        capture.append(values)
    elif 'MAP_DRAW_AUDIT ' in line:
        draws.append(values)

def metric(records, key):
    return stats([float(r[key].removesuffix('ms')) for r in records if key in r])

capture_times = [float(r['frame']) for r in capture]
rgb = [int(r['rgb']) for r in capture]
frame_times = [float(r['frame']) for r in frames]
span = (capture_times[-1]-capture_times[0]) if len(capture_times)>1 else 0
input_intervals = [(y-x)*1000 for x,y in zip(frame_times, frame_times[1:])]
map_completed = [float(r['audit_wall_ms']) for r in frames if 'audit_wall_ms' in r]
intervals = [y-x for x,y in zip(map_completed, map_completed[1:])]
report = dict(
    package='com.seaway.guideassistant.swinl', requested_record_seconds=a.seconds,
    input_width=384, input_height=288, actual_sensor_data=True,
    fixed_image_fixture=False, synthetic_pose=False, freshness_limit_ms=1500,
    completed_maps=len(frames), map_interval_ms=stats(intervals),
    semantic_input_interval_ms=stats(input_intervals),
    capture_span_seconds=span,
    map_refresh_hz=1000/statistics.mean(intervals) if intervals else None,
    camera_rgb_counter_hz=(rgb[-1]-rgb[0])/span if span else None,
    capture_pairs_logged=len(capture), same_clock=all(r.get('same_clock')=='true' for r in capture),
    rgb_depth_skew_ms=metric(capture, 'skew_ms'),
    model_ms=metric(frames, 'model_ms'), queue_ms=metric(frames, 'queue_ms'),
    preprocess_ms_integer_log=metric(frames, 'prep_ms'),
    decode_ms_integer_log=metric(frames, 'decode_ms'),
    pointcloud_ms=metric(frames, 'cloud_ms'), octomap_insert_ms=metric(frames, 'insert_ms'),
    grid_ms=metric(frames, 'grid_ms'), map_total_ms=metric(frames, 'map_ms'),
    capture_to_map_ms=metric(frames, 'age_ms'), planning_attempt_ms=metric(plans, 'astar_ms'),
    planning_executed_ms=metric([r for r in plans if r.get('planned')=='true'], 'astar_ms'),
    capture_to_draw_ms=metric(draws, 'capture_to_draw_ms'),
    compile_ms=metric(models, 'compile'), compute_ms=metric(models, 'compute'),
    freshness_pass_count=sum(float(r['age_ms'])<1500 for r in frames),
    diagnostic_plans_computed=sum(r.get('planned')=='true' for r in plans),
    diagnostic_paths_found=sum(r.get('success')=='true' for r in plans),
    moving_cues=sum('CUE_AUDIT' in s and 'cue=停止' not in s and 'cue= ' not in s for s in text.splitlines()),
    cold_compile_events=len(re.findall(r'cold_compiles=[1-9]\d*', text)),
    cpu_fallback_observed='cpu_fallback=true' in text,
    camera_error_observed='SecurityException' in text,
    frames=frames, plans=plans,
    first_completed_frame=frames[0] if frames else None,
    continuous_model_ms=metric(frames[1:], 'model_ms'),
    continuous_capture_to_map_ms=metric(frames[1:], 'age_ms'),
    drop_accounting='CAPTURE_AUDIT is sampled; model deliberately replaces pending pairs. '
                    'USB loss count and exact replaced-frame count are not instrumented and cannot be inferred from sparse logs.'
)
(a.output/'metrics.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k not in ('frames','plans')}, ensure_ascii=False, indent=2), flush=True)
