#!/usr/bin/env python3
"""Collect native NAVI test, lint and final APK evidence without private data."""
import argparse
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile


def collect(root, apk, logs=()):
    reports = {}
    for module, variant in [('naviindoor', 'Debug'), ('app', 'Debug'), ('navoutdoor', 'SwinlDebug')]:
        folder = root / module / 'build/test-results' / ('test' + variant + 'UnitTest')
        files = sorted(folder.glob('TEST-*.xml'))
        if not files:
            raise RuntimeError('Missing unit test reports: ' + module)
        sums = dict(tests=0, failures=0, errors=0, skipped=0)
        for file in files:
            suite = ET.parse(file).getroot()
            for key in sums:
                sums[key] += int(suite.get(key, '0'))
        reports[module] = sums
    lint = ET.parse(root / 'naviindoor/build/reports/lint-results-debug.xml').getroot()
    lint_errors = sum(i.get('severity') in ('Error', 'Fatal') for i in lint.findall('issue'))
    lint_warnings = sum(i.get('severity') == 'Warning' for i in lint.findall('issue'))
    with zipfile.ZipFile(apk) as package:
        names = package.namelist()
        flutter = [n for n in names if n.startswith('assets/flutter_assets/') or n.endswith(('/libflutter.so', '/libapp.so'))]
        retained = {n: n in names for n in ['classes.dex', 'lib/arm64-v8a/librealsense2.so', 'assets/models/swinl-384.plan']}
    digest = hashlib.sha256()
    with apk.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    builds = {path.name: 'BUILD SUCCESSFUL' in path.read_text(errors='replace') for path in logs}
    success = all(v['tests'] > 0 and v['failures'] == 0 and v['errors'] == 0 for v in reports.values())
    success = success and lint_errors == 0 and not flutter and all(retained.values()) and all(builds.values())
    return dict(success=success, scope='自动化、编译与安装包核验；尚未进行真机现场导航验收',
                tests=reports, total_tests=sum(v['tests'] for v in reports.values()),
                lint=dict(errors=lint_errors, warnings=lint_warnings), builds=builds,
                apk=dict(name=apk.name, bytes=apk.stat().st_size, sha256=digest.hexdigest(),
                         flutter_entries=flutter, original_host_assets=retained))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apk', type=Path, required=True)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--log', type=Path, action='append', default=[])
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = collect(args.root, args.apk, args.log)
    text = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.write_text(text, encoding='utf-8')
    print(text, end='')
    if not report['success']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
