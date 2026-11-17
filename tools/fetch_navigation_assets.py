#!/usr/bin/env python3
"""Materialize only the five Git LFS assets needed for Swin-L fast builds."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import urllib.request
from concurrent.futures import ThreadPoolExecutor
import time

REQUIRED = [
    'librealsense/src/main/jniLibs/arm64-v8a/librealsense2.so',
    'navoutdoor/src/main/jniLibs/arm64-v8a/libelabrador_native.so',
    'navoutdoor/src/main/jniLibs/arm64-v8a/libvins_estimator.so',
    'navoutdoor/src/main/jniLibs/arm64-v8a/libvins_feature_tracker.so',
    'navoutdoor/src/swinlFast/assets/models/swinl-384.plan',
]


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def download_ranges(action, partial, size):
    """Keep verified-length ranges across interrupted large downloads."""
    chunks = partial.with_suffix('.chunks')
    chunks.mkdir(exist_ok=True)
    prefix = chunks / 'prefix'
    if partial.is_file() and not prefix.exists():
        if partial.stat().st_size < size:
            partial.replace(prefix)
        else:
            partial.unlink()
    start = prefix.stat().st_size if prefix.exists() else 0
    ranges = [(pos, min(pos + 8 * 1024 * 1024, size))
              for pos in range(start, size, 8 * 1024 * 1024)]

    def fetch(bounds):
        begin, end = bounds
        target = chunks / f'{begin}-{end}'
        if target.is_file() and target.stat().st_size == end - begin:
            return
        for attempt in range(4):
            try:
                request = urllib.request.Request(action['href'], headers={
                    **action.get('header', {}), 'Range': f'bytes={begin}-{end - 1}'})
                with urllib.request.urlopen(request, timeout=90) as response:
                    if response.status != 206 or not response.headers.get('Content-Range', '').startswith(f'bytes {begin}-'):
                        raise RuntimeError('LFS endpoint did not honor the requested byte range')
                    with target.open('wb') as output:
                        shutil.copyfileobj(response, output, length=1024 * 1024)
                if target.stat().st_size != end - begin:
                    raise RuntimeError('LFS byte range was truncated')
                return
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(1 + attempt)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(fetch, ranges))
    with partial.open('wb') as output:
        if prefix.exists():
            with prefix.open('rb') as stream:
                shutil.copyfileobj(stream, output)
        for begin, end in ranges:
            with (chunks / f'{begin}-{end}').open('rb') as stream:
                shutil.copyfileobj(stream, output)
    return chunks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, default=Path.home() / '.cache/roboguide-lfs')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    args.cache.mkdir(parents=True, exist_ok=True)
    missing = []
    for name in REQUIRED:
        target = root / name
        with target.open('rb') as stream:
            header = stream.read(1024)
        if not header.startswith(b'version https://git-lfs.github.com/spec/v1\n'):
            continue
        fields = dict(line.split(' ', 1) for line in header.decode().strip().splitlines())
        oid, size = fields['oid'].removeprefix('sha256:'), int(fields['size'])
        cached = args.cache / oid
        if cached.is_file() and cached.stat().st_size == size and digest(cached) == oid:
            shutil.copyfile(cached, target)
            print(f'Restored verified {target.relative_to(root)}', flush=True)
        else:
            missing.append((target, oid, size))
    if not missing:
        print('Navigation assets already materialized.')
        return
    payload = json.dumps({'operation': 'download', 'transfers': ['basic'], 'objects': [
        {'oid': oid, 'size': size} for _, oid, size in missing]}).encode()
    request = urllib.request.Request(
        'https://github.com/Farewell-CK/RoboGuide.git/info/lfs/objects/batch', data=payload,
        headers={'Content-Type': 'application/vnd.git-lfs+json', 'Accept': 'application/vnd.git-lfs+json'})
    with urllib.request.urlopen(request, timeout=60) as response:
        objects = {item['oid']: item for item in json.load(response)['objects']}
    for target, oid, size in missing:
        cached = args.cache / oid
        if not cached.is_file() or cached.stat().st_size != size or digest(cached) != oid:
            action = objects[oid].get('actions', {}).get('download')
            if not action:
                raise RuntimeError(f'LFS download unavailable for {target.relative_to(root)}')
            partial = cached.with_suffix('.part')
            print(f'Downloading {target.relative_to(root)} ({size} bytes)', flush=True)
            chunks = download_ranges(action, partial, size)
            if partial.stat().st_size != size or digest(partial) != oid:
                raise RuntimeError(f'LFS integrity check failed: {target.relative_to(root)}')
            partial.replace(cached)
            shutil.rmtree(chunks)
        shutil.copyfile(cached, target)
        print(f'Verified {target.relative_to(root)}', flush=True)


if __name__ == '__main__':
    main()
