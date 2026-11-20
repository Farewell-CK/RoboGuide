"""Collect a sequential baseline/candidate/baseline comparison in the installed app."""
import argparse
import json
from pathlib import Path
import subprocess
import time

parser = argparse.ArgumentParser()
parser.add_argument('--device', required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
adb = r'C:\Users\27728\AppData\Local\Android\Sdk\platform-tools\adb.exe'
package = 'com.seaway.guideassistant.swinl'


def run(*arguments, check=True):
    result = subprocess.run([adb, '-s', args.device, *arguments], capture_output=True,
                            encoding='utf-8', errors='replace', timeout=60)
    if check and result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    return result


results = []
for name, baseline in [('baseline-before', True), ('optimized', False), ('baseline-after', True)]:
    run('shell', 'am', 'force-stop', package)
    run('shell', 'run-as', package, 'rm', '-f', 'files/swinl-app-benchmark.json')
    start_args = ['shell', 'am', 'start', '-W', '-n', package +
                  '/com.elabrador.mobilenavigation.SwinlBenchmarkActivity',
                  '--ez', 'baseline', str(baseline).lower()]
    if not baseline:
        start_args += ['--ez', 'burst', 'false']
    run(*start_args)
    print('Started', name, flush=True)
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        time.sleep(3)
        response = run('shell', 'run-as', package, 'cat', 'files/swinl-app-benchmark.json', check=False)
        try:
            report = json.loads(response.stdout)
        except (json.JSONDecodeError, ValueError):
            continue
        if 'success' not in report:
            continue
        (args.output / (name + '.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        pid = run('shell', 'pidof', package, check=False).stdout.strip()
        if pid:
            logs = run('logcat', '-d', '--pid=' + pid, '-s', 'SwinLBench:I', 'SwinLPlan:I', 'AndroidRuntime:E').stdout
            (args.output / (name + '.log')).write_text(logs, encoding='utf-8')
        if not report['success']:
            raise RuntimeError(name + ': ' + report.get('error', 'benchmark failed'))
        results.append(dict(name=name, warm_median_ms=report['warm_median_ms'],
                            passes=[p['total_ms'] for p in report['passes']]))
        print(name, 'warm_median_ms', report['warm_median_ms'], flush=True)
        break
    else:
        raise TimeoutError(name)

control = (results[0]['warm_median_ms'] + results[2]['warm_median_ms']) / 2
summary = dict(runs=results, baseline_midpoint_ms=control,
               optimized_ms=results[1]['warm_median_ms'],
               reduction_percent=(1 - results[1]['warm_median_ms'] / control) * 100)
(args.output / 'comparison.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
print(json.dumps(summary, indent=2), flush=True)
