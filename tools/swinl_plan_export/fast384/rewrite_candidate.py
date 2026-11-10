"""Exact static graph rewrites for the separate 384x288 experiment."""
from pathlib import Path
import sys
from collections import Counter
import numpy as np
import onnx
from onnx import helper, numpy_helper

root = Path(sys.argv[1]) if len(sys.argv)>1 else Path('fast384/export3')
model = onnx.load(root / 'swinl-fp16.onnx')
constants = {v.name:numpy_helper.to_array(v) for v in model.graph.initializer}
shapes = {v.name:tuple(d.dim_value for d in v.type.tensor_type.shape.dim)
          for v in list(model.graph.input)+list(model.graph.value_info)+list(model.graph.output)}
shapes.update({name:value.shape for name,value in constants.items()})
new = []
def constant(name, value):
    model.graph.initializer.append(numpy_helper.from_array(np.asarray(value, np.int64), name=name))
    return name
for n in model.graph.node:
    if n.op_type == 'MatMul' and n.input[1] in constants and constants[n.input[1]].ndim == 2:
        a, b = n.input
        input_shape = shapes[a]
        weight_shape = constants[b].shape
        prefix = n.name + '/linear'
        rows = int(np.prod(input_shape[:-1]))
        assert input_shape[-1] == weight_shape[0]
        new.append(helper.make_node('Reshape', [a,constant(prefix+'in_shape',[rows,weight_shape[0]])], [prefix+'input'],name=prefix+'/input'))
        new.append(helper.make_node('Gemm',[prefix+'input',b],[prefix+'product'],name=prefix+'/gemm',transB=0))
        new.append(helper.make_node('Reshape',[prefix+'product',constant(prefix+'out_shape',list(input_shape[:-1])+[weight_shape[1]])],list(n.output),name=prefix+'/output'))
    elif n.op_type == 'Gather':
        indices = constants[n.input[1]]
        assert indices.ndim == 0, (n.name, indices.shape)
        shape = shapes[n.input[0]]
        axis = next((a.i for a in n.attribute if a.name == 'axis'), 0) % len(shape)
        index = int(indices) % shape[axis]
        prefix = n.name + '/slice'
        inputs = [n.input[0], constant(prefix+'start', [index]), constant(prefix+'end', [index+1]),
                  constant(prefix+'axis', [axis]), constant(prefix+'step', [1])]
        new.append(helper.make_node('Slice', inputs, [prefix+'output'], name=prefix))
        new.append(helper.make_node('Squeeze', [prefix+'output', inputs[3]], list(n.output), name=prefix+'/squeeze'))
    elif n.op_type == 'Einsum':
        equation = next(a.s.decode() for a in n.attribute if a.name == 'equation').replace(' ', '')
        assert equation == 'bqc,bchw->bqhw', equation
        a, b = n.input
        batch, channels, height, width = shapes[b]
        queries = shapes[a][1]
        prefix = n.name + '/bmm'
        new.append(helper.make_node('Reshape', [b, constant(prefix+'feature_shape', [batch, channels, height*width])],
                                    [prefix+'feature'], name=prefix+'/feature'))
        new.append(helper.make_node('MatMul', [a, prefix+'feature'], [prefix+'scores'], name=prefix+'/matmul'))
        new.append(helper.make_node('Reshape', [prefix+'scores', constant(prefix+'out_shape', [batch, queries, height, width])],
                                    list(n.output), name=prefix+'/output'))
    else:
        new.append(n)
model.graph.ClearField('node')
model.graph.node.extend(new)
model = onnx.shape_inference.infer_shapes(model, strict_mode=True, data_prop=True)
onnx.checker.check_model(model)
onnx.save(model, root/'swinl-fp16-rewritten.onnx')
print(Counter(n.op_type for n in model.graph.node))

# Reuse the existing exact rank-six spatial decomposition without changing it.
original = Path(__file__).with_name('rewrite_rank6_spatial.py').read_text()
start = original.index('SRC = ')
end = original.index('model = onnx.load', start)
source = original[:start] + 'SRC = Path('+repr(str(root/'swinl-fp16-rewritten.onnx'))+")\nDST = SRC.parent / 'swinl-fp16-lower.onnx'\n" + original[end:]
exec(compile(source, '<rank6-spatial-exact>', 'exec'))
