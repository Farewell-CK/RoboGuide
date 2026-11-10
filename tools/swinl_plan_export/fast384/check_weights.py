"""Verify learned ONNX constants against the local HF checkpoint after FP16 conversion."""
import hashlib
import json
from pathlib import Path
import struct
import numpy as np
import onnx

root = Path(__file__).parent
source = Path(r'D:\_work\models\m2f_swinl_mapillary\model.safetensors')
with source.open('rb') as stream:
    length = struct.unpack('<Q', stream.read(8))[0]
    header = json.loads(stream.read(length))
    offset = 8 + length

    def load(name):
        info = header[name]
        begin, end = info['data_offsets']
        stream.seek(offset + begin)
        return np.frombuffer(stream.read(end - begin), {'F32': np.float32, 'F16': np.float16}[info['dtype']]).reshape(info['shape'])

    hashes = {}

    def register(name, array):
        array = np.asarray(array, np.float16)
        hashes[(array.shape, hashlib.sha256(array.tobytes()).digest())] = name
        if array.ndim == 2:
            hashes[(array.T.shape, hashlib.sha256(array.T.tobytes()).digest())] = name + '.T'

    for name, info in header.items():
        if name == '__metadata__' or info['dtype'] not in ['F32', 'F16']:
            continue
        register(name, load(name))
    for name in header:
        if name.endswith(('.in_proj_weight', '.in_proj_bias')):
            for index, part in enumerate(np.split(load(name), 3, axis=0)):
                register(name + '.split' + str(index), part)
        for q, k, v in [('query', 'key', 'value'), ('q_proj', 'k_proj', 'v_proj')]:
            for suffix in ['weight', 'bias']:
                ending = '.' + q + '.' + suffix
                if not name.endswith(ending):
                    continue
                stem = name[:-len(ending)]
                keys = [stem + '.' + part + '.' + suffix for part in [q, k, v]]
                if all(key in header for key in keys):
                    register(stem + '.combined_qkv.' + suffix, np.concatenate([load(key) for key in keys], axis=0))

model = onnx.load(r'D:\codex\swinl-npu-20261023\full-fp16\swinl-fp16-qkv-rank4-spatial-rank4.onnx')
parameters = {name for node in model.graph.node if node.op_type in ['Gemm', 'Conv', 'LayerNormalization', 'InstanceNormalization'] for name in node.input[1:]}
matched, unmatched = [], []
for value in model.graph.initializer:
    if value.name not in parameters:
        continue
    array = onnx.numpy_helper.to_array(value).astype(np.float16)
    key = (array.shape, hashlib.sha256(array.tobytes()).digest())
    item = dict(name=value.name, shape=array.shape, bytes=array.nbytes)
    if key in hashes:
        item['checkpoint_name'] = hashes[key]
        matched.append(item)
    else:
        unmatched.append(item)
report = dict(matched_bytes=sum(v['bytes'] for v in matched), unmatched_bytes=sum(v['bytes'] for v in unmatched),
              matched=matched, unmatched=unmatched)
(root / 'weight-audit.json').write_text(json.dumps(report, indent=2))
print({k: v for k, v in report.items() if not isinstance(v, list)})
print('unmatched', [(v['name'], v['shape']) for v in unmatched[:15]])
