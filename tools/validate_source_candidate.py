"""Record a built source-rules candidate after required local tests; does not use ADB."""
import hashlib
import argparse
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'artifacts/d455f-source-swinl-20261105'
EXPECTED_MD5 = 'd7fda40af91b1e7e4e0a2ef54517c16d'


def digest(stream, algorithm):
    h = hashlib.new(algorithm)
    for block in iter(lambda: stream.read(1024*1024), b''):
        h.update(block)
    return h.hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=OUTPUT)
    parser.add_argument('--version',default='1.4-swinl-npu-384-v14-source')
    args=parser.parse_args()
    output=args.output
    suites = []
    for file in (ROOT/'navoutdoor/build/test-results/testSwinlDebugUnitTest').glob('TEST-*.xml'):
        suite = ET.parse(file).getroot()
        suites.append(dict(name=suite.get('name'), **{
            key: int(suite.get(key, 0)) for key in ('tests', 'failures', 'errors', 'skipped')}))
    required = {'SourceCostReferenceTest', 'SourceFilterReferenceTest',
                'SourceCoordinateConventionTest', 'SourceGroundConfidenceTest',
                'SourceGroundDeviceReplayTest', 'SwinObservationTimingTest'}
    if '-v15-' in args.version:
        required.update({'SourceProjectionPoseTest','SourceCurrentPoseProjectionTest'})
    assert required <= {s['name'].split('.')[-1] for s in suites}, 'Required suite missing'
    assert not any(s['failures'] or s['errors'] or s['skipped'] for s in suites), 'Tests did not pass'
    replay_log=ET.parse(ROOT/'navoutdoor/build/test-results/testSwinlDebugUnitTest/TEST-com.elabrador.mobilenavigation.SourceGroundDeviceReplayTest.xml').getroot().findtext('system-out','')
    replay_line=next(line for line in replay_log.splitlines() if line.startswith('D455F_SOURCE_BOOST_REPLAY_TOTAL '))
    replay=dict(re.findall(r'(\w+)=(\d+)',replay_line))
    build_log = (output/'build.log').read_text(errors='replace')
    assert 'BUILD SUCCESSFUL' in build_log, 'Build not complete'
    metadata_path = ROOT/'app/build/outputs/apk/debug/output-metadata.json'
    metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
    assert metadata['applicationId'] == 'com.seaway.guideassistant.swinl'
    element = metadata['elements'][0]
    assert element['versionName'] == args.version
    apk = metadata_path.parent/element['outputFile']
    with apk.open('rb') as f:
        apk_sha = digest(f, 'sha256')
    with zipfile.ZipFile(apk) as archive:
        asset = 'assets/models/swinl-384.plan'
        with archive.open(asset) as f:
            plan_md5 = digest(f, 'md5')
        assert plan_md5 == EXPECTED_MD5, 'Packaged model changed'
        assert archive.getinfo(asset).file_size == 459572610
        descriptors = b''.join(archive.read(n) for n in archive.namelist()
                               if n.startswith('classes') and n.endswith('.dex'))
        for removed in ('LiveDepthMap', 'WorldObstacleMemory', 'DepthPoseQueue'):
            assert f'Lcom/elabrador/mobilenavigation/{removed};'.encode() not in descriptors, removed
        for added in ('SourceGroundConfidence', 'SemanticObservationPolicy', 'InferenceTiming'):
            assert f'Lcom/elabrador/mobilenavigation/{added};'.encode() in descriptors, added
        native_sha = hashlib.sha256(archive.read('lib/arm64-v8a/libelabrador_native.so')).hexdigest()
    with (ROOT/'navoutdoor/src/main/jniLibs/arm64-v8a/libelabrador_native.so').open('rb') as f:
        assert native_sha == digest(f, 'sha256'), 'Unexpected native map library'
    report = dict(validated_utc=datetime.now(timezone.utc).isoformat(),
                  tests=sum(s['tests'] for s in suites), failures=0, errors=0, suites=suites,
                  official_commit='c758acdca0e9d05bb6f52337daefdce9b2b9983e',
                  apk=str(apk), apk_bytes=apk.stat().st_size, apk_sha256=apk_sha,
                  package=metadata['applicationId'], version=element['versionName'],
                  plan_md5=plan_md5, native_octomap_sha256=native_sha,
                  old_geometric_map_classes_absent=True,
                  source_cost_oracle=dict(cases=92, cells=588800, numeric_tolerance=1),
                  source_filter_oracle=dict(cases=79, outputs=1516800, tolerance=0),
                  real_cloud_replay=dict(frames=int(replay['frames']),points=int(replay['points']),
                      plane_valid_frames=int(replay['valid_planes']),boosted_points=int(replay['boosted']),
                      native_tree_fusion_verified=False),
                  device_test_complete=False)
    output.mkdir(parents=True, exist_ok=True)
    (output/'validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='suites'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
