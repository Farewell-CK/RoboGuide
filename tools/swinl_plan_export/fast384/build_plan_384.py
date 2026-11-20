"""Reproducible full-model lowering, strict static validation and hash manifest."""
import argparse
import contextlib
import hashlib
import json
import pathlib
import time
import os

import onnx
import lower
from fast384.lower_candidate import install
install()
from check_ops2 import check_plan

parser = argparse.ArgumentParser()
parser.add_argument('output')
parser.add_argument('--model',default=r'D:\npudaomang\swin\fast384\export3\swinl-fp16-lower.onnx')
parser.add_argument('--fast', action='store_true', help='Use the validated shared-weight and fused GridSample configuration')
parser.add_argument('--fast-v2', action='store_true', help='Use shared affine parameters, merged models, and compact uniform constants')
parser.add_argument('--fast-v3', action='store_true', help='Also compress repeated broadcast constants and export terminal values directly')
parser.add_argument('--fast-v4', action='store_true', help='Also remove shape-only models using shared buffer aliases')
parser.add_argument('--budget', type=int, default=8)
parser.add_argument('--points', type=int, default=4096)
parser.add_argument('--rows-per-sub', type=int, default=1)
parser.add_argument('--dynamic-fc', action='store_true')
parser.add_argument('--dynamic-affine', action='store_true')
parser.add_argument('--fuse-grid', action='store_true')
parser.add_argument('--gemm-elements', type=int, default=2000000)
parser.add_argument('--chunk-rows', type=int, default=2048)
parser.add_argument('--merge', type=int, default=1)
parser.add_argument('--merge-ops', type=int, default=160)
parser.add_argument('--gather-grid', action='store_true', help='Sparse sampling; requires Neuron accelerator')
parser.add_argument('--hoist-window-linear', action='store_true', help='Compute padded-window projections only for valid tokens')
parser.add_argument('--chunk-attention', action='store_true', help='Avoid global buffers for large Swin attention scores')
args = parser.parse_args()
if args.fast_v4:args.fast_v3=True
if args.fast_v3:args.fast_v2=True
if args.fast_v2:
    if args.gather_grid:parser.error('--fast-v2 uses the validated dense GridSample path')
    args.fast=True;args.dynamic_affine=True;args.merge=4;args.merge_ops=160
if args.fast:
    args.budget = 40
    args.points = 6144
    args.rows_per_sub = 0
    args.dynamic_fc = True
    args.fuse_grid = True
    args.gemm_elements = 12000000
    args.chunk_rows = 4096
lower.GS_POINT_BLOCK = args.points
lower.GS_ROWS_PER_SUB = args.rows_per_sub
lower.GS_FUSE_ALL_POINTS = args.fuse_grid
lower.GEMM_ELEMENT_LIMIT = args.gemm_elements
if args.gather_grid:
    import lower_gather
    lower_gather.install()
os.environ['SWIN_CHUNK_ROWS'] = str(args.chunk_rows)
model_path = args.model
started = time.perf_counter()
model = onnx.load(model_path, load_external_data=False)
hoist={}
if args.hoist_window_linear:
    from hoist_window_linear import transform
    hoist=transform(model.graph)
attention=[]
if args.chunk_attention:
    import chunk_attention
    attention=chunk_attention.transform(model.graph)
    chunk_attention.install()
converter = lower.Lowerer(model.graph, keep=[], budget_ops=args.budget)
output = pathlib.Path(args.output)
with output.with_suffix('.lower.log').open('w') as log, contextlib.redirect_stdout(log):
    plan = converter.run()
dynamic = {}
if args.dynamic_fc:
    from dynamic_fc import convert
    dynamic = convert(plan,dynamic_affine=args.dynamic_affine)
fusion={}
if args.merge>1:
    from fuse_plan import merge
    fusion=merge(plan,args.merge,args.merge_ops)
postpass={}
if args.fast_v2:
    from optimize_plan import simplify
    from dynamic_fc import convert
    from fuse_plan import merge
    postpass['initial_simplify']=simplify(plan,uniform_constants=False)
    postpass['shared_matmul']=convert(plan,dynamic_affine=True,dynamic_matmul=True)
    postpass['second_fusion']=merge(plan,8,400)
    postpass['fused_simplify']=simplify(plan,uniform_constants=False)
    postpass['uniform_constants']=simplify(plan)
    postpass['final_shared']=convert(plan,dynamic_affine=True,dynamic_matmul=True)
    postpass['final_simplify']=simplify(plan)
if args.fast_v3:
    from compress_broadcast import compress
    from terminal_outputs import remove
    postpass['broadcast_compression']=compress(plan)
    postpass['direct_outputs']=remove(plan)
if args.fast_v4:
    from alias_views import alias
    postpass['buffer_aliases']=alias(plan)
problems = check_plan(plan)
if problems:
    raise RuntimeError(f'{len(problems)} static errors: {problems[:12]}')
size = plan.write(str(output))
with output.open('rb') as stream:
    md5 = hashlib.file_digest(stream, 'md5').hexdigest()
manifest = dict(model=model_path, plan=str(output), md5=md5, bytes=size,
                dynamic_fc=dynamic,
                fusion=fusion,
                postpass=postpass,
                window_linear_hoist=hoist,
                chunked_attention=attention,
                gather_grid=args.gather_grid,
                fuse_grid=args.fuse_grid,
                budget=args.budget, point_block=args.points, rows_per_sub=args.rows_per_sub,
                gemm_elements=args.gemm_elements, chunk_rows=args.chunk_rows, static_problems=len(problems),
                subgraphs=len(plan.subs), operations=sum(len(s.ops) for s in plan.subs),
                build_seconds=round(time.perf_counter()-started, 3),
                outputs=[dict(name=value.name, buffer=converter.tensors[value.name].buf,
                              shape=converter.tensors[value.name].shape,
                              dtype=converter.tensors[value.name].dtype)
                         for value in model.graph.output])
output.with_suffix('.manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
print(json.dumps(manifest, indent=2), flush=True)
