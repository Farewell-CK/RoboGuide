"""Public-image smoke tests and concurrent DINO checks, not hallway accuracy.
Outputs predictions and timings without claiming dataset-level precision/recall.
"""
import argparse
import concurrent.futures
from io import BytesIO
import json
from pathlib import Path
import time
import requests
from PIL import Image

WORK = Path(__file__).resolve().parent


def post(port, path, data, frame_id):
    started = time.perf_counter()
    field = 'image' if path == '/encode/navigation' else 'file'
    response = requests.post(f'http://127.0.0.1:{port}' + path,
        files={field: ('sample.jpg', data, 'image/jpeg')},
        data={'frame_id': frame_id}, timeout=15)
    response.raise_for_status()
    return {'roundtrip_ms': round((time.perf_counter() - started) * 1000), 'body': response.json()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=18003)
    args = parser.parse_args()
    health = requests.get(f'http://127.0.0.1:{args.port}/health', timeout=3).json()
    assert health['profile'] == 'indoor' and health['policy_version'] == 2
    examples = []
    for name, expected in [('eval-room.jpg', {'行人', '桌子', '柜子'}),
                           ('eval-chairs.jpg', {'椅子'}),
                           ('bus-test.jpg', {'行人'}),
                           ('eval-sofa.jpg', set())]:
        data = (WORK / name).read_bytes()
        results = [post(args.port, '/analyze', data, name + str(i)) for i in range(3)]
        found = {d['label'] for d in results[-1]['body']['detections']}
        assert expected <= found, results[-1]
        examples.append({'image': name, 'required_labels': sorted(expected),
            'note': 'close-up/occluded sofa challenge; recorded without a positive assertion'
                    if name == 'eval-sofa.jpg' else 'public-image smoke test',
            'results': results})
    blank = BytesIO()
    Image.new('RGB', (512, 768), (160, 160, 160)).save(blank, 'JPEG')
    negative = post(args.port, '/analyze', blank.getvalue(), 'blank')
    assert negative['body']['detections'] == [], negative
    data = (WORK / 'eval-room.jpg').read_bytes()
    baseline = [post(8002, '/encode/navigation', data, str(i)) for i in range(3)]
    paired = []
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        for i in range(5):
            dino = pool.submit(post, 8002, '/encode/navigation', data, str(i))
            obstacle = pool.submit(post, args.port, '/analyze', data, str(i))
            paired.append({'dino': dino.result(), 'obstacle': obstacle.result()})
    reference = baseline[0]['body']
    key = next(k for k in ['embedding', 'features'] if k in reference)
    assert len(reference[key]) == 768
    for item in paired:
        assert len(item['dino']['body'][key]) == 768
        assert max(abs(a-b) for a, b in zip(reference[key], item['dino']['body'][key])) < 1e-5
        item['dino']['body'] = {'embedding_dimensions': 768}
    bad = requests.post(f'http://127.0.0.1:{args.port}/analyze',
        files={'file': ('bad.jpg', b'not an image', 'image/jpeg')}, data={'frame_id': 'bad'}, timeout=5)
    assert bad.status_code == 400
    report = {'health': health, 'examples': examples, 'blank': negative,
        'dino_only_ms': [item['roundtrip_ms'] for item in baseline],
        'concurrent': paired, 'embedding_preserved': True,
        'invalid_image_status': bad.status_code, 'physical_hallway_test': 'pending'}
    output = WORK / ('verification-indoor-' + str(args.port) + '.json')
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({'report': str(output), 'blank_detections': [],
        'dino_only_ms': report['dino_only_ms'],
        'concurrent_dino_ms': [item['dino']['roundtrip_ms'] for item in paired],
        'concurrent_obstacle_ms': [item['obstacle']['roundtrip_ms'] for item in paired],
        'embedding_preserved': True}, ensure_ascii=False))


if __name__ == '__main__':
    main()
