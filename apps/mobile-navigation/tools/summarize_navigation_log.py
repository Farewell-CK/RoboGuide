"""Summarize valid navigation publications for one Android process, not UI refreshes."""
import argparse
import datetime
import json
import re
import statistics
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('log', type=Path)
parser.add_argument('--pid', type=int, required=True)
args = parser.parse_args()
lines = [line for line in args.log.read_text(encoding='utf-8-sig').splitlines()
         if re.match(r'\S+\s+\S+\s+' + str(args.pid) + r'\s', line)]
maps = []
for line in lines:
    if 'stages total=' not in line:
        continue
    fields = {k: float(v) for k, v in re.findall(
        r'(total|prep|model|decode|cloud|octomap|grid|pose_wait|points)=([0-9.]+)', line)}
    if fields.get('points', 0) <= 0:
        continue
    stamp = datetime.datetime.strptime('2026-' + line[:18], '%Y-%m-%d %H:%M:%S.%f')
    maps.append((stamp, fields))
result = {'pid': args.pid, 'valid_maps': len(maps),
          'missing_pose_skips': sum('semantic map skipped:' in line for line in lines)}
if len(maps) > 1:
    duration = (maps[-1][0] - maps[0][0]).total_seconds()
    result.update(duration_seconds=duration, valid_map_fps=(len(maps)-1)/duration,
                  stage_median_ms={key: statistics.median(row[key] for _, row in maps)
                                   for key in maps[0][1] if key != 'points'})
    gaps = sorted((maps[i][0] - maps[i-1][0]).total_seconds()*1000 for i in range(1,len(maps)))
    result['update_interval_ms'] = {'median': statistics.median(gaps),
                                   'p90': gaps[int((len(gaps)-1)*0.9)],
                                   'p95': gaps[int((len(gaps)-1)*0.95)], 'max': max(gaps)}
windows = [float(m.group(1)) for line in lines
           if (m := re.search(r'MAP_THROUGHPUT fps=([0-9.]+)', line))]
if windows:
    result['30_update_windows'] = {'count': len(windows), 'min_fps': min(windows),
                                  'median_fps': statistics.median(windows),
                                  'max_fps': max(windows)}
print(json.dumps(result, indent=2))
