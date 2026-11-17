"""Compare actual HTTP services with the same images, precision and encoder."""
from pathlib import Path
import hashlib
import json
import math
import statistics
import time

import numpy as np
import requests

WORK = Path(__file__).resolve().parent
session = requests.Session()
session.trust_env = False
BASES = {'cpu': 'http://127.0.0.1:8000', 'gpu': 'http://127.0.0.1:8002'}


def encode(base, image, endpoint='/encode/navigation'):
    started = time.perf_counter()
    response = session.post(base + endpoint, files={'image': (image.name, image.read_bytes(), 'image/png')}, timeout=60)
    elapsed = (time.perf_counter() - started) * 1000
    response.raise_for_status()
    data = response.json()
    vector = np.array(data['embedding'], dtype=np.float64)
    assert data['dimensions'] == 768 and vector.shape == (768,)
    assert np.isfinite(vector).all() and abs(np.linalg.norm(vector) - 1) < 1e-5
    return vector, elapsed


def cosine(a, b):
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def main():
    health = {k: session.get(base + '/health', timeout=10).json() for k, base in BASES.items()}
    assert health['cpu']['status'] == health['gpu']['status'] == 'healthy'
    assert health['cpu']['device'] == 'cpu' and health['gpu']['device'].startswith('cuda')
    images = sorted((WORK / 'samples').glob('*.png'))
    assert len(images) >= 3, 'At least three input images required'
    results = []
    references = {'cpu': [], 'gpu': []}
    all_times = {'cpu': [], 'gpu': []}
    for image in images:
        row = {'image': image.name, 'sha256': hashlib.sha256(image.read_bytes()).hexdigest()}
        # Warm both actual services; exclude these requests from timings.
        for backend, base in BASES.items():
            for _ in range(3):
                vector, _ = encode(base, image)
            references[backend].append(vector)
        a, b = references['cpu'][-1], references['gpu'][-1]
        row['cpu_gpu_cosine'] = cosine(a, b)
        row['max_absolute_error'] = float(np.max(np.abs(a - b)))
        assert row['cpu_gpu_cosine'] >= .99999 and row['max_absolute_error'] < 1e-4, row
        times = {'cpu': [], 'gpu': []}
        for repeat in range(12):
            # Alternate order to reduce order/thermal effects. No concurrent CPU
            # load or asynchronous CUDA timing: the HTTP response contains CPU vectors.
            order = ['cpu', 'gpu'] if repeat % 2 == 0 else ['gpu', 'cpu']
            for backend in order:
                vector, elapsed = encode(BASES[backend], image)
                assert cosine(vector, references[backend][-1]) >= .99999
                times[backend].append(elapsed)
                all_times[backend].append(elapsed)
        for backend in BASES:
            row[backend + '_median_ms'] = statistics.median(times[backend])
            row[backend + '_p95_ms'] = float(np.percentile(times[backend], 95))
        row['speedup'] = row['cpu_median_ms'] / row['gpu_median_ms']
        results.append(row)
        print(json.dumps(row), flush=True)

    endpoint_checks = []
    for endpoint in ['/encode', '/encode/preprocessed', '/encode/inpainted?disable_yolo=true']:
        for backend, base in BASES.items():
            vector, _ = encode(base, images[0], endpoint)
            assert cosine(vector, references[backend][0]) >= .99999
            endpoint_checks.append({'backend': backend, 'endpoint': endpoint, 'passed': True})
    # Existing route vectors and thresholds must not change meaning on migration.
    threshold_decision_changes = 0
    max_pairwise_error = 0.0
    for i in range(len(images)):
        for j in range(len(images)):
            cpu_pair = cosine(references['cpu'][i], references['cpu'][j])
            # Retain old CPU reference, as existing recorded routes do.
            mixed_pair = cosine(references['gpu'][i], references['cpu'][j])
            max_pairwise_error = max(max_pairwise_error, abs(cpu_pair - mixed_pair))
            for threshold in [.5, .7, .8]:
                threshold_decision_changes += int((cpu_pair >= threshold) != (mixed_pair >= threshold))
    assert threshold_decision_changes == 0
    health_after = {k: session.get(base + '/health', timeout=10).json() for k, base in BASES.items()}
    assert health_after['cpu']['device'] == 'cpu' and health_after['gpu']['device'].startswith('cuda')
    summary = {backend: {'requests': len(times), 'median_ms': statistics.median(times),
               'p95_ms': float(np.percentile(times, 95))} for backend, times in all_times.items()}
    summary['speedup'] = summary['cpu']['median_ms'] / summary['gpu']['median_ms']
    report = dict(timestamp=time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                  scope='Server localhost HTTP, warmed sequential requests; excludes phone/LAN latency',
                  precision='FP32, TF32 disabled, original CPU gateway snapshot and shared cached model',
                  health_before=health, health_after=health_after,
                  images=results, summary=summary, endpoint_checks=endpoint_checks,
                  threshold_decision_changes=threshold_decision_changes,
                  max_pairwise_similarity_error=max_pairwise_error,
                  cpu_environment=json.loads((WORK/'cpu-environment.json').read_text()),
                  gpu_environment=json.loads((WORK/'gpu-environment.json').read_text()),
                  source_manifest=json.loads((WORK/'source-manifest.json').read_text()))
    (WORK / 'benchmark.json').write_text(json.dumps(report, indent=2))
    md = f'''# NAVI DINOv2 CPU / GPU comparison

Time: {report['timestamp']}

Both services use DINOv2-base, the same cached weights and preprocessing, normalized 768-dimensional CLS embeddings and FP32 arithmetic. The GPU uses one RTX 3090. CPU remains running on port 8000; GPU runs on loopback port 8002.

| Backend | Requests | Median ms | P95 ms |
|---|---:|---:|---:|
| CPU | {summary['cpu']['requests']} | {summary['cpu']['median_ms']:.2f} | {summary['cpu']['p95_ms']:.2f} |
| GPU | {summary['gpu']['requests']} | {summary['gpu']['median_ms']:.2f} | {summary['gpu']['p95_ms']:.2f} |

Median speedup: {summary['speedup']:.2f}x.

Minimum CPU/GPU same-image cosine: {min(r['cpu_gpu_cosine'] for r in results):.10f}. Maximum component error: {max(r['max_absolute_error'] for r in results):.8g}.

Threshold decision changes in sampled comparisons (0.5, 0.7, 0.8): {threshold_decision_changes}. Existing CPU reference vectors were retained for these comparisons.

These are localhost HTTP measurements with warmed services, not phone end-to-end latency or route navigation accuracy. Samples are diagnostic image crops and a synthetic image; a full walk remains necessary. No FP16, YOLO or inpainting is enabled. Both services remain healthy after testing.
'''
    (WORK / 'benchmark.md').write_text(md)
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == '__main__':
    main()
