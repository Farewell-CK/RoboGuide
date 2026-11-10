"""Fold constants without evaluating or freezing any image-dependent values."""
import gc
import sys
import json
from pathlib import Path
from collections import Counter
import onnx
import onnxruntime as ort
from onnxconverter_common import float16

root = Path(sys.argv[1]) if len(sys.argv)>1 else Path('/mnt/d/npudaomang/swin/fast384/export3')
basic = root / 'swinl-fp32-basic.onnx'
options = ort.SessionOptions()
options.intra_op_num_threads = 4
options.enable_cpu_mem_arena = False
options.enable_mem_pattern = False
options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
options.optimized_model_filepath = str(basic)
session = ort.InferenceSession(str(root / 'swinl-fp32.onnx'), options, providers=['CPUExecutionProvider'])
del session, options
gc.collect()
model = onnx.load(basic)
print('FP32_BASIC', len(model.graph.node), Counter(n.op_type for n in model.graph.node), flush=True)
model = float16.convert_float_to_float16(model, keep_io_types=False, disable_shape_infer=True,
    op_block_list=[], min_positive_val=0, max_finite_val=float('inf'), check_fp16_ready=False)
declared = {v.name:v.type.tensor_type.elem_type for v in list(model.graph.input)+list(model.graph.value_info)+list(model.graph.output)}
for n in model.graph.node:
    if n.op_type == 'Cast' and declared.get(n.output[0]) == onnx.TensorProto.FLOAT16:
        for a in n.attribute:
            if a.name == 'to' and a.i == onnx.TensorProto.FLOAT:
                a.i = onnx.TensorProto.FLOAT16
model = onnx.shape_inference.infer_shapes(model, strict_mode=True, data_prop=True)
onnx.checker.check_model(model)
onnx.save(model, root / 'swinl-fp16.onnx')
print('FP16_READY', flush=True)
