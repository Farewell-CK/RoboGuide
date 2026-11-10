"""Check per-op operand constraints of a plan (what the driver silently rejects)."""
import sys

import onnx
import numpy as np

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lower
import nnapi

MODEL = r'D:\codex\swinl-npu-20261023\full-fp16\swinl-fp16-qkv-rank4-spatial-rank4.onnx'


def prod(dims):
    value = 1
    for dim in dims:
        value *= dim
    return value


def check(path_model, budget=8):
    model = onnx.load(path_model, load_external_data=False)
    lowerer = lower.Lowerer(model.graph, keep=[], budget_ops=budget)
    plan = lowerer.run()
    return plan, check_plan(plan)


def check_plan(plan):
    problems = []
    for index, sub in enumerate(plan.subs):
        values = sub.values
        for op_index, (code, ins, outs) in enumerate(sub.ops):
            shapes = [list(values[i][1]) for i in ins]
            out_shapes = [list(values[o][1]) for o in outs]
            if code == nnapi.RESHAPE:
                if len(ins) >= 2 and values[ins[1]][2]:
                    if prod(shapes[0]) != prod(out_shapes[0]):
                        problems.append((index, op_index, 'reshape count', shapes[0], out_shapes[0]))
                    target = np.frombuffer(values[ins[1]][2], dtype=np.int32).tolist()
                    if target != out_shapes[0] and -1 not in target and 0 not in target:
                        problems.append((index, op_index, 'reshape target', target, out_shapes[0]))
            elif code == nnapi.SLICE:
                if len(shapes[0]) != len(out_shapes[0]):
                    problems.append((index, op_index, 'slice rank', shapes[0], out_shapes[0]))
                if len(shapes) > 2 and len(shapes[2]) and shapes[2][0] != len(shapes[0]):
                    problems.append((index, op_index, 'slice size len', shapes[0], shapes[2]))
            elif code == nnapi.TRANSPOSE:
                if len(shapes) > 1 and len(shapes[1]) == 1 and shapes[1][0] != len(shapes[0]):
                    problems.append((index, op_index, 'perm len', shapes[0], shapes[1]))
                if len(shapes[0]) != len(out_shapes[0]):
                    problems.append((index, op_index, 'transpose rank', shapes[0], out_shapes[0]))
                if values[ins[1]][2]:
                    perm = np.frombuffer(values[ins[1]][2], dtype=np.int32).tolist()
                    if sorted(perm) != list(range(len(shapes[0]))):
                        problems.append((index, op_index, 'transpose perm', shapes[0], perm))
                    elif [shapes[0][p] for p in perm] != out_shapes[0]:
                        problems.append((index, op_index, 'transpose output', [shapes[0][p] for p in perm], out_shapes[0]))
            elif code == nnapi.CONCATENATION:
                ranks = {len(shape) for shape in shapes[:-1]}
                if len(ranks) != 1:
                    problems.append((index, op_index, 'concat ranks', sorted(ranks), len(shapes)))
                elif len(out_shapes[0]) != len(shapes[0]):
                    problems.append((index, op_index, 'concat out rank', shapes[0], out_shapes[0]))
            elif code in (nnapi.ADD, nnapi.SUB, nnapi.MUL, nnapi.DIV, nnapi.MAXIMUM, nnapi.MINIMUM):
                if len(shapes) >= 2:
                    rank = max(len(shapes[0]), len(shapes[1]))
                    a = [1] * (rank - len(shapes[0])) + shapes[0]
                    b = [1] * (rank - len(shapes[1])) + shapes[1]
                    for left, right in zip(a, b):
                        if left != right and left != 1 and right != 1:
                            problems.append((index, op_index, 'broadcast', shapes[0], shapes[1]))
                            break
                    else:
                        expected = [max(x, y) for x, y in zip(a, b)]
                        if expected != out_shapes[0]:
                            problems.append((index, op_index, 'elementwise output', expected, out_shapes[0]))
                    if values[ins[0]][0] != values[ins[1]][0] or values[ins[0]][0] != values[outs[0]][0]:
                        problems.append((index, op_index, 'elementwise dtype', [values[i][0] for i in ins[:2]], [values[outs[0]][0]]))
            elif code == nnapi.FULLY_CONNECTED:
                if len(shapes) >= 3:
                    weight = shapes[1]
                    bias = shapes[2]
                    out = out_shapes[0]
                    if len(weight) == 2 and len(bias) == 1:
                        if bias[0] != weight[0]:
                            problems.append((index, op_index, 'fc bias', weight, bias))
                        if out[-1] != weight[0]:
                            problems.append((index, op_index, 'fc out', weight, out))
            elif code == nnapi.BATCH_MATMUL:
                if len(shapes) >= 2 and len(shapes[0]) != len(shapes[1]):
                    problems.append((index, op_index, 'bmm rank', shapes[0], shapes[1]))
    return problems


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else MODEL
    plan, problems = check(path)
    print('subgraphs', len(plan.subs), 'problems', len(problems), flush=True)
    shown = set()
    for item in problems:
        key = (item[0], item[2])
        if key in shown:
            continue
        shown.add(key)
        print('  sub', item[0], 'op', item[1], item[2], item[3], '->', item[4], flush=True)


if __name__ == '__main__':
    main()
