"""Run a snapshot of the CPU gateway on a single GPU, retaining its encoder/API.

The original CPU source, Python environment and process are never changed.
Only DINOv2 is initialized. No silent CPU or different-model fallback is allowed.
"""
from contextlib import asynccontextmanager
from pathlib import Path
import hashlib
import importlib.metadata as metadata
import json
import os
import platform
import sys

import torch
import uvicorn

WORK = Path(__file__).resolve().parent
MANIFEST = json.loads((WORK / 'source-manifest.json').read_text())
SNAPSHOT = WORK / 'cpu_gateway_snapshot.py'
SOURCE_SHA = hashlib.sha256(SNAPSHOT.read_bytes()).hexdigest()
if SOURCE_SHA != MANIFEST['files']['dinov2_http_gateway.py']:
    raise RuntimeError('Gateway snapshot does not match the recorded CPU source')
if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
    raise RuntimeError('Exactly one visible CUDA GPU is required; CPU fallback disabled')
if not Path(torch.__file__).resolve().is_relative_to(WORK.parent / 'navi-gpu-env'):
    raise RuntimeError('PyTorch must come from the independent GPU environment')

# Preserve FP32 arithmetic and the original preprocessing / normalized CLS token.
torch.set_num_threads(4)
torch.backends.cuda.matmul.allow_tf32 = False
torch.backends.cudnn.allow_tf32 = False

import cpu_gateway_snapshot as gateway  # noqa: E402


@asynccontextmanager
async def gpu_lifespan(app):
    if gateway._device_str != 'cuda' or not gateway.initialize_dinov2_model():
        raise RuntimeError('DINOv2 failed to load on the GPU')
    parameter = next(gateway.dinov2_model.parameters())
    if parameter.device.type != 'cuda' or parameter.dtype != torch.float32:
        raise RuntimeError('GPU model must use CUDA with FP32 parameters')
    packages = {}
    for name in ['torch', 'torchvision', 'transformers', 'numpy', 'pillow',
                 'safetensors', 'tokenizers', 'huggingface-hub', 'fastapi',
                 'uvicorn', 'python-multipart', 'clip-client', 'requests']:
        packages[name] = metadata.version(name)
    environment = dict(python=platform.python_version(), packages=packages,
                       cuda_available=True, cuda_build=torch.version.cuda,
                       gpu=torch.cuda.get_device_name(0),
                       visible_gpu=os.environ['CUDA_VISIBLE_DEVICES'],
                       dtype=str(parameter.dtype), source_sha256=SOURCE_SHA,
                       environment_prefix=sys.prefix, torch_file=torch.__file__,
                       model_revision=MANIFEST['model_revision'])
    (WORK / 'gpu-environment.json').write_text(json.dumps(environment, indent=2))
    print(json.dumps(environment), flush=True)
    yield


app = gateway.app
from obstacle_bridge import router as obstacle_router
app.include_router(obstacle_router)
app.router.lifespan_context = gpu_lifespan
# The upstream health response lists optional models even when absent. Report
# actual capabilities for this deliberately DINOv2-only comparison instance.
app.router.routes[:] = [r for r in app.router.routes if getattr(r, 'path', None) not in {'/health', '/'}]


@app.get('/health')
async def health():
    parameter = next(gateway.dinov2_model.parameters())
    return dict(status='healthy', model='DINOv2-base', dimensions=768,
                device=str(parameter.device), dtype=str(parameter.dtype),
                gpu_name=torch.cuda.get_device_name(0),
                gpu_uuid=os.environ['CUDA_VISIBLE_DEVICES'],
                gpu_allocated_mib=round(torch.cuda.memory_allocated(0) / 1024**2, 1),
                pid=os.getpid(), features=['DINOv2'],
                yolo_enabled=False, inpainting_enabled=False,
                model_revision=MANIFEST['model_revision'], source_sha256=SOURCE_SHA)


@app.get('/')
async def root():
    return dict(message='NAVI DINOv2 GPU comparison service',
                cpu_comparison='http://127.0.0.1:8000',
                health='/health', image_endpoints=['/encode', '/encode/navigation',
                '/encode/preprocessed', '/encode/inpainted?disable_yolo=true'],
                optional_models_enabled=False)


if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=8002, workers=1, log_level='info')
