"""Verify pinned files before loading model weights."""
import hashlib
import json
from pathlib import Path


def verify(directory):
    root = Path(directory)
    manifest = json.loads((root / 'model-indoor-manifest.json').read_text())
    for name, expected in manifest['files'].items():
        path = root / 'model-indoor' / name
        if path.stat().st_size != expected['size']:
            raise RuntimeError('Model size mismatch: ' + name)
        digest = hashlib.sha256()
        with path.open('rb') as handle:
            for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b''):
                digest.update(chunk)
        if digest.hexdigest() != expected['sha256']:
            raise RuntimeError('Model checksum mismatch: ' + name)


if __name__ == '__main__':
    verify(Path(__file__).resolve().parent)
    print('INDOOR_MODEL_VERIFIED')
