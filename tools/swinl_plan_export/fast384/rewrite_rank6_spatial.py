"""Fuse Swin-L rank-6 spatial Transpose+Reshape pairs into rank<=4 ops."""
from pathlib import Path
import numpy as np
import onnx
from onnx import helper, numpy_helper

SRC = Path(r"D:\codex\swinl-npu-20261023\full-fp16\swinl-fp16-qkv-rank4.onnx")
DST = SRC.parent / "swinl-fp16-qkv-rank4-spatial-rank4.onnx"
model = onnx.load(SRC, load_external_data=False)
init = {x.name: x for x in model.graph.initializer}
shape_info = {v.name: v for v in list(model.graph.input) + list(model.graph.value_info) + list(model.graph.output)}

def shape(name):
    v = shape_info.get(name)
    return None if v is None else [d.dim_value if d.HasField("dim_value") else None for d in v.type.tensor_type.shape.dim]

def set_shape(name, dims):
    v = shape_info.get(name)
    if v is None:
        v = helper.make_tensor_value_info(name, 10, dims)
        model.graph.value_info.append(v)
        shape_info[name] = v
    del v.type.tensor_type.shape.dim[:]
    for d in dims:
        v.type.tensor_type.shape.dim.add().dim_value = int(d)

def set_init(name, values):
    init[name].CopyFrom(numpy_helper.from_array(np.asarray(values, dtype=np.int64), name=name))

nodes = list(model.graph.node)
users = {}
producers = {}
for n in nodes:
    for o in n.output: producers[o] = n
    for i in n.input: users.setdefault(i, []).append(n)
replacements = {}
skip = set()
rewrites = []

for t in nodes:
    if t.op_type != "Transpose":
        continue
    perm = next((list(a.ints) for a in t.attribute if a.name == "perm"), [])
    ishape = shape(t.input[0])
    if perm != [0, 1, 3, 2, 4, 5] or ishape is None or len(ishape) != 6:
        continue
    b, h1, h2, w1, w2, c = ishape
    if not all(isinstance(x, int) and x > 0 for x in ishape):
        raise ValueError(f"dynamic rank6 shape: {t.name} {ishape}")
    producer = producers.get(t.input[0])
    if producer is None or producer.op_type != "Reshape" or producer.input[1] not in init:
        raise ValueError(f"rank6 producer is not static Reshape: {t.name}")
    us = users.get(t.output[0], [])
    if len(us) != 1 or us[0].op_type != "Reshape" or us[0].input[1] not in init:
        raise ValueError(f"rank6 transpose does not have one static Reshape user: {t.name}")
    consumer = us[0]
    out_shape = shape(consumer.output[0])
    if out_shape is None or any(not isinstance(x, int) or x <= 0 for x in out_shape):
        raise ValueError(f"unexpected rank6 consumer output: {consumer.name} {out_shape}")
    if int(np.prod(out_shape)) != b * h1 * h2 * w1 * w2 * c:
        raise ValueError(f"rank6 consumer element count mismatch: {consumer.name} {out_shape}")
    # Keep the same contiguous values, but represent the producer as rank 4.
    producer_shape_name = producer.input[1]
    set_init(producer_shape_name, [b * h1, h2, w1, w2 * c])
    set_shape(t.input[0], [b * h1, h2, w1, w2 * c])
    prefix = t.name + "/Rank4"
    s1 = prefix + "_shape1"
    s2 = prefix + "_shape2"
    s3 = prefix + "_shape3"
    mid1 = t.output[0] + "_rank4_transposed"
    mid2 = t.output[0] + "_rank4_merged"
    for n, vals in ((s1, [b * h1, h2, w1, w2 * c]),
                    (s2, [b * h1, w1, h2 * w2, c]),
                    (s3, list(out_shape))):
        model.graph.initializer.append(numpy_helper.from_array(np.asarray(vals, dtype=np.int64), name=n))
    r1 = helper.make_node("Reshape", [t.input[0], s1], [mid1], name=prefix + "/InputLayout")
    tr = helper.make_node("Transpose", [mid1], [mid2], name=prefix + "/Transpose4", perm=[0, 2, 1, 3])
    r2 = helper.make_node("Reshape", [mid2, s2], [t.output[0] + "_rank4_merged_out"], name=prefix + "/Merge")
    r3 = helper.make_node("Reshape", [r2.output[0], s3], [consumer.output[0]], name=prefix + "/RestoreConsumer")
    replacements[t.name] = [r1, tr, r2, r3]
    skip.add(consumer.name)
    rewrites.append({"transpose": t.name, "input_shape": ishape, "output_shape": out_shape})

new_nodes = []
for n in nodes:
    if n.name in skip:
        continue
    if n.name in replacements:
        new_nodes.extend(replacements[n.name])
    else:
        new_nodes.append(n)
model.graph.ClearField("node")
model.graph.node.extend(new_nodes)
onnx.checker.check_model(model)
onnx.save(model, DST)
print("rewritten rank6 paths:", len(rewrites))
print("output:", DST, "bytes:", DST.stat().st_size)
