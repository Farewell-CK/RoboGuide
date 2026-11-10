"""Extract measured VINS latency for deterministic scheduling tests, not geometry replay."""
import argparse, datetime, json, re, statistics
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('log',type=Path);p.add_argument('--output',type=Path,required=True)
a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
delays=[]
for line in a.log.read_text(encoding='utf-8',errors='replace').splitlines():
    m=re.search(r'^(\d\d-\d\d \d\d:\d\d:\d\d\.\d+) .*VINS pose t=([\d.]+)',line)
    if not m:continue
    wall=datetime.datetime.strptime('2026-'+m[1],'%Y-%m-%d %H:%M:%S.%f').replace(tzinfo=datetime.timezone(datetime.timedelta(hours=8))).timestamp()
    delay=(wall-float(m[2]))*1000
    if delay>=0:delays.append(round(delay))
resource=Path('navoutdoor/src/test/resources/v10-measured-pose-delays-ms.txt')
resource.parent.mkdir(parents=True,exist_ok=True);resource.write_text('\n'.join(map(str,delays))+'\n',encoding='ascii')
report={'source':str(a.log),'pose_samples':len(delays),'min_ms':min(delays),'median_ms':statistics.median(delays),
 'mean_ms':statistics.mean(delays),'max_ms':max(delays),'within_250ms':sum(d<=250 for d in delays),
 'within_1500ms':sum(d<1500 for d in delays),'delays_ms':delays,
 'scope':'Measured pose latency drives deterministic queue tests at 10 Hz. This is a timing test; no RGB/depth geometry or NPU accuracy is replayed.'}
(a.output/'measured-pose-delay.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='delays_ms'},ensure_ascii=False,indent=2))
