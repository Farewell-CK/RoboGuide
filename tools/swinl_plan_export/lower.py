"""ONNX -> NNAPI plan lowering for Swin-L, targeting mtk-mdla_shim.

The converter keeps ONNX semantics (NCHW where ONNX says NCHW) and inserts the
layout transposes that NNAPI Conv/Resize need.  Everything is FP16.
"""
import sys
import numpy as np
import onnx
from onnx import numpy_helper

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import nnapi
from nnapi import Plan, Sub

F16 = nnapi.TENSOR_FLOAT16
I32 = nnapi.TENSOR_INT32


class Tensor:
    __slots__ = ('name', 'dtype', 'shape', 'const', 'buf', 'local', 'local_sub', 'is_input')

    def __init__(self, name, dtype, shape, const=None):
        self.name = name
        self.dtype = dtype
        self.shape = tuple(shape)
        self.const = const          # numpy array for constants
        self.buf = None             # global buffer id once materialised
        self.local = None           # local value index in the subgraph `local_sub`
        self.local_sub = -1
        self.is_input = False

    def __repr__(self):
        return f'<{self.name} {self.shape} const={self.const is not None} buf={self.buf}>'


def prod(shape):
    n = 1
    for d in shape:
        n *= d
    return int(n)


def sanitize(name):
    out = []
    for ch in name:
        out.append(ch if (ch.isalnum() or ch in '._-') else '_')
    text = ''.join(out)[-60:]
    return text or 't'


class Lowerer:
    def __init__(self, graph, budget_ops=40, budget_bytes=192 * 1024 * 1024, verbose=False,
                 limit=None, keep=()):
        self.graph = graph
        self.nodes = list(graph.node)[:limit] if limit else list(graph.node)
        self.keep = list(keep)
        self.limit = limit
        self.plan = Plan()
        self.tensors = {}
        self.order = [n.name for n in self.nodes]
        self.node_index = {n.name: i for i, n in enumerate(self.nodes)}
        self.last_use = {}
        self.cur = None
        self.cur_index = -1
        self.budget_ops = budget_ops
        self.budget_bytes = budget_bytes
        self.verbose = verbose
        self.unique = 0

        self.shapes = {}
        self.dtypes = {}
        for v in list(graph.value_info) + list(graph.input) + list(graph.output):
            dims = [d.dim_value if d.HasField('dim_value') else 0
                    for d in v.type.tensor_type.shape.dim]
            self.shapes[v.name] = tuple(dims)
            self.dtypes[v.name] = v.type.tensor_type.elem_type

        self.initializers = {}
        for init in graph.initializer:
            self.initializers[init.name] = numpy_helper.to_array(init)

        self.use_count = {}
        for n in self.nodes:
            for i in n.input:
                if i:
                    self.use_count[i] = self.use_count.get(i, 0) + 1

        for n in self.nodes:
            for i in n.input:
                if i:
                    self.last_use[i] = max(self.last_use.get(i, -1), self.node_index[n.name])

    # ------------------------------------------------------------------ utils
    def shape_of(self, name):
        if name not in self.shapes:
            raise KeyError(f'no shape for {name}')
        return self.shapes[name]

    def tensor(self, name):
        if name in self.tensors:
            return self.tensors[name]
        if name in self.initializers:
            array = self.initializers[name]
            shape = self.shape_of(name) if name in self.shapes else array.shape
            array = array.reshape(shape)
            tensor = Tensor(name, self.dtypes.get(name, 10), tuple(shape), const=array)
            self.tensors[name] = tensor
            return tensor
        raise KeyError(f'unknown tensor {name}')

    def define(self, name, shape, dtype=F16, const=None):
        tensor = Tensor(name, dtype, shape, const=const)
        self.tensors[name] = tensor
        return tensor

    def operand(self, tensor):
        """Local operand index for a tensor, materialising cross-subgraph reads."""
        if tensor.const is not None:
            array = tensor.const
            if tensor.local is not None and tensor.local_sub == self.cur_index:
                return tensor.local
            data = np.asarray(array, dtype=np.float16).tobytes() if tensor.dtype != 4 else \
                np.asarray(array, dtype=np.int32).tobytes()
            return self.cur.val(F16 if tensor.dtype != 4 else I32, tensor.shape, const=data)
        if tensor.local is not None and tensor.local_sub == self.cur_index:
            return tensor.local
        if tensor.buf is None:
            raise RuntimeError(f'tensor used before materialisation: {tensor.name}')
        index = self.cur.val(tensor.dtype, tensor.shape, binding=tensor.buf, as_input=True)
        tensor.local = index
        tensor.local_sub = self.cur_index
        return index

    def const_f16(self, array, shape=None):
        arr = np.asarray(array, dtype=np.float16)
        if shape is not None:
            arr = arr.reshape(shape)
        return self.cur.val(F16, arr.shape, const=arr.tobytes())

    def scalar_i32(self, value):
        return self.cur.scalar_i32(int(value))

    def emit(self, code, ins, out_tensor):
        if code == nnapi.RESHAPE:
            source_dims = self.cur.values[ins[0]][1]
            if prod(source_dims) != prod(out_tensor.shape):
                raise ValueError(f'reshape element count: {source_dims} -> {out_tensor.shape}')
            ins = [ins[0], self.cur.i32(list(out_tensor.shape))]
        if code == nnapi.RESHAPE and len(ins) >= 1 and isinstance(ins[0], int):
            source_dims = self.cur.values[ins[0]][1]
            if list(source_dims) == list(out_tensor.shape):
                out_tensor.local = ins[0]
                out_tensor.local_sub = self.cur_index
                return out_tensor
        idx = self.cur.val(out_tensor.dtype, out_tensor.shape)
        self.cur.op(code, ins, idx)
        out_tensor.local = idx
        out_tensor.local_sub = self.cur_index
        return out_tensor

    def reshaped(self, tensor, shape, name=None):
        if tuple(shape) == tuple(tensor.shape):
            return tensor
        out = self.define(name or f'{tensor.name}/r{self.unique}', shape)
        self.unique += 1
        self.emit(nnapi.RESHAPE, [self.operand(tensor), self.cur.i32(list(shape))], out)
        return out

    def transposed(self, tensor, perm, name=None):
        out = self.define(name or f'{tensor.name}/t{self.unique}', [tensor.shape[p] for p in perm])
        self.unique += 1
        self.emit(nnapi.TRANSPOSE, [self.operand(tensor), self.cur.i32(list(perm))], out)
        return out

    def binary(self, code, a, b, out_shape, name):
        out = self.define(name, out_shape)
        self.emit(code, [self.operand(a), self.operand(b), self.scalar_i32(0)], out)
        return out

    # ------------------------------------------------------------- subgraphs
    def live_bytes(self, node_pos):
        total = 0
        for name, tensor in self.tensors.items():
            if tensor.const is not None or tensor.buf is not None:
                continue
            if self.last_use.get(name, -1) >= node_pos:
                total += prod(tensor.shape) * 2
        return total

    def start_subgraph(self):
        sub = Sub(self.plan)
        self.cur = sub
        self.cur_index = self.plan.add_sub(sub)

    def flush(self, node_pos):
        if self.cur is None or not self.cur.ops:
            return
        materialised = []
        for name, tensor in self.tensors.items():
            if tensor.const is not None or tensor.buf is not None or tensor.local is None:
                continue
            if tensor.local_sub != self.cur_index:
                continue
            if self.last_use.get(name, -1) < node_pos and name not in self.graph_outputs:
                continue
            buffer = self.plan.buffer(f'{sanitize(name)}_{self.unique}', F16, tensor.shape)
            self.unique += 1
            values = self.cur.values[tensor.local]
            self.cur.values[tensor.local] = (values[0], values[1], values[2], buffer)
            self.cur.outputs.append(tensor.local)
            tensor.buf = buffer
            tensor.local = None
            materialised.append(name)
        if self.verbose:
            print(f'  flush at node {node_pos}: {len(self.cur.ops)} ops, {len(materialised)} buffers')
        self.start_subgraph()

    def maybe_flush(self, node_pos):
        if len(self.cur.ops) >= 60:
            self.flush(node_pos)
            return
        if len(self.cur.ops) < self.budget_ops and self.live_bytes(node_pos) <= self.budget_bytes:
            return
        blockers = []
        for name, tensor in self.tensors.items():
            if tensor.const is not None or tensor.local is None:
                continue
            if tensor.local_sub != self.cur_index:
                continue
            if self.last_use.get(name, 1 << 30) > node_pos:
                blockers.append(name)
        if blockers:
            return
        self.flush(node_pos)

    # ------------------------------------------------------------------- run
    def run(self):
        self.graph_outputs = set(o.name for o in self.graph.output) | self.keep
        self.start_subgraph()
        for value in self.graph.input:
            tensor = Tensor(value.name, F16, tuple(self.shape_of(value.name)))
            tensor.buf = self.plan.buffer(sanitize(value.name), F16, tensor.shape)
            tensor.is_input = True
            self.tensors[value.name] = tensor
        for name, tensor in self.initializers.items():
            if name in self.tensors:
                continue
            self.tensors[name] = Tensor(name, self.dtypes.get(name, 10),
                                        tuple(self.shape_of(name) if name in self.shapes else tensor.shape),
                                        const=tensor)
        for position, node in enumerate(self.nodes):
            self.maybe_flush(position)
            op = node.op_type
            handler = getattr(self, 'op_' + op, None)
            if handler is None:
                raise NotImplementedError(f'{op} ({node.name})')
            handler(node)
        self.flush(len(self.nodes))
        for name in sorted(self.keep):
            tensor = self.tensors[name]
            if tensor.buf is None:
                raise RuntimeError(f'kept tensor not materialised: {name}')
            self.plan.results.append(tensor.buf)
        self.plan.subs = [s for s in self.plan.subs if s.ops]
        for out in (self.graph.output if self.limit is None else []):
            tensor = self.tensors[out.name]
            if tensor.buf is None:
                raise RuntimeError(f'graph output not materialised: {out.name}')
            self.plan.results.append(tensor.buf)
        return self.plan

    # --------------------------------------------------------------- handlers
    def in_shapes(self, node):
        return [self.shape_of(i) for i in node.input]

    def attrs(self, node):
        return {a.name: onnx.helper.get_attribute_value(a) for a in node.attribute}

    def op_Reshape(self, node):
        data = self.tensor(node.input[0])
        shape = [int(v) for v in self.tensor(node.input[1]).const.ravel()]
        out_shape = self.shape_of(node.output[0])
        if 0 in shape:
            shape = list(out_shape)
        out = self.define(node.output[0], out_shape)
        self.emit(nnapi.RESHAPE, [self.operand(data), self.cur.i32(shape)], out)

    def op_Squeeze(self, node):
        data = self.tensor(node.input[0])
        out_shape = self.shape_of(node.output[0])
        out = self.define(node.output[0], out_shape)
        self.emit(nnapi.RESHAPE, [self.operand(data), self.cur.i32(list(out_shape))], out)

    def op_Unsqueeze(self, node):
        data = self.tensor(node.input[0])
        out_shape = self.shape_of(node.output[0])
        out = self.define(node.output[0], out_shape)
        self.emit(nnapi.RESHAPE, [self.operand(data), self.cur.i32(list(out_shape))], out)

    def op_Transpose(self, node):
        data = self.tensor(node.input[0])
        perm = list(self.attrs(node)['perm'])
        out_shape = self.shape_of(node.output[0])
        if len(perm) > 4:
            raise NotImplementedError(f'rank {len(perm)} transpose {node.name}')
        out = self.define(node.output[0], out_shape)
        self.emit(nnapi.TRANSPOSE, [self.operand(data), self.cur.i32(perm)], out)

    def op_Slice(self, node):
        data = self.tensor(node.input[0])
        in_shape = self.shape_of(node.input[0])
        starts = self.tensor(node.input[1]).const.ravel().astype(np.int64)
        ends = self.tensor(node.input[2]).const.ravel().astype(np.int64)
        axes = self.tensor(node.input[3]).const.ravel().astype(np.int64) if len(node.input) > 3 else np.arange(len(starts))
        steps = self.tensor(node.input[4]).const.ravel().astype(np.int64) if len(node.input) > 4 and node.input[4] else np.ones_like(starts)
        if any(s != 1 for s in steps):
            raise NotImplementedError('slice steps')
        full_starts = [0] * len(in_shape)
        full_sizes = list(in_shape)
        for start, end, axis in zip(starts, ends, axes):
            axis = int(axis if axis >= 0 else axis + len(in_shape))
            size = int(in_shape[axis])
            start = int(start if start >= 0 else start + size)
            end = int(end if end >= 0 else end + size)
            end = min(end, size)
            start = max(0, min(start, size))
            full_starts[axis] = start
            full_sizes[axis] = max(0, end - start)
        out_shape = self.shape_of(node.output[0])
        out = self.define(node.output[0], out_shape)
        self.emit(nnapi.SLICE, [self.operand(data), self.cur.i32(full_starts), self.cur.i32(full_sizes)], out)

    def op_Concat(self, node):
        tensors = [self.tensor(i) for i in node.input]
        axis = int(self.attrs(node)['axis'])
        rank = len(tensors[0].shape)
        if axis < 0:
            axis += rank
        out_shape = self.shape_of(node.output[0])
        out = self.define(node.output[0], out_shape)
        ins = []
        for t in tensors:
            if len(t.shape) != rank:
                raise NotImplementedError('concat rank mismatch')
            ins.append(self.operand(t))
        ins.append(self.scalar_i32(axis))
        self.emit(nnapi.CONCATENATION, ins, out)

    def op_Add(self, node):
        self.elementwise(nnapi.ADD, node)

    def op_Sub(self, node):
        self.elementwise(nnapi.SUB, node)

    def op_Mul(self, node):
        self.elementwise(nnapi.MUL, node)

    def op_Div(self, node):
        self.elementwise(nnapi.DIV, node)

    def op_Pow(self, node):
        raise NotImplementedError('pow')

    def elementwise(self, code, node):
        a = self.tensor(node.input[0])
        b = self.tensor(node.input[1])
        out_shape = self.shape_of(node.output[0])
        raw_a = _raw_shape(self, a)
        raw_b = _raw_shape(self, b)
        if max(len(raw_a), len(raw_b)) > 4:
            result = _binary_v3(self, code, a, b, out_shape, node.output[0])
            self.tensors[node.output[0]] = result
            return
        rank = len(out_shape)
        a_shape = self.broadcast_shape(a.shape, rank)
        b_shape = self.broadcast_shape(b.shape, rank)
        a = self.align(a, a_shape)
        b = self.align(b, b_shape)
        out = self.define(node.output[0], out_shape)
        self.emit(code, [self.operand(a), self.operand(b), self.scalar_i32(0)], out)

    def broadcast_shape(self, shape, rank):
        shape = list(shape)
        while len(shape) < rank:
            shape.insert(0, 1)
        return shape

    def align(self, tensor, shape):
        if tuple(shape) == tuple(tensor.shape):
            return tensor
        if tensor.const is None:
            return self.reshaped(tensor, shape)
        return self.define(tensor.name + '/aligned', shape,
                           const=np.asarray(tensor.const).reshape(shape))

    def op_Relu(self, node):
        data = self.tensor(node.input[0])
        out = self.define(node.output[0], self.shape_of(node.output[0]))
        self.emit(nnapi.RELU, [self.operand(data)], out)

    def op_Sigmoid(self, node):
        data = self.tensor(node.input[0])
        out = self.define(node.output[0], self.shape_of(node.output[0]))
        self.emit(nnapi.LOGISTIC, [self.operand(data)], out)

    def op_Tanh(self, node):
        data = self.tensor(node.input[0])
        out = self.define(node.output[0], self.shape_of(node.output[0]))
        self.emit(nnapi.TANH, [self.operand(data)], out)

    def op_Softmax(self, node):
        data = self.tensor(node.input[0])
        axis = int(self.attrs(node).get('axis', -1))
        rank = len(data.shape)
        if axis < 0:
            axis += rank
        if axis != rank - 1:
            raise NotImplementedError(f'softmax axis {axis} rank {rank}')
        out_shape = self.shape_of(node.output[0])
        if data.shape[-1] > 2048:
            # MDLA's native 4800-wide softmax can return probabilities > 1.
            # Stage both reductions to keep each reduction axis short.
            width = data.shape[-1]
            rows = prod(data.shape[:-1])
            flat = self.reshaped(data, (1, rows, width), node.name + '/flat')
            block = next(d for d in range(256, 1, -1) if width % d == 0)
            split = self.reshaped(flat, (1, rows, width // block, block), node.name + '/split')
            partial = self.define(node.name + '/max_partial', (1, rows, width // block, 1))
            self.emit(nnapi.REDUCE_MAX, [self.operand(split), self.cur.i32([3]), self.cur.scalar_bool(True)], partial)
            maximum = self.define(node.name + '/max', (1, rows, 1, 1))
            self.emit(nnapi.REDUCE_MAX, [self.operand(partial), self.cur.i32([2]), self.cur.scalar_bool(True)], maximum)
            maximum = self.reshaped(maximum, (1, rows, 1), node.name + '/max3')
            shifted = self.binary(nnapi.SUB, flat, maximum, flat.shape, node.name + '/shifted')
            exp = self.define(node.name + '/exp', flat.shape)
            self.emit(nnapi.EXP, [self.operand(shifted)], exp)
            mean = _spatial_mean_staged(self, exp, node.name + '/mean_exp')
            scale = _minimal(self, mean.shape, width, node.name + '/width')
            denominator = self.binary(nnapi.MUL, mean, scale, mean.shape, node.name + '/denominator')
            result = self.binary(nnapi.DIV, exp, denominator, flat.shape, node.name + '/normalized')
            self.tensors[node.output[0]] = self.reshaped(result, out_shape, node.output[0])
            return
        out = self.define(node.output[0], out_shape)
        self.emit(nnapi.SOFTMAX, [self.operand(data), self.cur.scalar_f16(1.0), self.scalar_i32(axis)], out)

    def op_MatMul(self, node):
        a = self.tensor(node.input[0])
        b = self.tensor(node.input[1])
        out_shape = self.shape_of(node.output[0])
        out = self.define(node.output[0], out_shape)
        self.emit(nnapi.BATCH_MATMUL, [self.operand(a), self.operand(b),
                                       self.cur.scalar_bool(False), self.cur.scalar_bool(False)], out)

    def op_Gemm(self, node):
        attrs = self.attrs(node)
        a = self.tensor(node.input[0])
        b = self.tensor(node.input[1])
        out_shape = self.shape_of(node.output[0])
        if attrs.get('transA', 0) or attrs.get('transB', 0):
            raise NotImplementedError('gemm transpose')
        if abs(attrs.get('alpha', 1.0) - 1.0) > 1e-8 or abs(attrs.get('beta', 1.0) - 1.0) > 1e-8:
            raise NotImplementedError('gemm alpha/beta')
        flat = int(np.prod(a.shape))
        rows = flat // int(a.shape[-1])
        a2 = self.reshaped(a, (rows, a.shape[-1])) if len(a.shape) != 2 else a
        out2_shape = (rows, b.shape[0])
        out2 = self.define(node.output[0] + '/fc', out2_shape)
        if b.const is None:
            raise NotImplementedError('dynamic gemm weight')
        weight = np.ascontiguousarray(np.asarray(b.const, dtype=np.float16).T)
        weight_t = self.define(node.input[1] + '/fcW', weight.shape, const=weight)
        bias = self.tensor(node.input[2]) if len(node.input) > 2 else None
        if bias is None:
            zero = self.define(node.output[0] + '/zero', (b.shape[0],), const=np.zeros(b.shape[0], np.float16))
            bias = zero
        self.emit(nnapi.FULLY_CONNECTED, [self.operand(a2), self.operand(weight_t), self.operand(bias),
                                          self.scalar_i32(0)], out2)
        out = self.define(node.output[0], out_shape)
        self.emit(nnapi.RESHAPE, [self.operand(out2), self.cur.i32(list(out_shape))], out)

    def op_LayerNormalization(self, node):
        attrs = self.attrs(node)
        axis = int(attrs.get('axis', -1))
        eps = float(attrs.get('epsilon', 1e-5))
        data = self.tensor(node.input[0])
        shape = data.shape
        rank = len(shape)
        if axis < 0:
            axis += rank
        if axis != rank - 1:
            raise NotImplementedError('layernorm axis')
        gamma = self.tensor(node.input[1])
        beta = self.tensor(node.input[2]) if len(node.input) > 2 else None
        out = self.layernorm(data, gamma, beta, eps, shape, node.output[0])
        out.name = node.output[0]
        self.tensors[node.output[0]] = out

    def layernorm(self, data, gamma, beta, eps, shape, name):
        base = name
        mean = self.define(base + '/mean', tuple(list(shape[:-1]) + [1]))
        self.emit(nnapi.MEAN, [self.operand(data), self.cur.i32([len(shape) - 1]), self.scalar_i32(1)], mean)
        centered = self.binary(nnapi.SUB, data, mean, shape, base + '/center')
        squared = self.binary(nnapi.MUL, centered, centered, shape, base + '/sq')
        variance = self.define(base + '/var', tuple(list(shape[:-1]) + [1]))
        self.emit(nnapi.MEAN, [self.operand(squared), self.cur.i32([len(shape) - 1]), self.scalar_i32(1)], variance)
        eps_t = self.define(base + '/eps', tuple(list(shape[:-1]) + [1]),
                            const=np.full(list(shape[:-1]) + [1], eps, np.float16))
        shifted = self.binary(nnapi.ADD, variance, eps_t, variance.shape, base + '/ve')
        inv = self.define(base + '/inv', variance.shape)
        self.emit(nnapi.RSQRT, [self.operand(shifted)], inv)
        normed = self.binary(nnapi.MUL, centered, inv, shape, base + '/normed')
        gamma_t = self.align(gamma, list(gamma.shape))
        scaled = self.binary(nnapi.MUL, normed, gamma_t, shape, base + '/scaled')
        if beta is not None:
            final = self.binary(nnapi.ADD, scaled, beta, shape, base + '/final')
        else:
            final = scaled
        return final

    def op_Erf(self, node):
        data = self.tensor(node.input[0])
        shape = self.shape_of(node.output[0])
        out = self.erf(data, shape, node.output[0])
        out.name = node.output[0]
        self.tensors[node.output[0]] = out

    def erf(self, data, shape, name):
        # erf(z) ~ tanh(1.1283791670955126 z + 0.08943 z^3), |z| clipped to 4
        low = self.define(name + '/low', shape, const=np.full(shape, -4.0, np.float16))
        high = self.define(name + '/high', shape, const=np.full(shape, 4.0, np.float16))
        clipped = self.binary(nnapi.MAXIMUM, data, low, shape, name + '/clip_lo')
        clipped = self.binary(nnapi.MINIMUM, clipped, high, shape, name + '/clip_hi')
        square = self.binary(nnapi.MUL, clipped, clipped, shape, name + '/sq')
        cube = self.binary(nnapi.MUL, square, clipped, shape, name + '/cube')
        c1 = self.define(name + '/c1', shape, const=np.full(shape, 0.08943, np.float16))
        c2 = self.define(name + '/c2', shape, const=np.full(shape, 1.1283791670955126, np.float16))
        corr = self.binary(nnapi.MUL, cube, c1, shape, name + '/corr')
        total = self.binary(nnapi.ADD, clipped, corr, shape, name + '/sum')
        scaled = self.binary(nnapi.MUL, total, c2, shape, name + '/scaled')
        out = self.define(name, shape)
        self.emit(nnapi.TANH, [self.operand(scaled)], out)
        return out

    def op_ReduceSum(self, node):
        self.reduce(nnapi.REDUCE_SUM, node)

    def op_ReduceMax(self, node):
        self.reduce(nnapi.REDUCE_MAX, node)

    def op_ReduceMean(self, node):
        self.reduce(nnapi.MEAN, node)

    def reduce(self, code, node):
        attrs = self.attrs(node)
        data = self.tensor(node.input[0])
        if len(node.input) > 1 and node.input[1]:
            axes = [int(a) for a in self.tensor(node.input[1]).const.ravel()]
        else:
            axes = [int(a) for a in attrs.get('axes', [])]
        rank = len(data.shape)
        axes = [a + rank if a < 0 else a for a in axes]
        keep = int(attrs.get('keepdims', 1))
        out_shape = self.shape_of(node.output[0])
        out = self.define(node.output[0], out_shape)
        keep_operand = self.scalar_i32(keep) if code == nnapi.MEAN else self.cur.scalar_bool(bool(keep))
        self.emit(code, [self.operand(data), self.cur.i32(axes), keep_operand], out)

    def op_ArgMax(self, node):
        attrs = self.attrs(node)
        data = self.tensor(node.input[0])
        axis = int(attrs.get('axis', 0))
        rank = len(data.shape)
        if axis < 0:
            axis += rank
        if axis != rank - 1:
            perm = [i for i in range(rank) if i != axis] + [axis]
            data = self.transposed(data, perm, node.name + '/axis_last')
            axis = rank - 1
        out_shape = self.shape_of(node.output[0])
        out = self.define(node.output[0], out_shape, dtype=I32)
        idx = self.cur.val(I32, out_shape)
        self.cur.op(nnapi.ARGMAX, [self.operand(data), self.scalar_i32(axis)], idx)
        out.local = idx
        out.local_sub = self.cur_index
        out.dtype = I32

    def op_Cast(self, node):
        data = self.tensor(node.input[0])
        target = int(self.attrs(node)['to'])
        out_shape = self.shape_of(node.output[0])
        if target in (9, 10, 1):   # fp32/fp16/int32 markers -> keep fp16 payload
            out = self.define(node.output[0], out_shape)
            self.emit(nnapi.RESHAPE, [self.operand(data), self.cur.i32(list(out_shape))], out)
        else:
            raise NotImplementedError(f'cast to {target}')

    def op_Pad(self, node):
        data = self.tensor(node.input[0])
        attrs = self.attrs(node)
        pads = [int(v) for v in self.tensor(node.input[1]).const.ravel()]
        rank = len(data.shape)
        pads = np.array(pads, dtype=np.int32).reshape(2, rank).T
        out_shape = self.shape_of(node.output[0])
        out = self.define(node.output[0], out_shape)
        paddings = self.cur.i32(pads.reshape(-1).tolist(), (rank, 2))
        if attrs.get('mode', b'constant') not in (b'constant',):
            raise NotImplementedError('pad mode')
        value = self.cur.scalar_f16(0.0)
        self.emit(nnapi.PAD_V2, [self.operand(data), paddings, value], out)

    def op_Tile(self, node):
        data = self.tensor(node.input[0])
        repeats = [int(v) for v in self.tensor(node.input[1]).const.ravel()]
        out_shape = self.shape_of(node.output[0])
        out = self.define(node.output[0], out_shape)
        self.emit(nnapi.TILE, [self.operand(data), self.cur.i32(repeats)], out)

    def op_Expand(self, node):
        data = self.tensor(node.input[0])
        out_shape = self.shape_of(node.output[0])
        rank = len(out_shape)
        data = self.align(data, self.broadcast_shape(data.shape, rank))
        in_shape = data.shape
        repeats = [int(o // i) if i else 1 for o, i in zip(out_shape, in_shape)]
        out = self.define(node.output[0], out_shape)
        self.emit(nnapi.TILE, [self.operand(data), self.cur.i32(repeats)], out)

    def op_Shape(self, node):
        raise NotImplementedError('shape')

    def op_Constant(self, node):
        raise NotImplementedError('constant node')

    def op_Less(self, node):
        a = self.tensor(node.input[0])
        b = self.tensor(node.input[1])
        out_shape = self.shape_of(node.output[0])
        rank = len(out_shape)
        a = self.align(a, self.broadcast_shape(a.shape, rank))
        b = self.align(b, self.broadcast_shape(b.shape, rank))
        # less = clamp((b - a) * 4096, 0, 1)
        diff = self.binary(nnapi.SUB, b, a, out_shape, node.output[0] + '/diff')
        zero = self.define(node.output[0] + '/z', out_shape, const=np.zeros(out_shape, np.float16))
        pos = self.binary(nnapi.MAXIMUM, diff, zero, out_shape, node.output[0] + '/pos')
        scale = self.define(node.output[0] + '/s', out_shape, const=np.full(out_shape, 4096.0, np.float16))
        scaled = self.binary(nnapi.MUL, pos, scale, out_shape, node.output[0] + '/scaled')
        one = self.define(node.output[0] + '/o', out_shape, const=np.ones(out_shape, np.float16))
        mask = self.binary(nnapi.MINIMUM, scaled, one, out_shape, node.output[0] + '/mask')
        mask.name = node.output[0]
        self.tensors[node.output[0]] = mask

    def op_Where(self, node):
        cond = self.tensor(node.input[0])
        a = self.tensor(node.input[1])
        b = self.tensor(node.input[2])
        out_shape = self.shape_of(node.output[0])
        rank = len(out_shape)
        cond = self.align(cond, self.broadcast_shape(cond.shape, rank))
        a = self.align(a, self.broadcast_shape(a.shape, rank))
        b = self.align(b, self.broadcast_shape(b.shape, rank))
        diff = self.binary(nnapi.SUB, a, b, out_shape, node.output[0] + '/diff')
        scaled = self.binary(nnapi.MUL, cond, diff, out_shape, node.output[0] + '/scaled')
        out = self.binary(nnapi.ADD, b, scaled, out_shape, node.output[0])
        out.name = node.output[0]
        self.tensors[node.output[0]] = out

    def op_Floor(self, node):
        data = self.tensor(node.input[0])
        shape = self.shape_of(node.output[0])
        raise NotImplementedError('floor')

    def op_Split(self, node):
        data = self.tensor(node.input[0])
        attrs = self.attrs(node)
        axis = int(attrs.get('axis', 0))
        rank = len(data.shape)
        if axis < 0:
            axis += rank
        splits = [int(v) for v in self.tensor(node.input[1]).const.ravel()]
        offset = 0
        for index, size in enumerate(splits):
            starts = [0] * rank
            sizes = list(data.shape)
            starts[axis] = offset
            sizes[axis] = size
            offset += size
            out_shape = self.shape_of(node.output[index])
            out = self.define(node.output[index], out_shape)
            self.emit(nnapi.SLICE, [self.operand(data), self.cur.i32(starts), self.cur.i32(sizes)], out)

    def op_Conv(self, node):
        attrs = self.attrs(node)
        data = self.tensor(node.input[0])
        weight = self.tensor(node.input[1])
        bias = self.tensor(node.input[2]) if len(node.input) > 2 else None
        w = np.asarray(weight.const, dtype=np.float16)
        # ONNX [O, C, kh, kw] -> NNAPI [O, kh, kw, C]
        w = np.transpose(w, (0, 2, 3, 1)).copy()
        out_shape = self.shape_of(node.output[0])
        nhwc_in = (data.shape[0], data.shape[2], data.shape[3], data.shape[1])
        nhwc_out = (out_shape[0], out_shape[2], out_shape[3], out_shape[1])
        x = self.transposed(data, [0, 2, 3, 1], node.name + '/nchw2nhwc')
        w_t = self.define(node.name + '/w', w.shape, const=w)
        b_t = bias
        if b_t is None:
            b_t = self.define(node.name + '/b', (out_shape[1],), const=np.zeros(out_shape[1], np.float16))
        y = self.define(node.name + '/conv', nhwc_out)
        pads = attrs.get('pads', [0, 0, 0, 0])
        top, left, bottom, right = pads[0], pads[1], pads[2], pads[3]
        strides = attrs.get('strides', [1, 1])
        self.emit(nnapi.CONV_2D, [self.operand(x), self.operand(w_t), self.operand(b_t),
                                  self.scalar_i32(left), self.scalar_i32(right),
                                  self.scalar_i32(top), self.scalar_i32(bottom),
                                  self.scalar_i32(strides[1]), self.scalar_i32(strides[0]),
                                  self.scalar_i32(0)], y)
        back = self.transposed(y, [0, 3, 1, 2], node.output[0])
        back.name = node.output[0]
        self.tensors[node.output[0]] = back

    def op_Resize(self, node):
        attrs = self.attrs(node)
        data = self.tensor(node.input[0])
        sizes = None
        if len(node.input) > 3 and node.input[3]:
            sizes = [int(v) for v in self.tensor(node.input[3]).const.ravel()]
        if sizes is None:
            raise NotImplementedError('resize without sizes')
        mode = attrs.get('mode', b'nearest')
        if mode != b'linear':
            raise NotImplementedError(f'resize mode {mode}')
        if attrs.get('coordinate_transformation_mode', b'half_pixel') != b'half_pixel':
            raise NotImplementedError('resize coord mode')
        out_shape = self.shape_of(node.output[0])
        if tuple(data.shape) == tuple(out_shape):
            self.tensors[node.output[0]] = data
            self.last_use[data.name] = max(self.last_use.get(data.name, -1),
                                           self.last_use.get(node.output[0], self.position))
            return
        x = self.transposed(data, [0, 2, 3, 1], node.name + '/nchw2nhwc')
        nhwc_out = (out_shape[0], out_shape[2], out_shape[3], out_shape[1])
        resize_shape = list(nhwc_out)
        pad = [0] * 8
        for axis in (1, 2):
            if resize_shape[axis] < 16:
                # Preserve the exact half-pixel scale by extending the input
                # and output by the same rational factor, then crop the tail.
                import math
                period = resize_shape[axis] // math.gcd(x.shape[axis], resize_shape[axis])
                extra = ((16 - resize_shape[axis] + period - 1) // period) * period
                pad[2 * axis + 1] = extra * x.shape[axis] // resize_shape[axis]
                resize_shape[axis] += extra
        if any(pad):
            padded_shape = tuple(x.shape[i] + pad[2*i+1] for i in range(4))
            padded = self.define(node.name + '/resize_pad', padded_shape)
            self.emit(nnapi.PAD, [self.operand(x), self.cur.i32(pad, (4, 2))], padded)
            x = padded
        y = self.define(node.name + '/resize', tuple(resize_shape))
        self.emit(nnapi.RESIZE_BILINEAR, [self.operand(x), self.scalar_i32(resize_shape[2]),
                                          self.scalar_i32(resize_shape[1]),
                                          self.cur.scalar_bool(False), self.cur.scalar_bool(False),
                                          self.cur.scalar_bool(True)], y)
        if tuple(resize_shape) != nhwc_out:
            y = _slice_t(self, y, (0, 0, 0, 0), nhwc_out, node.name + '/resize_crop')
        back = self.transposed(y, [0, 3, 1, 2], node.output[0])
        back.name = node.output[0]
        self.tensors[node.output[0]] = back

    def op_InstanceNormalization(self, node):
        attrs = self.attrs(node)
        eps = float(attrs.get('epsilon', 1e-5))
        data = self.tensor(node.input[0])
        scale = self.tensor(node.input[1])
        bias = self.tensor(node.input[2])
        shape = data.shape
        rank = len(shape)
        axes = list(range(2, rank))
        mean = self.define(node.output[0] + '/mean', tuple(shape[:2]) + (1,) * (rank - 2))
        self.emit(nnapi.MEAN, [self.operand(data), self.cur.i32(axes), self.scalar_i32(1)], mean)
        centered = self.binary(nnapi.SUB, data, mean, shape, node.output[0] + '/center')
        squared = self.binary(nnapi.MUL, centered, centered, shape, node.output[0] + '/sq')
        variance = self.define(node.output[0] + '/var', mean.shape)
        self.emit(nnapi.MEAN, [self.operand(squared), self.cur.i32(axes), self.scalar_i32(1)], variance)
        eps_t = self.define(node.output[0] + '/eps', mean.shape,
                            const=np.full(mean.shape, eps, np.float16))
        shifted = self.binary(nnapi.ADD, variance, eps_t, mean.shape, node.output[0] + '/ve')
        inv = self.define(node.output[0] + '/inv', mean.shape)
        self.emit(nnapi.RSQRT, [self.operand(shifted)], inv)
        normed = self.binary(nnapi.MUL, centered, inv, shape, node.output[0] + '/normed')
        scale_t = self.align(scale, list(scale.shape))
        scaled = self.binary(nnapi.MUL, normed, scale_t, shape, node.output[0] + '/scaled')
        final = self.binary(nnapi.ADD, scaled, bias, shape, node.output[0])
        final.name = node.output[0]
        self.tensors[node.output[0]] = final

# ---------------------------------------------------------------------------
# Lazy shape derivation for rewritten tensors that lack value_info entries.
def _producer_map(self):
    mapping = getattr(self, '_producers', None)
    if mapping is None:
        mapping = {}
        for node in self.graph.node:
            for out in node.output:
                if out:
                    mapping[out] = node
        self._producers = mapping
    return mapping


def _derive_shape(self, name):
    cache = getattr(self, '_derived', None)
    if cache is None:
        cache = self._derived = {}
    if name in cache:
        return cache[name]
    node = _producer_map(self).get(name)
    if node is None:
        raise KeyError('no shape for ' + name)
    op = node.op_type
    if op == 'Reshape':
        raw = [int(v) for v in self.tensor(node.input[1]).const.ravel()]
        src = self.shape_of(node.input[0])
        shape = []
        for index, value in enumerate(raw):
            if value == 0:
                shape.append(src[index])
            elif value == -1:
                total = 1
                for d in shape:
                    total *= d
                rest = 1
                for d in src:
                    rest *= d
                shape.append(rest // total)
            else:
                shape.append(value)
        cache[name] = tuple(shape)
        return cache[name]
    if op == 'Transpose':
        perm = [a for a in node.attribute if a.name == 'perm'][0].ints
        src = self.shape_of(node.input[0])
        cache[name] = tuple(src[p] for p in perm)
        return cache[name]
    if op in ('Squeeze', 'Unsqueeze'):
        src = self.shape_of(node.input[0])
        axes = [int(v) for v in self.tensor(node.input[1]).const.ravel()]
        shape = list(src)
        for axis in sorted((a + len(shape) if a < 0 else a for a in axes), reverse=True):
            if op == 'Squeeze':
                shape.pop(axis)
            else:
                shape.insert(axis, 1)
        cache[name] = tuple(shape)
        return cache[name]
    raise KeyError('cannot derive shape for ' + name + ' (' + op + ')')


def _shape_of(self, name):
    if name in self.shapes:
        return self.shapes[name]
    return _derive_shape(self, name)


Lowerer.shape_of = _shape_of


# ---------------------------------------------------------------------------
# Handlers that need NNAPI ops beyond the basic set.
def _less_tensor(self, a, b, shape, name):
    diff = self.binary(nnapi.SUB, b, a, shape, name + '/d')
    zero = self.define(name + '/z', shape, const=np.zeros(shape, np.float16))
    pos = self.binary(nnapi.MAXIMUM, diff, zero, shape, name + '/p')
    scale = self.define(name + '/s', shape, const=np.full(shape, 4096.0, np.float16))
    scaled = self.binary(nnapi.MUL, pos, scale, shape, name + '/m')
    one = self.define(name + '/o', shape, const=np.ones(shape, np.float16))
    return self.binary(nnapi.MINIMUM, scaled, one, shape, name + '/lt')


def _op_slice(self, node):
    data = self.tensor(node.input[0])
    in_shape = list(self.shape_of(node.input[0]))
    starts = self.tensor(node.input[1]).const.ravel().astype(np.int64)
    ends = self.tensor(node.input[2]).const.ravel().astype(np.int64)
    axes = self.tensor(node.input[3]).const.ravel().astype(np.int64) if len(node.input) > 3 and node.input[3] \
        else np.arange(len(starts))
    steps = self.tensor(node.input[4]).const.ravel().astype(np.int64) if len(node.input) > 4 and node.input[4] \
        else np.ones_like(starts)
    rank = len(in_shape)
    full_starts = [0] * rank
    full_ends = list(in_shape)
    full_strides = [1] * rank
    for start, end, axis, step in zip(starts, ends, axes, steps):
        axis = int(axis + rank if axis < 0 else axis)
        size = in_shape[axis]
        step = int(step)
        if step < 0:
            raise NotImplementedError('negative slice step')
        start = int(start + size if start < 0 else start)
        start = max(0, min(start, size))
        if end < 0:
            end = int(end + size)
        end = max(0, min(int(end), size))
        full_starts[axis] = start
        full_ends[axis] = end
        full_strides[axis] = step
    out_shape = self.shape_of(node.output[0])
    out = self.define(node.output[0], out_shape)
    if all(s == 1 for s in full_strides):
        sizes = [max(0, e - s) for e, s in zip(full_ends, full_starts)]
        self.emit(nnapi.SLICE, [self.operand(data), self.cur.i32(full_starts), self.cur.i32(sizes)], out)
    else:
        index = self.cur.val(F16, out_shape)
        self.cur.op(nnapi.STRIDED_SLICE,
                    [self.operand(data), self.cur.i32(full_starts), self.cur.i32(full_ends),
                     self.cur.i32(full_strides), self.scalar_i32(0), self.scalar_i32(0),
                     self.scalar_i32(0)], index)
        out.local = index
        out.local_sub = self.cur_index


def _op_str(stride_slice):
    pass


def _op_floor(self, node):
    data = self.tensor(node.input[0])
    shape = self.shape_of(node.output[0])
    base = node.output[0]
    offset = self.define(base + '/off', shape, const=np.full(shape, 1024.0, np.float16))
    shifted = self.binary(nnapi.ADD, data, offset, shape, base + '/shifted')
    back = self.define(base + '/back', shape, const=np.full(shape, -1024.0, np.float16))
    rounded = self.binary(nnapi.ADD, shifted, back, shape, base + '/rounded')
    up = _less_tensor(self, data, rounded, shape, base + '/up')
    candidate = self.binary(nnapi.SUB, rounded, up, shape, base + '/cand')
    limit = self.define(base + '/lim', shape, const=np.full(shape, 1024.0, np.float16))
    active = _less_tensor(self, data, limit, shape, base + '/act')
    one = self.define(base + '/one', shape, const=np.ones(shape, np.float16))
    inactive = self.binary(nnapi.SUB, one, active, shape, base + '/inact')
    left = self.binary(nnapi.MUL, candidate, active, shape, base + '/left')
    right = self.binary(nnapi.MUL, data, inactive, shape, base + '/right')
    out = self.binary(nnapi.ADD, left, right, shape, base)
    out.name = node.output[0]
    self.tensors[node.output[0]] = out


Lowerer.op_Slice = _op_slice
Lowerer.op_Floor = _op_floor


# ---------------------------------------------------------------------------
# NNAPI caps most element-wise ops at rank 4, and MINIMUM/MAXIMUM take no
# fused-activation operand.  Constants are kept minimal so plans stay small.
def _squeeze_rank(self, tensor, limit=4):
    shape = list(tensor.shape)
    while len(shape) > limit and shape[0] == 1:
        shape.pop(0)
    if len(shape) == len(tensor.shape):
        return tensor
    return self.reshaped(tensor, tuple(shape))


def _minimal(self, shape, value, name):
    ones = tuple([1] * len(shape))
    return self.define(name, ones, const=np.full(ones, value, np.float16))


def _binary_v2(self, code, a, b, out_shape, name, fuse=True):
    out_shape = tuple(out_shape)
    for slot, (tensor, other) in enumerate(()):
        if tensor.const is None or len(tensor.shape) <= len(other.shape):
            continue
        try:
            _broadcast_raw(tuple(tensor.shape), tuple(other.shape))
            continue
        except ValueError:
            pass
        virt = (getattr(self, '_orig_shape', {}).get(other.name)
                or getattr(self, '_virtual', {}).get(other.name)
                or self.shapes.get(other.name) or tuple(other.shape))
        virt = tuple(virt)
        if len(virt) < 3:
            continue
        row = [1, 1] + list(virt[2:])
        body = np.asarray(tensor.const, dtype=np.float16)
        pad = (1,) * (len(row) - body.ndim) + tuple(body.shape)
        body = body.reshape(pad)
        full = [1 if pad[i] == 1 else row[i] for i in range(len(row))]
        flat = np.broadcast_to(body, tuple(full)).reshape(1, int(np.prod(full[2:])))
        tiled = self.define(tensor.name + '/row', flat.shape, const=np.ascontiguousarray(flat))
        if slot == 0:
            a = tiled
        else:
            b = tiled
    if len(out_shape) == 4 and _nh4(list(out_shape)) > 65535:
        try:
            repaired = _broadcast_raw(tuple(a.shape), tuple(b.shape))
        except ValueError:
            repaired = None
        if repaired is not None and (len(repaired) < 4 or _nh4(list(repaired)) <= 65535):
            enlarged = []
            for tensor in (a, b):
                if tensor.const is not None:
                    other = b if tensor is a else a
                    virt = getattr(self, '_virtual', {}).get(other.name) or self.shapes.get(other.name) or tuple(other.shape)
                    virt = tuple(virt)
                    if len(virt) >= 3:
                        row = list(virt)
                        row[0] = 1
                        if len(row) > 2:
                            row[1] = 1
                        body = np.asarray(tensor.const, dtype=np.float16)
                        pad = (1,) * (len(row) - body.ndim) + tuple(body.shape)
                        body = body.reshape(pad)
                        full = [1 if pad[i] == 1 else row[i] for i in range(len(row))]
                        try:
                            expanded = np.broadcast_to(body, tuple(full))
                            flat = expanded.reshape(1, int(np.prod(full[1:])))
                            enlarged.append(self.define(tensor.name + '/rep', flat.shape,
                                                        const=np.ascontiguousarray(flat)))
                        except ValueError:
                            enlarged.append(tensor)
                    else:
                        enlarged.append(tensor)
                else:
                    enlarged.append(tensor)
            a, b = enlarged
            out_shape = tuple(repaired)
            self.shapes[name] = out_shape
    target = out_shape
    if len(out_shape) > 4:
        target = list(out_shape)
        while len(target) > 4 and target[0] == 1:
            target.pop(0)
        target = tuple(target)
        a = _squeeze_rank(self, a)
        b = _squeeze_rank(self, b)
    out = self.define(name, target)
    ins = [self.operand(a), self.operand(b)]
    if fuse:
        ins.append(self.scalar_i32(0))
    self.emit(code, ins, out)
    if target != out_shape:
        final = self.define(name + '/unsq', out_shape)
        self.emit(nnapi.RESHAPE, [self.operand(out), self.cur.i32(list(out_shape))], final)
        return final
    return out


def _erf_v2(self, data, shape, name):
    low = _minimal(self, shape, -4.0, name + '/low')
    high = _minimal(self, shape, 4.0, name + '/high')
    clipped = self.binary(nnapi.MAXIMUM, data, low, shape, name + '/clip_lo', fuse=False)
    clipped = self.binary(nnapi.MINIMUM, clipped, high, shape, name + '/clip_hi', fuse=False)
    square = self.binary(nnapi.MUL, clipped, clipped, shape, name + '/sq')
    cube = self.binary(nnapi.MUL, square, clipped, shape, name + '/cube')
    c1 = _minimal(self, shape, 0.08943, name + '/c1')
    c2 = _minimal(self, shape, 1.1283791670955126, name + '/c2')
    corr = self.binary(nnapi.MUL, cube, c1, shape, name + '/corr')
    total = self.binary(nnapi.ADD, clipped, corr, shape, name + '/sum')
    scaled = self.binary(nnapi.MUL, total, c2, shape, name + '/scaled')
    out = self.define(name, shape)
    self.emit(nnapi.TANH, [self.operand(scaled)], out)
    return out


def _op_erf_v2(self, node):
    data = self.tensor(node.input[0])
    shape = self.shape_of(node.output[0])
    out = _erf_v2(self, data, shape, node.output[0])
    out.name = node.output[0]
    self.tensors[node.output[0]] = out


def _less_v2(self, a, b, shape, name):
    diff = self.binary(nnapi.SUB, b, a, shape, name + '/d')
    zero = _minimal(self, shape, 0.0, name + '/z')
    pos = self.binary(nnapi.MAXIMUM, diff, zero, shape, name + '/p', fuse=False)
    scale = _minimal(self, shape, 4096.0, name + '/s')
    scaled = self.binary(nnapi.MUL, pos, scale, shape, name + '/m')
    one = _minimal(self, shape, 1.0, name + '/o')
    return self.binary(nnapi.MINIMUM, scaled, one, shape, name + '/lt', fuse=False)


def _op_less_v2(self, node):
    a = self.tensor(node.input[0])
    b = self.tensor(node.input[1])
    out_shape = self.shape_of(node.output[0])
    rank = len(out_shape)
    a = self.align(a, self.broadcast_shape(a.shape, rank))
    b = self.align(b, self.broadcast_shape(b.shape, rank))
    mask = _less_v2(self, a, b, out_shape, node.output[0])
    mask.name = node.output[0]
    self.tensors[node.output[0]] = mask


def _op_where_v2(self, node):
    cond = self.tensor(node.input[0])
    a = self.tensor(node.input[1])
    b = self.tensor(node.input[2])
    if (node.name.endswith('/multihead_attn/Where') and a.const is not None
            and np.isneginf(a.const).all() and b.const is not None and np.all(b.const == 0)):
        # Arithmetic selection with -inf produces 0 * inf = NaN. A finite
        # attention sentinel has zero softmax mass in FP16 without that NaN.
        a = self.define(node.output[0] + '/masked_logit', a.shape,
                        const=np.full(a.shape, -10000.0, np.float16))
    out_shape = self.shape_of(node.output[0])
    rank = len(out_shape)
    cond = self.align(cond, self.broadcast_shape(cond.shape, rank))
    a = self.align(a, self.broadcast_shape(a.shape, rank))
    b = self.align(b, self.broadcast_shape(b.shape, rank))
    diff = self.binary(nnapi.SUB, a, b, out_shape, node.output[0] + '/diff')
    scaled = self.binary(nnapi.MUL, cond, diff, out_shape, node.output[0] + '/scaled')
    out = self.binary(nnapi.ADD, b, scaled, out_shape, node.output[0])
    out.name = node.output[0]
    self.tensors[node.output[0]] = out


def _op_floor_v2(self, node):
    data = self.tensor(node.input[0])
    shape = self.shape_of(node.output[0])
    base = node.output[0]
    offset = _minimal(self, shape, 1024.0, base + '/off')
    shifted = self.binary(nnapi.ADD, data, offset, shape, base + '/shifted')
    back = _minimal(self, shape, -1024.0, base + '/back')
    rounded = self.binary(nnapi.ADD, shifted, back, shape, base + '/rounded')
    up = _less_v2(self, data, rounded, shape, base + '/up')
    candidate = self.binary(nnapi.SUB, rounded, up, shape, base + '/cand')
    limit = _minimal(self, shape, 1024.0, base + '/lim')
    active = _less_v2(self, data, limit, shape, base + '/act')
    one = _minimal(self, shape, 1.0, base + '/one')
    inactive = self.binary(nnapi.SUB, one, active, shape, base + '/inact')
    left = self.binary(nnapi.MUL, candidate, active, shape, base + '/left')
    right = self.binary(nnapi.MUL, data, inactive, shape, base + '/right')
    out = self.binary(nnapi.ADD, left, right, shape, base)
    out.name = node.output[0]
    self.tensors[node.output[0]] = out


Lowerer.binary = _binary_v2
Lowerer.erf = _erf_v2
Lowerer.op_Erf = _op_erf_v2
Lowerer.op_Less = _op_less_v2
Lowerer.op_Where = _op_where_v2
Lowerer.op_Floor = _op_floor_v2


# ---------------------------------------------------------------------------
# Add/Sub/Mul/Div must go through the rank-4 aware binary emitter.
def _elementwise_v2(self, code, node):
    a = self.tensor(node.input[0])
    b = self.tensor(node.input[1])
    out_shape = self.shape_of(node.output[0])
    raw_a = _raw_shape(self, a)
    raw_b = _raw_shape(self, b)
    if max(len(raw_a), len(raw_b)) > 4:
        result = _binary_v3(self, code, a, b, out_shape, node.output[0])
        result.name = node.output[0]
        self.tensors[node.output[0]] = result
        return
    rank = len(out_shape)
    a = self.align(a, self.broadcast_shape(a.shape, rank))
    b = self.align(b, self.broadcast_shape(b.shape, rank))
    out = _binary_v2(self, code, a, b, out_shape, node.output[0])
    out.name = node.output[0]
    self.tensors[node.output[0]] = out


Lowerer.elementwise = _elementwise_v2


# ---------------------------------------------------------------------------
# NNAPI allows at most rank 4 for RESHAPE/TRANSPOSE.  Every rank>4 tensor in
# this model has unit leading dimensions, so dropping them is exact.
def _canon(shape):
    import os as _os
    if _os.environ.get('SWIN_NO_CANON'):
        return tuple(shape)
    shape = list(shape)
    while len(shape) > 4 and shape[0] == 1:
        shape.pop(0)
    while len(shape) > 4:
        drop = None
        for index in range(len(shape) - 1, 0, -1):
            if shape[index] == 1:
                drop = index
                break
        if drop is not None:
            del shape[drop]
        else:
            shape = [shape[0] * shape[1]] + shape[2:]
    return tuple(shape)


def _shape_of_v2(self, name):
    if name in self.shapes:
        return _canon(self.shapes[name])
    if name in self.initializers:
        return tuple(self.initializers[name].shape)
    return _canon(_derive_shape(self, name))


def _op_reshape_v2(self, node):
    data = self.tensor(node.input[0])
    raw = [int(v) for v in self.tensor(node.input[1]).const.ravel()]
    out_shape = self.shape_of(node.output[0])
    if 0 in raw:
        raw = list(out_shape)
    raw_shapes = getattr(self, '_raw', None)
    if raw_shapes is None:
        raw_shapes = self._raw = {}
    raw_shapes[node.output[0]] = tuple(raw)
    raw = list(_canon(raw))
    out = self.define(node.output[0], out_shape)
    self.emit(nnapi.RESHAPE, [self.operand(data), self.cur.i32(raw)], out)


def _op_transpose_v2(self, node):
    data = self.tensor(node.input[0])
    perm = [int(p) for p in self.attrs(node)['perm']]
    in_shape = list(self.shape_of(node.input[0]))
    while len(perm) > len(in_shape):
        if perm[0] != 0:
            raise NotImplementedError('cannot drop leading transpose axis')
        perm = [p - 1 for p in perm[1:]]
    out_shape = self.shape_of(node.output[0])
    out = self.define(node.output[0], out_shape)
    self.emit(nnapi.TRANSPOSE, [self.operand(data), self.cur.i32(perm)], out)


Lowerer.shape_of = _shape_of_v2
Lowerer.op_Reshape = _op_reshape_v2
Lowerer.op_Transpose = _op_transpose_v2


# ---------------------------------------------------------------------------
# Gemm: ONNX weight is [in, out]; NNAPI FC units live in weight.shape[1].
def _op_gemm_v2(self, node):
    print('    GEMM', node.name[-24:], 'out', node.output[0][-20:], 'shape', self.shape_of(node.output[0]), flush=True)
    attrs = self.attrs(node)
    a = self.tensor(node.input[0])
    b = self.tensor(node.input[1])
    out_shape = self.shape_of(node.output[0])
    if attrs.get('transA', 0) or attrs.get('transB', 0):
        raise NotImplementedError('gemm transpose')
    if abs(attrs.get('alpha', 1.0) - 1.0) > 1e-8 or abs(attrs.get('beta', 1.0) - 1.0) > 1e-8:
        raise NotImplementedError('gemm alpha/beta')
    if b.const is None:
        raise NotImplementedError('dynamic gemm weight')
    units = int(b.shape[1])
    weight = np.ascontiguousarray(np.asarray(b.const, dtype=np.float16).T)
    weight_t = self.define(node.input[1] + '/fcW', weight.shape, const=weight)
    rows = int(np.prod(a.shape)) // int(a.shape[-1])
    a2 = self.reshaped(a, (rows, a.shape[-1])) if len(a.shape) != 2 else a
    out2 = self.define(node.output[0] + '/fc', (rows, units))
    bias = self.tensor(node.input[2]) if len(node.input) > 2 else None
    if bias is None:
        bias = self.define(node.output[0] + '/zero', (units,), const=np.zeros(units, np.float16))
    self.emit(nnapi.FULLY_CONNECTED, [self.operand(a2), self.operand(weight_t), self.operand(bias),
                                      self.scalar_i32(0)], out2)
    out = self.define(node.output[0], out_shape)
    self.emit(nnapi.RESHAPE, [self.operand(out2), self.cur.i32(list(out_shape))], out)


def _layernorm_v2(self, data, gamma, beta, eps, shape, name):
    rank = len(shape)
    mean = self.define(name + '/mean', tuple(list(shape[:-1]) + [1]))
    self.emit(nnapi.MEAN, [self.operand(data), self.cur.i32([rank - 1]), self.scalar_i32(1)], mean)
    centered = self.binary(nnapi.SUB, data, mean, shape, name + '/center')
    # Swin's deeper blocks contain outliers above 1000. Squaring those in
    # FP16 overflows even though their channel-mean variance is representable.
    # Exact power-of-two scaling protects the square; restore the variance
    # after reduction so epsilon retains its original value and precision.
    shrink = _minimal(self, shape, 0.125, name + '/shrink')
    reduced_center = self.binary(nnapi.MUL, centered, shrink, shape, name + '/small_center')
    squared = self.binary(nnapi.MUL, reduced_center, reduced_center, shape, name + '/sq')
    small_var = self.define(name + '/small_var', tuple(list(shape[:-1]) + [1]))
    self.emit(nnapi.MEAN, [self.operand(squared), self.cur.i32([rank - 1]), self.scalar_i32(1)], small_var)
    restore = _minimal(self, small_var.shape, 64.0, name + '/restore_variance')
    variance = self.binary(nnapi.MUL, small_var, restore, small_var.shape, name + '/var')
    eps_t = self.define(name + '/eps', mean.shape, const=np.full(mean.shape, eps, np.float16))
    shifted = self.binary(nnapi.ADD, variance, eps_t, variance.shape, name + '/ve')
    inv = self.define(name + '/inv', variance.shape)
    self.emit(nnapi.RSQRT, [self.operand(shifted)], inv)
    normed = self.binary(nnapi.MUL, centered, inv, shape, name + '/normed')
    gamma_shape = self.broadcast_shape(gamma.shape, rank)
    gamma_t = self.align(gamma, gamma_shape)
    scaled = self.binary(nnapi.MUL, normed, gamma_t, shape, name + '/scaled')
    if beta is not None:
        beta_t = self.align(beta, self.broadcast_shape(beta.shape, rank))
        final = self.binary(nnapi.ADD, scaled, beta_t, shape, name + '/final')
    else:
        final = scaled
    return final


def _op_layernorm_v2(self, node):
    attrs = self.attrs(node)
    axis = int(attrs.get('axis', -1))
    eps = float(attrs.get('epsilon', 1e-5))
    data = self.tensor(node.input[0])
    shape = data.shape
    rank = len(shape)
    if axis < 0:
        axis += rank
    if axis != rank - 1:
        raise NotImplementedError('layernorm axis')
    gamma = self.tensor(node.input[1])
    beta = self.tensor(node.input[2]) if len(node.input) > 2 else None
    out = _layernorm_v2(self, data, gamma, beta, eps, shape, node.output[0])
    out.name = node.output[0]
    self.tensors[node.output[0]] = out


def _spatial_mean_staged(self, data, name):
    shape = tuple(data.shape)
    spatial = prod(shape[2:])
    target = tuple(shape[:2]) + (1,) * (len(shape) - 2)
    if spatial <= 2048:
        out = self.define(name, target)
        self.emit(nnapi.MEAN, [self.operand(data), self.cur.i32(list(range(2, len(shape)))), self.scalar_i32(1)], out)
        return out
    # MDLA's long single-axis MEAN loses substantial accuracy (e.g. 38400
    # values). Two short equal-size reductions preserve the full sample set.
    block = next(d for d in range(256, 1, -1) if spatial % d == 0)
    split = self.reshaped(data, shape[:2] + (spatial // block, block), name + '/split')
    partial = self.define(name + '/partial', shape[:2] + (spatial // block, 1))
    self.emit(nnapi.MEAN, [self.operand(split), self.cur.i32([3]), self.scalar_i32(1)], partial)
    reduced = self.define(name + '/reduced', shape[:2] + (1, 1))
    self.emit(nnapi.MEAN, [self.operand(partial), self.cur.i32([2]), self.scalar_i32(1)], reduced)
    return self.reshaped(reduced, target, name)


def _op_instancenorm_v2(self, node):
    attrs = self.attrs(node)
    eps = float(attrs.get('epsilon', 1e-5))
    data = self.tensor(node.input[0])
    scale = self.tensor(node.input[1])
    bias = self.tensor(node.input[2])
    shape = data.shape
    rank = len(shape)
    axes = list(range(2, rank))
    mean = _spatial_mean_staged(self, data, node.output[0] + '/mean')
    centered = self.binary(nnapi.SUB, data, mean, shape, node.output[0] + '/center')
    squared = self.binary(nnapi.MUL, centered, centered, shape, node.output[0] + '/sq')
    variance = _spatial_mean_staged(self, squared, node.output[0] + '/var')
    eps_t = self.define(node.output[0] + '/eps', mean.shape, const=np.full(mean.shape, eps, np.float16))
    shifted = self.binary(nnapi.ADD, variance, eps_t, mean.shape, node.output[0] + '/ve')
    inv = self.define(node.output[0] + '/inv', mean.shape)
    self.emit(nnapi.RSQRT, [self.operand(shifted)], inv)
    normed = self.binary(nnapi.MUL, centered, inv, shape, node.output[0] + '/normed')
    channel = (1, shape[1]) + (1,) * (rank - 2)
    scale_t = self.align(scale, self.broadcast_shape(scale.shape, rank)) if len(scale.shape) > 1 \
        else self.define(node.input[1] + '/s', channel, const=np.asarray(scale.const).reshape(channel))
    scaled = self.binary(nnapi.MUL, normed, scale_t, shape, node.output[0] + '/scaled')
    bias_t = self.align(bias, self.broadcast_shape(bias.shape, rank)) if len(bias.shape) > 1 \
        else self.define(node.input[2] + '/b', channel, const=np.asarray(bias.const).reshape(channel))
    final = self.binary(nnapi.ADD, scaled, bias_t, shape, node.output[0])
    final.name = node.output[0]
    self.tensors[node.output[0]] = final


Lowerer.op_Gemm = _op_gemm_v2
Lowerer.layernorm = _layernorm_v2
Lowerer.op_LayerNormalization = _op_layernorm_v2
Lowerer.op_InstanceNormalization = _op_instancenorm_v2


# ---------------------------------------------------------------------------
# MDLA lowers BATCH_MATMUL by unrolling the batch.  [140,6,144,32] x [140,6,32,144]
# needs >3 minutes to compile, while 14-window chunks compile in ~0.6 s.
def _op_matmul_chunked(self, node):
    a = self.tensor(node.input[0])
    b = self.tensor(node.input[1])
    out_shape = tuple(self.shape_of(node.output[0]))
    if len(out_shape) != 4 or out_shape[0] <= 16:
        out = self.define(node.output[0], out_shape)
        self.emit(nnapi.BATCH_MATMUL, [self.operand(a), self.operand(b),
                                       self.cur.scalar_bool(False), self.cur.scalar_bool(False)], out)
        return
    batch = out_shape[0]
    chunk = 14
    parts = []
    for start in range(0, batch, chunk):
        size = min(chunk, batch - start)
        a_slice = _slice_t(self, a, (start, 0, 0, 0), (size,) + tuple(a.shape[1:]),
                           node.name + f'/as{start}')
        b_slice = _slice_t(self, b, (start, 0, 0, 0), (size,) + tuple(b.shape[1:]),
                           node.name + f'/bs{start}')
        part_shape = (size,) + out_shape[1:]
        part = self.define(node.name + f'/mm{start}', part_shape)
        self.emit(nnapi.BATCH_MATMUL, [self.operand(a_slice), self.operand(b_slice),
                                       self.cur.scalar_bool(False), self.cur.scalar_bool(False)], part)
        parts.append(self.operand(part))
    out = self.define(node.output[0], out_shape)
    self.emit(nnapi.CONCATENATION, parts + [self.scalar_i32(0)], out)


Lowerer.op_MatMul = _op_matmul_chunked


# ---------------------------------------------------------------------------
# Bilinear GridSample (align_corners=0, zeros padding) == separable triangular
# weighting:  out[n,c,l] = sum_h sum_w x[n,c,h,w] * wy[n,l,h] * wx[n,l,w].
def _slice_t(self, tensor, starts, sizes, name):
    out = self.define(name, tuple(sizes))
    self.emit(nnapi.SLICE, [self.operand(tensor), self.cur.i32(list(starts)), self.cur.i32(list(sizes))], out)
    return out


def _op_gridsample(self, node):
    attrs = self.attrs(node)
    if int(attrs.get('align_corners', 0)) != 0:
        raise NotImplementedError('gridsample align_corners')
    if attrs.get('padding_mode', b'zeros') != b'zeros':
        raise NotImplementedError('gridsample padding')
    x = self.tensor(node.input[0])
    grid = self.tensor(node.input[1])
    out_shape = self.shape_of(node.output[0])
    n, c, h, w = x.shape
    ho, wo = out_shape[2], out_shape[3]
    total = ho * wo
    g = self.reshaped(grid, (n, total, 2), node.name + '/flatgrid') if len(grid.shape) != 3 else grid
    gx = _slice_t(self, g, (0, 0, 0), (n, total, 1), node.name + '/gx')
    gy = _slice_t(self, g, (0, 0, 1), (n, total, 1), node.name + '/gy')

    def pixel(coord, length, tag):
        one = _minimal(self, coord.shape, 1.0, node.name + tag + '/one')
        half = _minimal(self, coord.shape, length / 2.0, node.name + tag + '/scale')
        shift = _minimal(self, coord.shape, -0.5, node.name + tag + '/shift')
        plus = self.binary(nnapi.ADD, coord, one, coord.shape, node.name + tag + '/plus')
        scaled = self.binary(nnapi.MUL, plus, half, coord.shape, node.name + tag + '/scaled')
        return self.binary(nnapi.ADD, scaled, shift, coord.shape, node.name + tag + '/pixel')

    px = pixel(gx, w, '/px')
    py = pixel(gy, h, '/py')

    def weights(p, length, tag):
        idx = self.define(node.name + tag + '/idx', (1, 1, length),
                          const=np.arange(length, dtype=np.float16).reshape(1, 1, length))
        delta = self.binary(nnapi.SUB, p, idx, (n, total, length), node.name + tag + '/delta')
        absolute = self.define(node.name + tag + '/abs', (n, total, length))
        self.emit(nnapi.ABS, [self.operand(delta)], absolute)
        one = _minimal(self, absolute.shape, 1.0, node.name + tag + '/one')
        negative = self.define(node.name + tag + '/neg', (n, total, length))
        minus_one = _minimal(self, absolute.shape, -1.0, node.name + tag + '/m1')
        self.emit(nnapi.MUL, [self.operand(absolute), self.operand(minus_one),
                              self.scalar_i32(0)], negative)
        shifted = self.binary(nnapi.ADD, negative, one, (n, total, length), node.name + tag + '/shift')
        out = self.define(node.name + tag + '/relu', (n, total, length))
        self.emit(nnapi.RELU, [self.operand(shifted)], out)
        return out

    wx = weights(px, w, '/wx')
    wy = weights(py, h, '/wy')
    for tensor in (px, py, gx, gy, wx, wy):
        self.last_use[tensor.name] = self.position
    self.flush(self.position)
    wxT = self.transposed(wx, (0, 2, 1), node.name + '/wxT')
    wyT = self.transposed(wy, (0, 2, 1), node.name + '/wyT')
    self.last_use[wxT.name] = self.position
    self.last_use[wyT.name] = self.position

    step = 4 if h >= 48 else (8 if h >= 24 else h)
    accumulated = None
    for start in range(0, h, step):
        size = min(step, h - start)
        xs = _slice_t(self, x, (0, 0, start, 0), (n, c, size, w), node.name + f'/xs{start}')
        xr = self.reshaped(xs, (n, c * size, w), node.name + f'/xr{start}')
        part = self.define(node.name + f'/part{start}', (n, c * size, total))
        self.emit(nnapi.BATCH_MATMUL, [self.operand(xr), self.operand(wxT),
                                       self.cur.scalar_bool(False), self.cur.scalar_bool(False)], part)
        part4 = self.reshaped(part, (n, c, size, total), node.name + f'/part4{start}')
        wys = _slice_t(self, wyT, (0, start, 0), (n, size, total), node.name + f'/wys{start}')
        wys4 = self.reshaped(wys, (n, 1, size, total), node.name + f'/wys4{start}')
        weighted = self.binary(nnapi.MUL, part4, wys4, (n, c, size, total), node.name + f'/weighted{start}')
        summed = self.define(node.name + f'/sum{start}', (n, c, 1, total))
        self.emit(nnapi.REDUCE_SUM, [self.operand(weighted), self.cur.i32([2]),
                                     self.cur.scalar_bool(True)], summed)
        if accumulated is None:
            accumulated = summed
        else:
            accumulated = self.binary(nnapi.ADD, accumulated, summed, (n, c, 1, total),
                                      node.name + f'/acc{start}')
        self.last_use[accumulated.name] = self.position
        self.flush(self.position)
    out = self.define(node.output[0], (n, c, ho, wo))
    self.emit(nnapi.RESHAPE, [self.operand(accumulated), self.cur.i32([n, c, ho, wo])], out)


Lowerer.op_GridSample = _op_gridsample


# ---------------------------------------------------------------------------
# The MDLA compiler stalls on large tensors combined with other ops.  Anything
# producing >= 4M elements gets its own subgraph so each compile stays small.
HEAVY_OPS = {'Transpose', 'Reshape', 'Softmax', 'MatMul', 'Concat', 'Add', 'Mul', 'Sub', 'Div'}


def _is_heavy(self, node):
    if node.op_type not in HEAVY_OPS:
        return False
    for name in node.output:
        try:
            shape = self.shape_of(name)
        except KeyError:
            continue
        count = 1
        for d in shape:
            count *= d
        if count >= 4000000:
            return True
    return False


def _run_v2(self):
    self.graph_outputs = set(o.name for o in self.graph.output) | self.keep
    self.start_subgraph()
    for value in self.graph.input:
        tensor = Tensor(value.name, F16, tuple(self.shape_of(value.name)))
        tensor.buf = self.plan.buffer(sanitize(value.name), F16, tensor.shape)
        tensor.is_input = True
        self.tensors[value.name] = tensor
    for name, tensor in self.initializers.items():
        if name in self.tensors:
            continue
        self.tensors[name] = Tensor(name, self.dtypes.get(name, 10),
                                    tuple(self.shape_of(name) if name in self.shapes else tensor.shape),
                                    const=tensor)
    for position, node in enumerate(self.nodes):
        heavy = _is_heavy(self, node)
        if heavy:
            self.flush(position)
        self.maybe_flush(position)
        handler = getattr(self, 'op_' + node.op_type, None)
        if handler is None:
            raise NotImplementedError(f'{node.op_type} ({node.name})')
        handler(node)
        if heavy:
            self.flush(position + 1)
    self.flush(len(self.nodes))
    self.plan.subs = [s for s in self.plan.subs if s.ops]
    for name in sorted(self.keep):
        tensor = self.tensors[name]
        if tensor.buf is None:
            raise RuntimeError(f'kept tensor not materialised: {name}')
        self.plan.results.append(tensor.buf)
    for out in (self.graph.output if self.limit is None else []):
        tensor = self.tensors[out.name]
        if tensor.buf is None:
            raise RuntimeError(f'graph output not materialised: {out.name}')
        self.plan.results.append(tensor.buf)
    return self.plan


Lowerer.run = _run_v2


# ---------------------------------------------------------------------------
# Preserve the caller's tap order in plan.results.
_keep_order = True


def _run_taps(self):
    self.keep = list(self.keep)
    self.graph_outputs = set(o.name for o in self.graph.output) | set(self.keep)
    self.start_subgraph()
    for value in self.graph.input:
        tensor = Tensor(value.name, F16, tuple(self.shape_of(value.name)))
        tensor.buf = self.plan.buffer(sanitize(value.name), F16, tensor.shape)
        tensor.is_input = True
        self.tensors[value.name] = tensor
    for name, tensor in self.initializers.items():
        if name in self.tensors:
            continue
        self.tensors[name] = Tensor(name, self.dtypes.get(name, 10),
                                    tuple(self.shape_of(name) if name in self.shapes else tensor.shape),
                                    const=tensor)
    for position, node in enumerate(self.nodes):
        heavy = _is_heavy(self, node)
        if heavy:
            self.flush(position)
        self.maybe_flush(position)
        handler = getattr(self, 'op_' + node.op_type, None)
        if handler is None:
            raise NotImplementedError(f'{node.op_type} ({node.name})')
        handler(node)
        if heavy:
            self.flush(position + 1)
    self.flush(len(self.nodes))
    self.plan.subs = [s for s in self.plan.subs if s.ops]
    for name in self.keep:
        tensor = self.tensors[name]
        if tensor.buf is None:
            raise RuntimeError(f'kept tensor not materialised: {name}')
        self.plan.results.append(tensor.buf)
    for out in (self.graph.output if self.limit is None else []):
        tensor = self.tensors[out.name]
        if tensor.buf is None:
            raise RuntimeError(f'graph output not materialised: {out.name}')
        self.plan.results.append(tensor.buf)
    return self.plan


Lowerer.run = _run_taps


# ---------------------------------------------------------------------------
# A single [19200,3072] FC output is 118 MB and stalls the MDLA compiler, so
# both the MLP projection and the GELU block are split along the token axis.
def _chunk_axis(shape):
    if len(shape) <= 2:
        return 0
    best = 0
    for axis in range(len(shape) - 1):
        if shape[axis] > shape[best]:
            best = axis
    return best


def _op_gemm_chunked(self, node):
    a = self.tensor(node.input[0])
    b = self.tensor(node.input[1])
    out_shape = tuple(self.shape_of(node.output[0]))
    if b.const is None:
        raise NotImplementedError('dynamic gemm weight')
    units = int(b.shape[1])
    rows = int(np.prod(a.shape)) // int(a.shape[-1])
    if rows * units < 2000000:
        return _op_gemm_v2(self, node)
    weight = np.ascontiguousarray(np.asarray(b.const, dtype=np.float16).T)
    weight_t = self.define(node.input[1] + '/fcW', weight.shape, const=weight)
    bias = self.tensor(node.input[2]) if len(node.input) > 2 else None
    if bias is None:
        bias = self.define(node.output[0] + '/zero', (units,), const=np.zeros(units, np.float16))
    a2 = self.reshaped(a, (rows, a.shape[-1])) if len(a.shape) != 2 else a
    step = max(1, 2000000 // units)
    parts = []
    for start in range(0, rows, step):
        size = min(step, rows - start)
        a_slice = _slice_t(self, a2, (start, 0), (size, a.shape[-1]), node.name + f'/gs{start}')
        part = self.define(node.name + f'/gf{start}', (size, units))
        self.emit(nnapi.FULLY_CONNECTED, [self.operand(a_slice), self.operand(weight_t),
                                          self.operand(bias), self.scalar_i32(0)], part)
        parts.append(self.operand(part))
    joined = self.define(node.name + '/gjoin', (rows, units))
    self.emit(nnapi.CONCATENATION, parts + [self.scalar_i32(0)], joined)
    out = self.define(node.output[0], out_shape)
    self.emit(nnapi.RESHAPE, [self.operand(joined), self.cur.i32(list(out_shape))], out)


def _erf_chunked(self, data, shape, name):
    count = 1
    for d in shape:
        count *= d
    if count < 4000000:
        return _erf_v2(self, data, shape, name)
    axis = _chunk_axis(shape)
    step = max(1, 2000000 // (count // shape[axis]))
    parts = []
    for start in range(0, shape[axis], step):
        size = min(step, shape[axis] - start)
        chunk_shape = list(shape)
        chunk_shape[axis] = size
        starts = [0] * len(shape)
        starts[axis] = start
        piece = _slice_t(self, data, tuple(starts), tuple(chunk_shape), name + f'/es{start}')
        piece_out = _erf_v2(self, piece, tuple(chunk_shape), name + f'/e{start}')
        parts.append(self.operand(piece_out))
    out = self.define(name, tuple(shape))
    self.emit(nnapi.CONCATENATION, parts + [self.scalar_i32(axis)], out)
    return out


def _op_erf_chunked(self, node):
    data = self.tensor(node.input[0])
    shape = tuple(self.shape_of(node.output[0]))
    out = _erf_chunked(self, data, shape, node.output[0])
    out.name = node.output[0]
    self.tensors[node.output[0]] = out


Lowerer.op_Gemm = _op_gemm_chunked
Lowerer.erf = _erf_chunked
Lowerer.op_Erf = _op_erf_chunked


# ---------------------------------------------------------------------------
# The phone has ~11 GB RAM shared with everything else, so host buffers for
# materialised tensors are recycled once their last consumer has executed.
def _flush_pool(self, node_pos):
    print('FLUSH', node_pos, 'tensors', len(self.tensors), 'ops', len(self.cur.ops) if self.cur else -1, flush=True)
    if self.cur is None or not self.cur.ops:
        return
    pool = getattr(self, '_pool', None)
    if pool is None:
        pool = self._pool = {}
    for name, tensor in list(self.tensors.items()):
        if tensor.buf is None or tensor.const is not None or tensor.is_input:
            continue
        if name in self.graph_outputs:
            continue
        if False and name in self.last_use:
            pool.setdefault(prod(tensor.shape) * 2, []).append(tensor.buf)
            tensor.buf = None
    materialised = []
    for name, tensor in list(self.tensors.items()):
        if tensor.const is not None or tensor.buf is not None or tensor.local is None:
            continue
        if tensor.local_sub != self.cur_index:
            continue
        if self.last_use.get(name, -1) < node_pos and name not in self.graph_outputs:
            continue
        size = prod(tensor.shape) * 2
        free = None
        if free:
            buffer = free.pop()
        else:
            buffer = self.plan.buffer(f'{sanitize(name)}_{self.unique}', F16, tensor.shape)
            self.unique += 1
        values = self.cur.values[tensor.local]
        self.cur.values[tensor.local] = (values[0], values[1], values[2], buffer)
        self.cur.outputs.append(tensor.local)
        tensor.buf = buffer
        tensor.local = None
        materialised.append(name)
    if self.verbose:
        print(f'  flush at node {node_pos}: {len(self.cur.ops)} ops, {len(materialised)} buffers')
    self.start_subgraph()


Lowerer.flush = _flush_pool


# ---------------------------------------------------------------------------
# Chunked lowering with an explicit subgraph boundary after each chunk: a
# single model holding ten 6 MB FCs stalled the MDLA compiler for >20 min.
def _run_v3(self):
    self.keep = list(self.keep)
    self.graph_outputs = set(o.name for o in self.graph.output) | set(self.keep)
    self.start_subgraph()
    for value in self.graph.input:
        tensor = Tensor(value.name, F16, tuple(self.shape_of(value.name)))
        tensor.buf = self.plan.buffer(sanitize(value.name), F16, tensor.shape)
        tensor.is_input = True
        self.tensors[value.name] = tensor
    for name, tensor in self.initializers.items():
        if name in self.tensors:
            continue
        self.tensors[name] = Tensor(name, self.dtypes.get(name, 10),
                                    tuple(self.shape_of(name) if name in self.shapes else tensor.shape),
                                    const=tensor)
    for position, node in enumerate(self.nodes):
        self.position = position
        heavy = _is_heavy(self, node)
        if heavy:
            self.flush(position)
        self.maybe_flush(position)
        handler = getattr(self, 'op_' + node.op_type, None)
        if handler is None:
            raise NotImplementedError(f'{node.op_type} ({node.name})')
        handler(node)
        if heavy:
            self.flush(position + 1)
    self.flush(len(self.nodes))
    self.plan.subs = [s for s in self.plan.subs if s.ops]
    for name in self.keep:
        tensor = self.tensors[name]
        if tensor.buf is None:
            raise RuntimeError(f'kept tensor not materialised: {name}')
        self.plan.results.append(tensor.buf)
    for out in (self.graph.output if self.limit is None else []):
        tensor = self.tensors[out.name]
        if tensor.buf is None:
            raise RuntimeError(f'graph output not materialised: {out.name}')
        self.plan.results.append(tensor.buf)
    return self.plan


def _op_gemm_chunked_v2(self, node):
    a = self.tensor(node.input[0])
    b = self.tensor(node.input[1])
    out_shape = tuple(self.shape_of(node.output[0]))
    if b.const is None:
        raise NotImplementedError('dynamic gemm weight')
    units = int(b.shape[1])
    rows = int(np.prod(a.shape)) // int(a.shape[-1])
    if rows * units < 2000000:
        return _op_gemm_v2(self, node)
    weight = np.ascontiguousarray(np.asarray(b.const, dtype=np.float16).T)
    weight_t = self.define(node.input[1] + '/fcW', weight.shape, const=weight)
    bias = self.tensor(node.input[2]) if len(node.input) > 2 else None
    if bias is None:
        bias = self.define(node.output[0] + '/zero', (units,), const=np.zeros(units, np.float16))
    a2 = self.reshaped(a, (rows, a.shape[-1])) if len(a.shape) != 2 else a
    step = max(1, 2000000 // units)
    parts = []
    for start in range(0, rows, step):
        size = min(step, rows - start)
        a_slice = _slice_t(self, a2, (start, 0), (size, a.shape[-1]), node.name + f'/gs{start}')
        part = self.define(node.name + f'/gf{start}', (size, units))
        self.emit(nnapi.FULLY_CONNECTED, [self.operand(a_slice), self.operand(weight_t),
                                          self.operand(bias), self.scalar_i32(0)], part)
        self.last_use[part.name] = self.position
        parts.append(part)
        self.flush(self.position)
    joined = self.define(node.name + '/gjoin', (rows, units))
    joined_index = self.cur.val(F16, (rows, units))
    self.cur.op(nnapi.CONCATENATION, [self.operand(t) for t in parts] + [self.scalar_i32(0)], joined_index)
    joined.local = joined_index
    joined.local_sub = self.cur_index
    out = self.define(node.output[0], out_shape)
    self.emit(nnapi.RESHAPE, [self.operand(joined), self.cur.i32(list(out_shape))], out)


def _erf_chunked_v2(self, data, shape, name):
    if getattr(self, '_in_region', False):
        return _erf_v2(self, data, shape, name)
    count = 1
    for d in shape:
        count *= d
    if count < 4000000:
        return _erf_v2(self, data, shape, name)
    axis = _chunk_axis(shape)
    step = max(1, 2000000 // (count // shape[axis]))
    parts = []
    for start in range(0, shape[axis], step):
        size = min(step, shape[axis] - start)
        chunk_shape = list(shape)
        chunk_shape[axis] = size
        starts = [0] * len(shape)
        starts[axis] = start
        piece = _slice_t(self, data, tuple(starts), tuple(chunk_shape), name + f'/es{start}')
        piece_out = _erf_v2(self, piece, tuple(chunk_shape), name + f'/e{start}')
        self.last_use[piece_out.name] = self.position
        parts.append(piece_out)
        self.flush(self.position)
    out = self.define(name, tuple(shape))
    out_index = self.cur.val(F16, tuple(shape))
    self.cur.op(nnapi.CONCATENATION, [self.operand(t) for t in parts] + [self.scalar_i32(axis)], out_index)
    out.local = out_index
    out.local_sub = self.cur_index
    return out


def _op_erf_chunked_v2(self, node):
    data = self.tensor(node.input[0])
    shape = tuple(self.shape_of(node.output[0]))
    out = _erf_chunked_v2(self, data, shape, node.output[0])
    out.name = node.output[0]
    self.tensors[node.output[0]] = out


Lowerer.run = _run_v3
Lowerer.op_Gemm = _op_gemm_chunked_v2
Lowerer.erf = _erf_chunked_v2
Lowerer.op_Erf = _op_erf_chunked_v2


# ---------------------------------------------------------------------------
# fc1([19200,192]->[19200,768]) -> GELU -> fc2 produces a 118 MB activation and
# a 118 MB concat; the MDLA compiler stalls on both.  The whole region is run
# per token slice instead, so no oversized tensor ever exists.
ELEMENTWISE_REGION = {'Div', 'Mul', 'Add', 'Sub', 'Erf', 'Sigmoid', 'Tanh', 'Relu', 'Exp', 'Neg',
                     'Reshape', 'Squeeze', 'Unsqueeze'}


def _region_map(self):
    regions = {}
    index = 0
    while index < len(self.nodes):
        node = self.nodes[index]
        if node.op_type == 'Gemm':
            import os
            try:
                out_shape = self.shape_of(node.output[0])
            except KeyError:
                out_shape = None
            big = out_shape is not None and prod(out_shape) >= int(os.environ.get('SWIN_REGION_MIN', '8000000'))
            if big and index + 1 < len(self.nodes):
                produced = set(node.output)
                end = index + 1
                while end < len(self.nodes):
                    candidate = self.nodes[end]
                    if candidate.op_type not in ELEMENTWISE_REGION:
                        break
                    produced.update(candidate.output)
                    end += 1
                if end < len(self.nodes) and self.nodes[end].op_type == 'Gemm' and \
                        all(self.last_use.get(nm, 0) <= end for nm in produced):
                    regions[index] = (end, self.nodes[end].output[0])
                    index = end + 1
                    continue
        index += 1
    return regions


def _lower_region(self, start, end, final_name, chunk_rows=None):
    import os
    if chunk_rows is None:
        chunk_rows = int(os.environ.get('SWIN_CHUNK_ROWS', '2048'))
    self._in_region = True
    first = self.nodes[start]
    final_shape_full = tuple(self.shape_of(final_name))
    saved_input_tensor = self.tensors[first.input[0]]
    produced_names = set()
    for region_index in range(start, end + 1):
        produced_names.update(self.nodes[region_index].output)
    saved_region_tensors = {nm: self.tensors.get(nm) for nm in produced_names}
    source = self.tensor(first.input[0])
    shape = list(source.shape)
    axis = 1 if len(shape) >= 3 and shape[1] >= 256 else 0
    total = shape[axis]
    names = set()
    for node in self.nodes[start:end + 1]:
        names.update(node.output)
    parts = []
    extra = {n: [] for n in names if n in self.graph_outputs}
    saved_shapes = {n: self.shapes.get(n) for n in names}
    for offset in range(0, total, chunk_rows):
        print('CHUNK', offset, 'total', total, 'step', chunk_rows, 'axis', axis, 'shape', shape, flush=True)
        size = min(chunk_rows, total - offset)
        chunk_shape = list(shape)
        chunk_shape[axis] = size
        starts = [0] * len(shape)
        starts[axis] = offset
        chunk = _slice_t(self, source, tuple(starts), tuple(chunk_shape),
                         f'{final_name}/chunk{offset}')
        for nm, shp in saved_shapes.items():
            if shp is None:
                continue
            adjusted = list(shp)
            token_axis = None
            for dim_index, dim_value in enumerate(adjusted):
                if dim_value == total:
                    token_axis = dim_index
                    break
            if token_axis is None and len(adjusted) > axis:
                token_axis = axis
            if token_axis is not None:
                adjusted[token_axis] = size
            self.shapes[nm] = tuple(adjusted)
        self.tensors[first.input[0]] = chunk
        idx = start
        while idx <= end:
            fused = _gelu_match(self, idx)
            if fused is not None and idx + 4 <= end:
                gelu_out, gelu_x, _ = fused
                _emit_gelu(self, idx, gelu_out, gelu_x)
                idx += 5
                continue
            node = self.nodes[idx]
            print('  NODE', idx, node.op_type, node.output[0][-30:], flush=True)
            handler = getattr(self, 'op_' + node.op_type)
            handler(node)
            self.maybe_flush(self.position)
            idx += 1
        piece = self.tensors[final_name]
        print('  PIECE', piece.name[-30:], piece.shape, flush=True)
        self.last_use[piece.name] = self.position
        for tap in extra:
            self.last_use[self.tensors[tap].name] = self.position
            extra[tap].append(self.tensors[tap])
        parts.append(piece)
        self.flush(self.position)
    out_shape = list(final_shape_full)
    axis_out = axis
    for dim_index, dim_value in enumerate(out_shape):
        if dim_value == total:
            axis_out = dim_index
            break
    out_shape = tuple(out_shape)
    out = self.define(final_name, out_shape)
    index = self.cur.val(F16, out_shape)
    self.cur.op(nnapi.CONCATENATION, [self.operand(t) for t in parts] + [self.scalar_i32(axis_out)], index)
    out.local = index
    out.local_sub = self.cur_index
    for nm, shp in saved_shapes.items():
        if shp is not None:
            self.shapes[nm] = shp
    for tap, pieces in extra.items():
        piece_shape = list(pieces[0].shape)
        tap_axis = axis
        for dim_index, dim_value in enumerate(piece_shape):
            if dim_value == size or dim_value == pieces[0].shape[axis]:
                tap_axis = dim_index
                break
        if len(piece_shape) > 1 and piece_shape[0] == 1:
            tap_axis = 1
        piece_shape[tap_axis] = total
        tap_shape = tuple(piece_shape)
        joined = self.define(tap, tap_shape)
        joined_index = self.cur.val(F16, tap_shape)
        self.cur.op(nnapi.CONCATENATION, [self.operand(t) for t in pieces] + [self.scalar_i32(tap_axis)],
                    joined_index)
        joined.local = joined_index
        joined.local_sub = self.cur_index
        self.tensors[tap] = joined
    self.tensors[final_name] = out
    self.tensors[first.input[0]] = saved_input_tensor
    for region_name, saved_tensor in saved_region_tensors.items():
        if saved_tensor is None:
            self.tensors.pop(region_name, None)
        else:
            self.tensors[region_name] = saved_tensor
    self._in_region = False
    return out


def _run_v4(self):
    self.keep = list(self.keep)
    self.graph_outputs = set(o.name for o in self.graph.output) | set(self.keep)
    self.start_subgraph()
    for value in self.graph.input:
        tensor = Tensor(value.name, F16, tuple(self.shape_of(value.name)))
        tensor.buf = self.plan.buffer(sanitize(value.name), F16, tensor.shape)
        tensor.is_input = True
        self.tensors[value.name] = tensor
    for name, tensor in self.initializers.items():
        if name in self.tensors:
            continue
        self.tensors[name] = Tensor(name, self.dtypes.get(name, 10),
                                    tuple(self.shape_of(name) if name in self.shapes else tensor.shape),
                                    const=tensor)
    regions = _region_map(self)
    position = 0
    while position < len(self.nodes):
        if position in regions:
            end, final_name = regions[position]
            self.position = position
            self.flush(position)
            region_out = _lower_region(self, position, end, final_name)
            self.tensors[final_name] = region_out
            position = end + 1
            continue
        node = self.nodes[position]
        self.position = position
        heavy = _is_heavy(self, node)
        if heavy:
            self.flush(position)
        self.maybe_flush(position)
        handler = getattr(self, 'op_' + node.op_type, None)
        if handler is None:
            raise NotImplementedError(f'{node.op_type} ({node.name})')
        handler(node)
        if heavy:
            self.flush(position + 1)
        position += 1
    self.flush(len(self.nodes))
    self.plan.subs = [s for s in self.plan.subs if s.ops]
    for name in self.keep:
        tensor = self.tensors[name]
        if tensor.buf is None:
            raise RuntimeError(f'kept tensor not materialised: {name}')
        self.plan.results.append(tensor.buf)
    for out in (self.graph.output if self.limit is None else []):
        tensor = self.tensors[out.name]
        if tensor.buf is None:
            raise RuntimeError(f'graph output not materialised: {out.name}')
        self.plan.results.append(tensor.buf)
    return self.plan


Lowerer.run = _run_v4


# ---------------------------------------------------------------------------
# Swin's MLP activation is exactly GELU.  The ONNX form costs eight ops on a
# 118 MB tensor and stalls the MDLA compiler; x*sigmoid(1.702x) is two ops and
# matches within 3e-4 in FP32.
def _const_value(self, name):
    tensor = self.tensors.get(name)
    if tensor is None or tensor.const is None:
        return None
    try:
        flat = np.asarray(tensor.const, dtype=np.float32).ravel()
    except Exception:
        return None
    if flat.size != 1:
        return None
    return float(flat[0])


def _gelu_match(self, position):
    nodes = self.nodes
    if position + 4 >= len(nodes):
        return None
    div, erf, add, mul, mul2 = nodes[position:position + 5]
    if div.op_type != 'Div' or erf.op_type != 'Erf' or add.op_type != 'Add':
        return None
    if mul.op_type != 'Mul' or mul2.op_type != 'Mul':
        return None
    if list(div.output) != list(erf.input):
        return None
    x = div.input[0]
    divisor = _const_value(self, div.input[1]) if div.input[1] != x else None
    if divisor is None or abs(divisor - 1.4142135) > 1e-3:
        return None
    if erf.output[0] not in add.input:
        return None
    other = add.input[0] if add.input[1] == erf.output[0] else add.input[1]
    if _const_value(self, other) != 1.0:
        return None
    if x not in mul.input or add.output[0] not in mul.input:
        return None
    if _const_value(self, mul2.input[1] if mul2.input[0] == mul.output[0] else mul2.input[0]) != 0.5:
        return None
    return mul2.output[0], x, set(div.output) | set(erf.output) | set(add.output) | set(mul.output)


def _emit_gelu(self, position, final_name, x_name):
    source = self.tensor(x_name)
    shape = tuple(self.shape_of(final_name))
    coefficient = _minimal(self, shape, 0.044715, final_name + '/coefficient')
    scaled_x = self.binary(nnapi.MUL, source, coefficient, shape, final_name + '/scaled_x')
    square = self.binary(nnapi.MUL, scaled_x, source, shape, final_name + '/square')
    correction = self.binary(nnapi.MUL, square, source, shape, final_name + '/correction')
    inner = self.binary(nnapi.ADD, source, correction, shape, final_name + '/inner')
    scale = _minimal(self, shape, 1.5957691216057308, final_name + '/gscale')
    scaled = self.binary(nnapi.MUL, inner, scale, shape, final_name + '/gx')
    gate = self.define(final_name + '/gsig', shape)
    self.emit(nnapi.LOGISTIC, [self.operand(scaled)], gate)
    out = self.binary(nnapi.MUL, source, gate, shape, final_name)
    out.name = final_name
    self.tensors[final_name] = out
    return out


def _run_v5(self):
    self.keep = list(self.keep)
    self.graph_outputs = set(o.name for o in self.graph.output) | set(self.keep)
    self.start_subgraph()
    for value in self.graph.input:
        tensor = Tensor(value.name, F16, tuple(self.shape_of(value.name)))
        tensor.buf = self.plan.buffer(sanitize(value.name), F16, tensor.shape)
        tensor.is_input = True
        self.tensors[value.name] = tensor
    for name, tensor in self.initializers.items():
        if name in self.tensors:
            continue
        self.tensors[name] = Tensor(name, self.dtypes.get(name, 10),
                                    tuple(self.shape_of(name) if name in self.shapes else tensor.shape),
                                    const=tensor)
    regions = _region_map(self)
    position = 0
    while position < len(self.nodes):
        if position in regions:
            end, final_name = regions[position]
            self.position = position
            self.flush(position)
            region_out = _lower_region(self, position, end, final_name)
            self.tensors[final_name] = region_out
            position = end + 1
            continue
        gelu = _gelu_match(self, position)
        if gelu is not None:
            final_name, x_name, _ = gelu
            self.position = position
            _emit_gelu(self, position, final_name, x_name)
            position += 5
            continue
        node = self.nodes[position]
        self.position = position
        heavy = _is_heavy(self, node)
        if heavy:
            self.flush(position)
        self.maybe_flush(position)
        handler = getattr(self, 'op_' + node.op_type, None)
        if handler is None:
            raise NotImplementedError(f'{node.op_type} ({node.name})')
        handler(node)
        if heavy:
            self.flush(position + 1)
        position += 1
    self.flush(len(self.nodes))
    self.plan.subs = [s for s in self.plan.subs if s.ops]
    for name in self.keep:
        tensor = self.tensors[name]
        if tensor.buf is None:
            raise RuntimeError(f'kept tensor not materialised: {name}')
        self.plan.results.append(tensor.buf)
    for out in (self.graph.output if self.limit is None else []):
        tensor = self.tensors[out.name]
        if tensor.buf is None:
            raise RuntimeError(f'graph output not materialised: {out.name}')
        self.plan.results.append(tensor.buf)
    return self.plan


Lowerer.run = _run_v5


# ---------------------------------------------------------------------------
_derive_extra = True


def _derive_shape_more(self, name):
    cache = getattr(self, '_derived', None)
    if cache is None:
        cache = self._derived = {}
    node = _producer_map(self).get(name)
    if node is None:
        raise KeyError('no shape for ' + name)
    op = node.op_type
    if op == 'Gemm':
        weight = self.tensor(node.input[1])
        source = self.shape_of(node.input[0])
        cache[name] = tuple(list(source[:-1]) + [weight.shape[1]])
        return cache[name]
    if op in ('Add', 'Sub', 'Mul', 'Div', 'Pow'):
        a = self.shape_of(node.input[0])
        b = self.shape_of(node.input[1])
        rank = max(len(a), len(b))
        a = (1,) * (rank - len(a)) + tuple(a)
        b = (1,) * (rank - len(b)) + tuple(b)
        cache[name] = tuple(max(x, y) for x, y in zip(a, b))
        return cache[name]
    if op == 'Erf' or op == 'Tanh' or op == 'Relu' or op == 'Sigmoid':
        cache[name] = tuple(self.shape_of(node.input[0]))
        return cache[name]
    if op == 'LayerNormalization':
        cache[name] = tuple(self.shape_of(node.input[0]))
        return cache[name]
    raise KeyError('cannot derive shape for ' + name + ' (' + op + ')')


_old_derive = _derive_shape


def _derive_shape(self, name):
    try:
        return _old_derive(self, name)
    except KeyError:
        return _derive_shape_more(self, name)


Lowerer._derive_shape = _derive_shape


# ---------------------------------------------------------------------------
def _gemm_weight(self, node, b):
    attrs = self.attrs(node)
    if attrs.get('transA', 0):
        raise NotImplementedError('gemm transA')
    weight = np.asarray(b.const, dtype=np.float16)
    if not attrs.get('transB', 0):
        weight = weight.T
    return np.ascontiguousarray(weight)


def _gemm_units(self, node, b):
    attrs = self.attrs(node)
    return int(b.shape[0] if attrs.get('transB', 0) else b.shape[1])


def _op_gemm_v3(self, node):
    a = self.tensor(node.input[0])
    b = self.tensor(node.input[1])
    out_shape = tuple(self.shape_of(node.output[0]))
    if b.const is None:
        raise NotImplementedError('dynamic gemm weight')
    attrs = self.attrs(node)
    if abs(attrs.get('alpha', 1.0) - 1.0) > 1e-8 or abs(attrs.get('beta', 1.0) - 1.0) > 1e-8:
        raise NotImplementedError('gemm alpha/beta')
    units = _gemm_units(self, node, b)
    weight = _gemm_weight(self, node, b)
    weight_t = self.define(node.input[1] + '/gw', weight.shape, const=weight)
    rows = int(np.prod(a.shape)) // int(a.shape[-1])
    a2 = self.reshaped(a, (rows, a.shape[-1])) if len(a.shape) != 2 else a
    out2 = self.define(node.output[0] + '/fc', (rows, units))
    bias = self.tensor(node.input[2]) if len(node.input) > 2 else None
    if bias is None:
        bias = self.define(node.output[0] + '/zero', (units,), const=np.zeros(units, np.float16))
    self.emit(nnapi.FULLY_CONNECTED, [self.operand(a2), self.operand(weight_t), self.operand(bias),
                                      self.scalar_i32(0)], out2)
    out = self.define(node.output[0], out_shape)
    self.emit(nnapi.RESHAPE, [self.operand(out2), self.cur.i32(list(out_shape))], out)


GEMM_ELEMENT_LIMIT = 2000000


def _op_gemm_chunked_v3(self, node):
    a = self.tensor(node.input[0])
    b = self.tensor(node.input[1])
    if b.const is None:
        raise NotImplementedError('dynamic gemm weight')
    units = _gemm_units(self, node, b)
    rows = int(np.prod(a.shape)) // int(a.shape[-1])
    if rows * units < GEMM_ELEMENT_LIMIT:
        return _op_gemm_v3(self, node)
    out_shape = tuple(self.shape_of(node.output[0]))
    weight = _gemm_weight(self, node, b)
    weight_t = self.define(node.input[1] + '/gw', weight.shape, const=weight)
    bias = self.tensor(node.input[2]) if len(node.input) > 2 else None
    if bias is None:
        bias = self.define(node.output[0] + '/zero', (units,), const=np.zeros(units, np.float16))
    a2 = self.reshaped(a, (rows, a.shape[-1])) if len(a.shape) != 2 else a
    step = max(1, GEMM_ELEMENT_LIMIT // units)
    parts = []
    for start in range(0, rows, step):
        size = min(step, rows - start)
        a_slice = _slice_t(self, a2, (start, 0), (size, a.shape[-1]), node.name + f'/gs{start}')
        part = self.define(node.name + f'/gf{start}', (size, units))
        self.emit(nnapi.FULLY_CONNECTED, [self.operand(a_slice), self.operand(weight_t),
                                          self.operand(bias), self.scalar_i32(0)], part)
        self.last_use[part.name] = self.position
        parts.append(part)
        self.flush(self.position)
    joined = self.define(node.name + '/gjoin', (rows, units))
    joined_index = self.cur.val(F16, (rows, units))
    self.cur.op(nnapi.CONCATENATION, [self.operand(t) for t in parts] + [self.scalar_i32(0)], joined_index)
    joined.local = joined_index
    joined.local_sub = self.cur_index
    out = self.define(node.output[0], out_shape)
    self.emit(nnapi.RESHAPE, [self.operand(joined), self.cur.i32(list(out_shape))], out)


Lowerer.op_Gemm = _op_gemm_chunked_v3


# ---------------------------------------------------------------------------
# NNAPI rejects a model where an operand is both an output and an input of a
# later operation, so the subgraph is closed before such a consumer runs.
def _needs_split(self, node):
    if self.cur is None:
        return False
    outputs = set(self.cur.outputs)
    if not outputs:
        return False
    buffered = set()
    for index in outputs:
        entry = self.cur.values[index]
        if entry[3] is not None and entry[3] != 0xFFFFFFFF:
            buffered.add(entry[3])
    if not buffered:
        return False
    for name in node.input:
        tensor = self.tensors.get(name)
        if tensor is None or tensor.const is not None:
            continue
        if tensor.buf is not None and tensor.buf in buffered:
            return True
    return False


def _run_v6(self):
    self.keep = list(self.keep)
    self.graph_outputs = set(o.name for o in self.graph.output) | set(self.keep)
    self.start_subgraph()
    for value in self.graph.input:
        tensor = Tensor(value.name, F16, tuple(self.shape_of(value.name)))
        tensor.buf = self.plan.buffer(sanitize(value.name), F16, tensor.shape)
        tensor.is_input = True
        self.tensors[value.name] = tensor
    for name, tensor in self.initializers.items():
        if name in self.tensors:
            continue
        self.tensors[name] = Tensor(name, self.dtypes.get(name, 10),
                                    tuple(self.shape_of(name) if name in self.shapes else tensor.shape),
                                    const=tensor)
    regions = _region_map(self)
    position = 0
    while position < len(self.nodes):
        if position in regions:
            end, final_name = regions[position]
            self.position = position
            self.flush(position)
            region_out = _lower_region(self, position, end, final_name)
            self.tensors[final_name] = region_out
            position = end + 1
            continue
        gelu = _gelu_match(self, position)
        if gelu is not None:
            gelu_out, gelu_x, _ = gelu
            self.position = position
            _emit_gelu(self, position, gelu_out, gelu_x)
            position += 5
            continue
        node = self.nodes[position]
        self.position = position
        for out_name in node.output:
            declared = self.shapes.get(out_name)
            store = getattr(self, '_orig_shape', None)
            if store is None:
                store = self._orig_shape = {}
            if declared is not None:
                store.setdefault(out_name, tuple(declared))
            if declared is not None and len(declared) > 4:
                raw = getattr(self, '_raw', None)
                if raw is None:
                    raw = self._raw = {}
                raw[out_name] = tuple(declared)
        if _needs_split(self, node):
            self.flush(position)
        heavy = _is_heavy(self, node)
        if heavy:
            self.flush(position)
        self.maybe_flush(position)
        handler = getattr(self, 'op_' + node.op_type, None)
        if handler is None:
            raise NotImplementedError(f'{node.op_type} ({node.name})')
        handler(node)
        if heavy:
            self.flush(position + 1)
        position += 1
    self.flush(len(self.nodes))
    self.plan.subs = [s for s in self.plan.subs if s.ops]
    for name in self.keep:
        tensor = self.tensors[name]
        if tensor.buf is None:
            raise RuntimeError(f'kept tensor not materialised: {name}')
        self.plan.results.append(tensor.buf)
    for out in (self.graph.output if self.limit is None else []):
        tensor = self.tensors[out.name]
        if tensor.buf is None:
            raise RuntimeError(f'graph output not materialised: {out.name}')
        self.plan.results.append(tensor.buf)
    return self.plan


Lowerer.run = _run_v6


# ---------------------------------------------------------------------------
# Neuron rejects a model whose output is also consumed by a later op, so each
# boundary value is copied through an identity reshape into its own buffer.
_materialise_copy = True


def _flush_copy(self, node_pos):
    if self.cur is None or not self.cur.ops:
        return
    # Buffer IDs represent values, including every reshape alias. Name-based
    # recycling can overwrite a live alias much later in the graph. The runner
    # allocates lazily and frees at the last actual subgraph read instead.
    materialised = []
    for name, tensor in list(self.tensors.items()):
        if tensor.const is not None or tensor.buf is not None or tensor.local is None:
            continue
        if tensor.local_sub != self.cur_index:
            continue
        if self.last_use.get(name, -1) < node_pos and name not in self.graph_outputs:
            continue
        buffer = self.plan.buffer(f'{sanitize(name)}_{self.unique}', tensor.dtype, tensor.shape)
        self.unique += 1
        copy_index = self.cur.val(tensor.dtype, tensor.shape)
        if tensor.dtype == I32:
            self.cur.op(nnapi.RESHAPE, [tensor.local, self.cur.i32(list(tensor.shape))], copy_index)
        else:
            zeros = self.define(f'{name}/zc{self.unique}', (1,) * len(tensor.shape),
                                const=np.zeros((1,) * len(tensor.shape), np.float16))
            self.unique += 1
            self.cur.op(nnapi.ADD, [tensor.local, self.operand(zeros), self.scalar_i32(0)], copy_index)
        entry = self.cur.values[copy_index]
        self.cur.values[copy_index] = (entry[0], entry[1], entry[2], buffer)
        self.cur.outputs.append(copy_index)
        tensor.buf = buffer
        tensor.local = None
        materialised.append(name)
    if self.verbose:
        print(f'  flush at node {node_pos}: {len(self.cur.ops)} ops, {len(materialised)} buffers')
    self.start_subgraph()


Lowerer.flush = _flush_copy


# ---------------------------------------------------------------------------
_align_bmm = True


def _op_matmul_ranked(self, node):
    a = self.tensor(node.input[0])
    b = self.tensor(node.input[1])
    out_shape = tuple(self.shape_of(node.output[0]))
    rank = max(len(a.shape), len(b.shape))
    if len(a.shape) != rank:
        a = self.reshaped(a, (1,) * (rank - len(a.shape)) + tuple(a.shape), node.name + '/arank')
    if len(b.shape) != rank:
        b = self.reshaped(b, (1,) * (rank - len(b.shape)) + tuple(b.shape), node.name + '/brank')
    if len(out_shape) != 4 or out_shape[0] <= 16:
        out = self.define(node.output[0], out_shape)
        self.emit(nnapi.BATCH_MATMUL, [self.operand(a), self.operand(b),
                                       self.cur.scalar_bool(False), self.cur.scalar_bool(False)], out)
        return
    batch = out_shape[0]
    chunk = 14
    parts = []
    for start in range(0, batch, chunk):
        size = min(chunk, batch - start)
        a_slice = _slice_t(self, a, (start, 0, 0, 0), (size,) + tuple(a.shape[1:]),
                           node.name + f'/as{start}')
        b_slice = _slice_t(self, b, (start, 0, 0, 0), (size,) + tuple(b.shape[1:]),
                           node.name + f'/bs{start}')
        part = self.define(node.name + f'/mm{start}', (size,) + out_shape[1:])
        self.emit(nnapi.BATCH_MATMUL, [self.operand(a_slice), self.operand(b_slice),
                                       self.cur.scalar_bool(False), self.cur.scalar_bool(False)], part)
        parts.append(self.operand(part))
    out = self.define(node.output[0], out_shape)
    self.emit(nnapi.CONCATENATION, parts + [self.scalar_i32(0)], out)


Lowerer.op_MatMul = _op_matmul_ranked


# ---------------------------------------------------------------------------
# NNAPI only takes rank <= 4 tensors.  Folding the leading dims is only sound
# when both operands fold to the same shape, so operands that broadcast along a
# folded dim are expanded first (tile through an inserted unit axis).
_fold_rank5 = True


def _fold_shape(shape):
    shape = list(shape)
    while len(shape) > 4:
        shape = [shape[0] * shape[1]] + shape[2:]
    return tuple(shape)


def _raw_shape(self, tensor):
    raw = getattr(self, '_raw', {}).get(tensor.name)
    if raw is not None:
        return tuple(raw)
    declared = self.shapes.get(tensor.name)
    if declared is not None:
        return tuple(declared)
    return tuple(tensor.shape)


def _broadcast_raw(ra, rb):
    rank = max(len(ra), len(rb))
    ra = (1,) * (rank - len(ra)) + tuple(ra)
    rb = (1,) * (rank - len(rb)) + tuple(rb)
    out = []
    for x, y in zip(ra, rb):
        if x == y or y == 1:
            out.append(x)
        elif x == 1:
            out.append(y)
        else:
            raise ValueError(f'cannot broadcast {ra} with {rb}')
    return tuple(out)


def _expand_folded(self, tensor, raw, target, name, broad=None):
    folded = _fold_shape(raw)
    if folded == target:
        return tensor
    if tensor.const is not None:
        raw_target = broad if broad is not None else raw
        data = np.broadcast_to(np.asarray(tensor.const).reshape(raw), raw_target)
        return self.define(name, target, const=np.ascontiguousarray(data.reshape(target)))
    if len(folded) != len(target) or folded[0] == target[0]:
        return tensor
    if target[0] % folded[0] != 0:
        raise ValueError(f'cannot expand {folded} to {target}')
    repeats = target[0] // folded[0]
    tail = 1
    for dim in folded[1:]:
        tail *= dim
    inserted = self.reshaped(tensor, (folded[0], 1, tail), name + '/ins')
    tiled = self.define(name + '/tile', (folded[0], repeats, tail))
    self.emit(nnapi.TILE, [self.operand(inserted), self.cur.i32([1, repeats, 1])], tiled)
    return self.reshaped(tiled, target, name + '/tgt')


def _binary_v3(self, code, a, b, out_shape, name, fuse=True):
    out_shape = tuple(out_shape)
    ra = _raw_shape(self, a)
    rb = _raw_shape(self, b)
    if max(len(ra), len(rb)) > 4:
        try:
            broad = _broadcast_raw(ra, rb)
        except ValueError:
            broad = None
        if broad is None:
            return _binary_v2(self, code, a, b, out_shape, name, fuse)
        target = _fold_shape(broad)
        a = _expand_folded(self, a, ra, target, name + '/ea', broad)
        b = _expand_folded(self, b, rb, target, name + '/eb', broad)
        out = self.define(name, target)
        ins = [self.operand(a), self.operand(b)]
        if fuse:
            ins.append(self.scalar_i32(0))
        self.emit(code, ins, out)
        return out
    target = out_shape
    if len(out_shape) > 4:
        target = _fold_shape(out_shape)
        a = _squeeze_rank(self, a)
        b = _squeeze_rank(self, b)
    out = self.define(name, target)
    ins = [self.operand(a), self.operand(b)]
    if fuse:
        ins.append(self.scalar_i32(0))
    self.emit(code, ins, out)
    if target != out_shape:
        final = self.define(name + '/unsq', out_shape)
        self.emit(nnapi.RESHAPE, [self.operand(out), self.cur.i32(list(out_shape))], final)
        return final
    return out


_original_define = Lowerer.define


def _define_raw(self, name, shape, const=None, **kwargs):
    shape = _canon(shape)
    return _original_define(self, name, shape, const=const, **kwargs)


Lowerer.define = _define_raw
Lowerer.binary = _binary_v3


# ---------------------------------------------------------------------------
_op_reshape_alias = True


def _alias_shape(self, tensor, shape, name):
    alias = Tensor(name, tensor.dtype, tuple(shape))
    alias.buf = tensor.buf
    alias.is_input = True
    alias.local = None
    alias.const = None
    return alias


def _op_reshape_alias(self, node):
    data = self.tensor(node.input[0])
    raw = [int(v) for v in self.tensor(node.input[1]).const.ravel()]
    out_shape = self.shape_of(node.output[0])
    if 0 in raw or -1 in raw:
        raw = list(self.shapes.get(node.output[0], out_shape))
    raw_shapes = getattr(self, '_raw', None)
    if raw_shapes is None:
        raw_shapes = self._raw = {}
    raw_shapes[node.output[0]] = tuple(raw)
    canonic = tuple(_canon(raw))
    if data.const is not None:
        reshaped = np.ascontiguousarray(np.asarray(data.const).reshape(canonic))
        out = self.define(node.output[0], out_shape, const=reshaped)
        self.tensors[node.output[0]] = out
        return
    if data.buf is None:
        self.last_use[data.name] = self.position
        self.flush(self.position)
    if data.buf is None:
        out = self.define(node.output[0], canonic)
        self.emit(nnapi.RESHAPE, [self.operand(data), self.cur.i32(list(canonic))], out)
        self.tensors[node.output[0]] = out
        return
    alias = _alias_shape(self, data, canonic, node.output[0])
    self.tensors[node.output[0]] = alias


Lowerer.op_Reshape = _op_reshape_v2

Lowerer.op_Reshape = _op_reshape_v2


# ---------------------------------------------------------------------------
_op_reshape_staged = True
_NH_LIMIT = 65535


def _nh(shape):
    if len(shape) < 2:
        return 1
    return shape[0] * shape[1]


def _block_split(source, target):
    """Group the two shapes into matching products (or None when they differ)."""
    i = j = 0
    blocks = []
    pa = source[0]
    pb = target[0]
    while True:
        if pa == pb:
            blocks.append((pa, i, j))
            i += 1
            j += 1
            if i >= len(source) or j >= len(target):
                break
            pa = source[i]
            pb = target[j]
        elif pa < pb:
            i += 1
            if i >= len(source):
                return None
            pa *= source[i]
        else:
            j += 1
            if j >= len(target):
                return None
            pb *= target[j]
    return blocks


def _block_ranges(blocks):
    ranges = []
    start_a = 0
    start_b = 0
    for product, a_index, b_index in blocks:
        ranges.append((product, start_a, a_index + 1, start_b, b_index + 1))
        start_a = a_index + 1
        start_b = b_index + 1
    return ranges


def _rotate(entries, offset):
    return entries[offset:] + entries[:offset]


def _op_reshape_staged(self, node):
    data = self.tensor(node.input[0])
    raw = [int(v) for v in self.tensor(node.input[1]).const.ravel()]
    out_shape = self.shape_of(node.output[0])
    if 0 in raw or -1 in raw:
        raw = list(self.shapes.get(node.output[0], out_shape))
    raw_shapes = getattr(self, '_raw', None)
    if raw_shapes is None:
        raw_shapes = self._raw = {}
    raw_shapes[node.output[0]] = tuple(raw)
    target = list(_canon(raw))
    source = list(data.shape)
    if target == source:
        out = self.define(node.output[0], tuple(target))
        self.emit(nnapi.RESHAPE, [self.operand(data), self.cur.i32(target)], out)
        self.last_use[data.name] = max(self.last_use.get(data.name, 0),
                                       self.last_use.get(node.output[0], 0))
        return
    if len(target) < 2 or len(source) < 2 or (_nh(source) <= _NH_LIMIT and _nh(target) <= _NH_LIMIT):
        out = self.define(node.output[0], out_shape)
        self.emit(nnapi.RESHAPE, [self.operand(data), self.cur.i32(target)], out)
        return
    blocks = _block_split(source, target)
    if blocks is None or len(blocks) < 3:
        out = self.define(node.output[0], out_shape)
        self.emit(nnapi.RESHAPE, [self.operand(data), self.cur.i32(target)], out)
        return
    best = None
    for offset in range(len(blocks)):
        rotated = _rotate(blocks, offset)
        src_dims = []
        dst_dims = []
        for _product, a_index, b_index in rotated:
            src_dims.extend(source[a_index:b_index + 1])
            dst_dims.extend(target[a_index:b_index + 1])
        if _nh(src_dims) <= _NH_LIMIT and _nh(dst_dims) <= _NH_LIMIT:
            best = (offset, src_dims, dst_dims)
            break
    if best is None:
        out = self.define(node.output[0], out_shape)
        self.emit(nnapi.RESHAPE, [self.operand(data), self.cur.i32(target)], out)
        return
    offset, src_dims, dst_dims = best
    order = []
    for _product, a_index, b_index in _rotate(blocks, offset):
        order.extend(range(a_index, b_index + 1))
    staged = self.define(node.output[0] + '/rot', tuple(src_dims))
    self.emit(nnapi.TRANSPOSE, [self.operand(data), self.cur.i32(order)], staged)
    middle = self.define(node.output[0] + '/mid', tuple(dst_dims))
    self.emit(nnapi.RESHAPE, [self.operand(staged), self.cur.i32(dst_dims)], middle)
    back = []
    cursor = 0
    positions = {}
    for _product, a_index, b_index in _rotate(blocks, offset):
        for slot in range(a_index, b_index + 1):
            positions[slot] = cursor
            cursor += 1
    for slot in range(len(target)):
        back.append(positions[slot])
    out = self.define(node.output[0], out_shape)
    self.emit(nnapi.TRANSPOSE, [self.operand(middle), self.cur.i32(back)], out)


Lowerer.op_Reshape = _op_reshape_v2


# ---------------------------------------------------------------------------
_op_reshape_stage2 = True


def _nh4(shape):
    return shape[0] * shape[1] if len(shape) == 4 else 0


def _blocks_of(source, target):
    """Match the two shapes into contiguous blocks of equal product."""
    i = j = 0
    blocks = []
    pa = source[0]
    pb = target[0]
    a0 = b0 = 0
    while True:
        if pa == pb:
            blocks.append((pa, a0, i + 1, b0, j + 1))
            i += 1
            j += 1
            if i >= len(source) or j >= len(target):
                break
            pa = source[i]
            pb = target[j]
            a0 = i
            b0 = j
        elif pa < pb:
            i += 1
            if i >= len(source):
                return None
            pa *= source[i]
        else:
            j += 1
            if j >= len(target):
                return None
            pb *= target[j]
    if i != len(source) or j != len(target):
        return None
    return blocks


def _op_reshape_stage2(self, node):
    data = self.tensor(node.input[0])
    raw = [int(v) for v in self.tensor(node.input[1]).const.ravel()]
    out_shape = self.shape_of(node.output[0])
    if 0 in raw or -1 in raw:
        raw = list(self.shapes.get(node.output[0], out_shape))
    raw_shapes = getattr(self, '_raw', None)
    if raw_shapes is None:
        raw_shapes = self._raw = {}
    raw_shapes[node.output[0]] = tuple(raw)
    target = list(_canon(raw))
    source = list(data.shape)

    def plain():
        out = self.define(node.output[0], out_shape)
        self.emit(nnapi.RESHAPE, [self.operand(data), self.cur.i32(target)], out)

    if target == source:
        self.tensors[node.output[0]] = data
        return
    if len(target) == 4 and _nh4(target) > 65535 and target[2] * target[3] > 0:
        merged = [target[0], target[1], target[2] * target[3]]
        out = self.define(node.output[0], tuple(merged))
        self.emit(nnapi.RESHAPE, [self.operand(data), self.cur.i32(merged)], out)
        self.shapes[node.output[0]] = tuple(merged)
        return
    if len(target) < 2 or len(source) < 2 or (_nh4(source) <= 65535 and _nh4(target) <= 65535):
        plain()
        return
    blocks = _blocks_of(source, target)
    if blocks is None or len(blocks) < 2:
        plain()
        return
    for offset in range(len(blocks)):
        rotated = blocks[offset:] + blocks[:offset]
        a_dims = []
        b_dims = []
        for _product, a0, a1, b0, b1 in rotated:
            a_dims.extend(source[a0:a1])
            b_dims.extend(target[b0:b1])
        if _nh4(a_dims) > 65535 or _nh4(b_dims) > 65535:
            continue
        order = []
        for _product, a0, a1, _b0, _b1 in rotated:
            order.extend(range(a0, a1))
        positions = {}
        cursor = 0
        for _product, _a0, _a1, b0, b1 in rotated:
            for slot in range(b0, b1):
                positions[slot] = cursor
                cursor += 1
        back = [positions[slot] for slot in range(len(target))]
        staged = self.define(node.output[0] + '/rot', tuple(a_dims))
        self.emit(nnapi.TRANSPOSE, [self.operand(data), self.cur.i32(order)], staged)
        middle = self.define(node.output[0] + '/mid', tuple(b_dims))
        self.emit(nnapi.RESHAPE, [self.operand(staged), self.cur.i32(b_dims)], middle)
        out = self.define(node.output[0], out_shape)
        self.emit(nnapi.TRANSPOSE, [self.operand(middle), self.cur.i32(back)], out)
        return
    if data.buf is None:
        self.last_use[data.name] = self.position
        self.flush(self.position)
    if data.buf is not None and len(target) == len(source) and [i for i in range(len(source))]:
        alias = _make_alias(self, data, target, node.output[0])
        self.tensors[node.output[0]] = alias
        return
    plain()


Lowerer.op_Reshape = _op_reshape_stage2


# ---------------------------------------------------------------------------
_reshaped_final = True


def _make_alias(self, tensor, shape, name):
    alias = Tensor(name, tensor.dtype, tuple(shape))
    alias.buf = tensor.buf
    alias.local = None
    alias.const = None
    alias.is_input = True
    return alias


def _reshaped_final(self, tensor, shape, name=None):
    shape = tuple(_canon(shape))
    if shape == tuple(tensor.shape):
        return tensor
    if _nh4(list(shape)) <= 65535 and _nh4(list(tensor.shape)) <= 65535:
        out = self.define(name or f'{tensor.name}/r{self.unique}', shape)
        self.unique += 1
        self.emit(nnapi.RESHAPE, [self.operand(tensor), self.cur.i32(list(shape))], out)
        return out
    blocks = _blocks_of(list(tensor.shape), list(shape))
    if blocks is not None and len(blocks) >= 2:
        for offset in range(len(blocks)):
            rotated = blocks[offset:] + blocks[:offset]
            a_dims = []
            b_dims = []
            for _product, a0, a1, b0, b1 in rotated:
                a_dims.extend(tensor.shape[a0:a1])
                b_dims.extend(shape[b0:b1])
            if _nh4(a_dims) > 65535 or _nh4(b_dims) > 65535:
                continue
            order = []
            for _product, a0, a1, _b0, _b1 in rotated:
                order.extend(range(a0, a1))
            positions = {}
            cursor = 0
            for _product, _a0, _a1, b0, b1 in rotated:
                for slot in range(b0, b1):
                    positions[slot] = cursor
                    cursor += 1
            back = [positions[slot] for slot in range(len(shape))]
            label = name or f'{tensor.name}/r{self.unique}'
            self.unique += 1
            staged = self.define(label + '/rot', tuple(a_dims))
            self.emit(nnapi.TRANSPOSE, [self.operand(tensor), self.cur.i32(order)], staged)
            middle = self.define(label + '/mid', tuple(b_dims))
            self.emit(nnapi.RESHAPE, [self.operand(staged), self.cur.i32(b_dims)], middle)
            out = self.define(label, shape)
            self.emit(nnapi.TRANSPOSE, [self.operand(middle), self.cur.i32(back)], out)
            return out
    if tensor.buf is None:
        self.last_use[tensor.name] = self.position
        self.flush(self.position)
    if tensor.buf is not None:
        return _make_alias(self, tensor, shape, name or f'{tensor.name}/a{self.unique}')
    out = self.define(name or f'{tensor.name}/r{self.unique}', shape)
    self.unique += 1
    self.emit(nnapi.RESHAPE, [self.operand(tensor), self.cur.i32(list(shape))], out)
    return out


Lowerer.reshaped = _reshaped_final


# ---------------------------------------------------------------------------
_layout3_shim = True


def _l3_map(self):
    table = getattr(self, '_layout3', None)
    if table is None:
        table = self._layout3 = {}
    return table


def _l3_shape(shape):
    return (shape[0], shape[1], shape[2] * shape[3])


def _l3_const(self, tensor, logical, target3, name):
    """Broadcast a 4-D constant into the rank-3 layout."""
    data = np.asarray(tensor.const, dtype=np.float16)
    rank = len(logical)
    padded = (1,) * (rank - data.ndim) + tuple(data.shape)
    data = data.reshape(padded)
    full = [1 if padded[index] == 1 else logical[index] for index in range(rank)]
    body = np.broadcast_to(data, tuple(full)).reshape(target3)
    return self.define(name, tuple(target3), const=np.ascontiguousarray(body))


def _op_reshape_l3(self, node):
    data = self.tensor(node.input[0])
    raw = [int(v) for v in self.tensor(node.input[1]).const.ravel()]
    out_shape = self.shape_of(node.output[0])
    if 0 in raw or -1 in raw:
        raw = list(self.shapes.get(node.output[0], out_shape))
    table = _l3_map(self)
    table[node.output[0]] = tuple(raw)
    raw_shapes = getattr(self, '_raw', None)
    if raw_shapes is None:
        raw_shapes = self._raw = {}
    raw_shapes[node.output[0]] = tuple(raw)
    target = list(_canon(raw))
    source = list(data.shape)
    if len(target) == 4 and (target[0] * target[1] > 65535):
        merged = list(_l3_shape(target))
        out = self.define(node.output[0], tuple(merged))
        self.emit(nnapi.RESHAPE, [self.operand(data), self.cur.i32(merged)], out)
        table[node.output[0]] = tuple(target)
        return
    _op_reshape_stage2(self, node)


def _elementwise_l3(self, code, node):
    table = _l3_map(self)
    ins = []
    logical_out = None
    tensors = [self.tensor(node.input[0]), self.tensor(node.input[1])]
    for tensor in tensors:
        if tensor.name in table:
            logical_out = table[tensor.name]
            break
    if logical_out is None:
        _elementwise_v2(self, code, node)
        return
    out4 = list(self.shape_of(node.output[0]))
    if len(out4) != 4:
        _elementwise_v2(self, code, node)
        return
    out3 = list(_l3_shape(out4))
    for tensor in tensors:
        if tensor.const is not None and tensor.name not in table:
            padded = (1,) * (4 - np.ndim(tensor.const)) + tuple(np.shape(tensor.const))
            covered = [1 if padded[i] == 1 else out4[i] for i in range(4)]
            if int(np.prod(covered)) != int(np.prod(out3)):
                _elementwise_v2(self, code, node)
                return
        if tensor.name in table:
            ins.append(self.operand(tensor))
        elif tensor.const is not None:
            stretched = _l3_const(self, tensor, out4, out3, tensor.name + '/l3')
            ins.append(self.operand(stretched))
        else:
            ins.append(self.operand(tensor))
    out = self.define(node.output[0], tuple(out3))
    ins.append(self.scalar_i32(0))
    self.emit(code, ins, out)
    table[node.output[0]] = tuple(out4)


Lowerer.op_Reshape = _op_reshape_stage2
Lowerer.elementwise = _elementwise_v2


# ---------------------------------------------------------------------------
_attn_layout = True


def _is_attn(name):
    return ('self_attn' in name) or ('GridSample' in name)


def _canon_attn(shape):
    shape = [int(d) for d in shape]
    while len(shape) > 1 and shape[0] == 1:
        shape.pop(0)
    while len(shape) > 4:
        shape = shape[:-2] + [shape[-2] * shape[-1]]
    return tuple(shape)


def _attn_axis(shape, axis):
    """Map an axis of the raw shape onto the folded layout."""
    rank = len(shape)
    lead = 0
    while lead < rank - 1 and shape[lead] == 1:
        lead += 1
    axis = axis - lead
    if axis < 0:
        return 0
    merged = _canon_attn(shape)
    if axis >= len(merged) - 1:
        return len(merged) - 1
    return axis


_old_shape_of = _shape_of_v2


def _shape_of_attn(self, name):
    declared = self.shapes.get(name)
    if declared is not None and _is_attn(name) and len(declared) > 4:
        return _canon_attn(declared)
    return _old_shape_of(self, name)


Lowerer.shape_of = _old_shape_of


_old_slice = _op_slice


def _op_slice_attn(self, node):
    if _is_attn(node.input[0]):
        declared = self.shapes.get(node.input[0])
        if declared is not None and len(declared) > 4:
            data = self.tensor(node.input[0])
            starts = [int(v) for v in self.tensor(node.input[1]).const.ravel()]
            ends = [int(v) for v in self.tensor(node.input[2]).const.ravel()]
            if len(node.input) > 3 and node.input[3]:
                axes = [int(v) for v in self.tensor(node.input[3]).const.ravel()]
            else:
                axes = list(range(len(starts)))
            steps = ([int(v) for v in self.tensor(node.input[4]).const.ravel()]
                     if len(node.input) > 4 and node.input[4] else [1] * len(starts))
            mapped = [_attn_axis(declared, a) for a in axes]
            out_shape = self.shape_of(node.output[0])
            begin = [0] * len(data.shape)
            size = [1] * len(data.shape)
            stride = [1] * len(data.shape)
            for index, axis in enumerate(mapped):
                length = data.shape[axis]
                start = starts[index]
                end = ends[index]
                if end < 0:
                    end += length
                begin[axis] = max(0, start)
                size[axis] = max(1, min(end, length) - begin[axis])
                stride[axis] = steps[index]
            out = self.define(node.output[0], out_shape)
            self.emit(nnapi.STRIDED_SLICE,
                      [self.operand(data), self.cur.i32(begin), self.cur.i32(size),
                       self.cur.i32(stride), self.cur.i32(0), self.cur.i32(0), self.cur.i32(0)],
                      out)
            return
    return _old_slice(self, node)


Lowerer.op_Slice = _old_slice


# ---------------------------------------------------------------------------
_virtual_layout = True


def _virt_map(self):
    table = getattr(self, '_virtual', None)
    if table is None:
        table = self._virtual = {}
    return table


def _virt(shape):
    """Physical rank-2 form of a virtual shape."""
    flat = 1
    for dim in shape[1:]:
        flat *= dim
    return (shape[0], flat)


def _op_reshape_virtual(self, node):
    declared_out = self.shapes.get(node.output[0])
    if declared_out is not None:
        store = getattr(self, '_orig_shape', None)
        if store is None:
            store = self._orig_shape = {}
        store.setdefault(node.output[0], tuple(declared_out))
    data = self.tensor(node.input[0])
    raw = [int(v) for v in self.tensor(node.input[1]).const.ravel()]
    out_shape = self.shape_of(node.output[0])
    if 0 in raw or -1 in raw:
        raw = list(self.shapes.get(node.output[0], out_shape))
    table = _virt_map(self)
    if len(raw) >= 5 and len(data.shape) == 2:
        total = 1
        for dim in raw:
            total *= dim
        if total == int(np.prod(data.shape)):
            raw = list(_drop_leading(raw))
            if _virt(raw) == tuple(data.shape):
                table[node.output[0]] = tuple(raw)
                self.tensors[node.output[0]] = data
                return
    table.pop(node.output[0], None)
    target = list(_canon(raw))
    if (len(target) == 3 and target[-1] > 65535 and len(data.shape) in (3, 4)
            and data.shape[-2] * data.shape[-1] == target[-1]):
        physical = tuple(target[:2]) + tuple(data.shape[-2:])
        out = self.reshaped(data, physical, node.output[0])
        self.tensors[node.output[0]] = out
        self.shapes[node.output[0]] = physical
        return
    # GroupNorm exports [N, groups, channels_per_group * H * W]. MDLA
    # rejects individual dimensions above 65535; keep the spatial plane apart.
    if len(target) == 3 and target[-1] > 65535 and len(data.shape) == 4:
        plane = data.shape[-2] * data.shape[-1]
        if plane <= 65535 and target[-1] % plane == 0:
            physical = tuple(target[:2]) + (target[-1] // plane, plane)
            out = self.define(node.output[0], physical)
            self.emit(nnapi.RESHAPE, [self.operand(data), self.cur.i32(list(physical))], out)
            self.shapes[node.output[0]] = physical
            return
    if len(target) == 4 and target[0] * target[1] > 65535 and 1 in target:
        squeezed = [d for d in target if d != 1]
        if len(squeezed) == 3 and int(np.prod(squeezed)) == int(np.prod(data.shape)):
            out = self.define(node.output[0], tuple(squeezed))
            self.emit(nnapi.RESHAPE, [self.operand(data), self.cur.i32(squeezed)], out)
            table[node.output[0]] = tuple(target)
            return
    return _op_reshape_stage2(self, node)


def _drop_leading(shape):
    shape = list(shape)
    while len(shape) > 5 and shape[0] == 1:
        shape.pop(0)
    return shape


def _tile_constant(self, tensor, virt_out, name):
    body = np.asarray(tensor.const, dtype=np.float16)
    rank = len(virt_out)
    padded = (1,) * (rank - body.ndim) + tuple(body.shape)
    body = body.reshape(padded)
    full = [1 if padded[i] == 1 else virt_out[i] for i in range(rank)]
    expanded = np.broadcast_to(body, tuple(full))
    flat = expanded.reshape(full[0], -1)
    return self.define(name, flat.shape, const=np.ascontiguousarray(flat))


def _binary_virtual(self, code, a, b, out_shape, name, fuse=True):
    table = _virt_map(self)
    va = table.get(a.name)
    vb = table.get(b.name)
    if va is None and vb is None:
        return _binary_v3(self, code, a, b, out_shape, name, fuse)
    virt = va if va is not None else vb
    if va is not None and vb is not None:
        rank = max(len(va), len(vb))
        pa = (1,) * (rank - len(va)) + tuple(va)
        pb = (1,) * (rank - len(vb)) + tuple(vb)
        virt = tuple(max(x, y) for x, y in zip(pa, pb))
    physical = [(1 if va is not None else a.shape[0]), 0]
    flat = _virt(virt)
    ins = []
    for tensor in (a, b):
        if tensor.const is not None:
            ins.append(self.operand(_tile_constant(self, tensor, virt, tensor.name + '/vt')))
        else:
            ins.append(self.operand(tensor))
    if fuse:
        ins.append(self.scalar_i32(0))
    out = self.define(name, flat)
    self.emit(code, ins, out)
    table[name] = virt
    return out


def _elementwise_virtual(self, code, node):
    a = self.tensor(node.input[0])
    b = self.tensor(node.input[1])
    table = _virt_map(self)
    if a.name not in table and b.name not in table:
        return _elementwise_v2(self, code, node)
    out_shape = self.shape_of(node.output[0])
    out = _binary_virtual(self, code, a, b, out_shape, node.output[0])
    self.tensors[node.output[0]] = out


Lowerer.op_Reshape = _op_reshape_virtual
Lowerer.elementwise = _elementwise_virtual
Lowerer.binary = _binary_virtual


# ---------------------------------------------------------------------------
_squeeze_repair = True


def _axis_list(self, node):
    if len(node.input) > 1 and node.input[1]:
        return [int(v) for v in self.tensor(node.input[1]).const.ravel()]
    return [int(v) for v in self.attrs(node).get('axes', [])]


def _op_squeeze_fix(self, node):
    data = self.tensor(node.input[0])
    shape = list(data.shape)
    rank = len(shape)
    axes = _axis_list(self, node)
    if axes:
        drop = {axis % rank for axis in axes}
    else:
        drop = {index for index, dim in enumerate(shape) if dim == 1}
    new_shape = [dim for index, dim in enumerate(shape) if index not in drop]
    self.shapes[node.output[0]] = tuple(new_shape)
    if new_shape == shape:
        self.tensors[node.output[0]] = data
        return
    out = self.define(node.output[0], tuple(new_shape))
    self.emit(nnapi.RESHAPE, [self.operand(data), self.cur.i32(new_shape)], out)


def _op_unsqueeze_fix(self, node):
    data = self.tensor(node.input[0])
    shape = list(data.shape)
    axes = _axis_list(self, node)
    if not axes:
        raise NotImplementedError('unsqueeze without axes')
    rank = len(shape) + len(axes)
    positions = sorted({axis % rank for axis in axes})
    new_shape = list(shape)
    for position in positions:
        new_shape.insert(position, 1)
    self.shapes[node.output[0]] = tuple(new_shape)
    if new_shape == shape:
        self.tensors[node.output[0]] = data
        return
    out = self.define(node.output[0], tuple(new_shape))
    self.emit(nnapi.RESHAPE, [self.operand(data), self.cur.i32(new_shape)], out)


# reverted: Lowerer.op_Squeeze = _op_squeeze_fix
# reverted: Lowerer.op_Unsqueeze = _op_unsqueeze_fix


# ---------------------------------------------------------------------------
_concat_fold = True


def _op_concat_fold(self, node):
    tensors = [self.tensor(i) for i in node.input]
    axis = int(self.attrs(node)['axis'])
    rank = len(tensors[0].shape)
    declared = self.shapes.get(node.output[0])
    if declared is not None and len(declared) > 4:
        folded = list(declared)
        while len(folded) > 1 and folded[0] == 1:
            folded.pop(0)
        while len(folded) > 4:
            folded = folded[:-2] + [folded[-2] * folded[-1]]
        out = self.define(node.output[0], tuple(folded))
        ins = [self.operand(t) for t in tensors]
        ins.append(self.scalar_i32(len(folded) - 1))
        self.emit(nnapi.CONCATENATION, ins, out)
        self.shapes[node.output[0]] = tuple(folded)
        self.last_use[node.output[0]] = max(self.last_use.get(node.output[0], 0), self.position + 1)
        self.flush(self.position)
        return
    if axis < 0:
        axis += rank
    out_shape = self.shape_of(node.output[0])
    out = self.define(node.output[0], out_shape)
    ins = []
    for t in tensors:
        if len(t.shape) != rank:
            raise NotImplementedError('concat rank mismatch')
        ins.append(self.operand(t))
    ins.append(self.scalar_i32(axis))
    self.emit(nnapi.CONCATENATION, ins, out)


Lowerer.op_Concat = _op_concat_fold


# ---------------------------------------------------------------------------
_axis_repair = True


def _repair_table(self):
    table = getattr(self, '_repaired', None)
    if table is None:
        table = self._repaired = {}
    return table


def _define_repaired(self, name, shape, const=None, **kwargs):
    shape = list(_canon(shape))
    if len(shape) == 4 and shape[0] * shape[1] > 65535 and shape[2] * shape[3] > 0:
        repaired = [shape[0], shape[1], shape[2] * shape[3]]
        _repair_table(self)[name] = (4, 3)
        self.shapes[name] = tuple(repaired)
        shape = repaired
    return _original_define(self, name, tuple(shape), const=const, **kwargs)


def _fix_axis(self, tensor, axis):
    record = _repair_table(self).get(tensor.name)
    if record is None:
        return axis
    old_rank, new_rank = record
    if old_rank == new_rank:
        return axis
    if axis < 0:
        axis += old_rank
    return min(axis, new_rank - 1)


def _fix_perm(self, tensor, perm):
    record = _repair_table(self).get(tensor.name)
    if record is None:
        return perm
    old_rank, new_rank = record
    if old_rank == new_rank:
        return perm
    mapped = []
    for axis in perm:
        mapped.append(min(axis, new_rank - 1))
    seen = []
    for axis in mapped:
        if axis not in seen:
            seen.append(axis)
    if sorted(seen) != list(range(new_rank)):
        raise NotImplementedError('transpose splits a merged axis')
    return seen


def _op_slice_repair(self, node):
    data = self.tensor(node.input[0])
    starts = [int(v) for v in self.tensor(node.input[1]).const.ravel()]
    ends = [int(v) for v in self.tensor(node.input[2]).const.ravel()]
    if len(node.input) > 3 and node.input[3]:
        axes = [int(v) for v in self.tensor(node.input[3]).const.ravel()]
    else:
        axes = list(range(len(starts)))
    steps = ([int(v) for v in self.tensor(node.input[4]).const.ravel()]
             if len(node.input) > 4 and node.input[4] else [1] * len(starts))
    if _repair_table(self).get(data.name) is None:
        return _op_slice(self, node)
    shape = list(data.shape)
    begin = [0] * len(shape)
    size = list(shape)
    stride = [1] * len(shape)
    for index, axis in enumerate(axes):
        mapped = _fix_axis(self, data, axis)
        length = shape[mapped]
        start = starts[index]
        end = ends[index]
        if end < 0:
            end += length
        begin[mapped] = max(0, start)
        size[mapped] = max(1, min(end, length) - begin[mapped])
        stride[mapped] = steps[index]
    out_shape = self.shape_of(node.output[0])
    if len(out_shape) != len(shape):
        merged = 1
        for dim in out_shape:
            merged *= dim
        if merged == int(np.prod(size)):
            out_shape = tuple(size)
    if stride == [1] * len(shape):
        out = self.define(node.output[0], tuple(size))
        self.emit(nnapi.SLICE, [self.operand(data), self.cur.i32(begin), self.cur.i32(size)], out)
    else:
        out = self.define(node.output[0], tuple(size))
        self.emit(nnapi.STRIDED_SLICE,
                  [self.operand(data), self.cur.i32(begin), self.cur.i32([b + s for b, s in zip(begin, size)]),
                   self.cur.i32(stride), self.cur.i32(0), self.cur.i32(0), self.cur.i32(0)], out)
    self.shapes[node.output[0]] = tuple(size)


def _op_concat_repair(self, node):
    tensors = [self.tensor(i) for i in node.input]
    axis = int(self.attrs(node)['axis'])
    if _repair_table(self).get(tensors[0].name) is None:
        return _op_concat_fold(self, node)
    mapped = _fix_axis(self, tensors[0], axis)
    shape = list(tensors[0].shape)
    total = sum(t.shape[mapped] for t in tensors)
    out_shape = list(shape)
    out_shape[mapped] = total
    out = self.define(node.output[0], tuple(out_shape))
    ins = [self.operand(t) for t in tensors]
    ins.append(self.scalar_i32(mapped))
    self.emit(nnapi.CONCATENATION, ins, out)
    self.shapes[node.output[0]] = tuple(out_shape)


def _op_transpose_repair(self, node):
    data = self.tensor(node.input[0])
    perm = [int(p) for p in self.attrs(node)['perm']]
    if _repair_table(self).get(data.name) is None:
        return _op_transpose_v2(self, node)
    fixed = _fix_perm(self, data, perm)
    out_shape = tuple(data.shape[axis] for axis in fixed)
    out = self.define(node.output[0], out_shape)
    self.emit(nnapi.TRANSPOSE, [self.operand(data), self.cur.i32(list(fixed))], out)
    self.shapes[node.output[0]] = out_shape


Lowerer.define = _define_repaired
Lowerer.op_Slice = _op_slice_repair
Lowerer.op_Concat = _op_concat_repair
Lowerer.op_Transpose = _op_transpose_repair


# ---------------------------------------------------------------------------
_fold_axis_map = True


def _fold_axis_map(shape):
    """Replay the shape fold and return (final_shape, axis_map)."""
    dims = [int(d) for d in shape]
    groups = [[i] for i in range(len(dims))]
    while len(dims) > 1 and dims[0] == 1:
        dims.pop(0)
        groups.pop(0)
    while len(dims) > 4:
        drop = None
        for index in range(len(dims) - 1, 0, -1):
            if dims[index] == 1:
                drop = index
                break
        if drop is not None:
            dims.pop(drop)
            groups.pop(drop)
        else:
            dims[0] = dims[0] * dims[1]
            dims.pop(1)
            groups[0] = groups[0] + groups[1]
            groups.pop(1)
    if len(dims) == 4 and dims[0] * dims[1] > 65535 and dims[2] * dims[3] > 0:
        dims[2] = dims[2] * dims[3]
        dims.pop(3)
        groups[2] = groups[2] + groups[3]
        groups.pop(3)
    mapping = {}
    for new_axis, group in enumerate(groups):
        for original in group:
            mapping[original] = new_axis
    return tuple(dims), mapping


def _tensor_axis_map(self, tensor):
    declared = getattr(self, '_orig_shape', {}).get(tensor.name)
    if declared is None:
        declared = self.shapes.get(tensor.name)
    if declared is None:
        return None, None
    final, mapping = _fold_axis_map(declared)
    if tuple(final) == tuple(tensor.shape):
        return mapping, len(final)
    return None, None


def _op_slice_fixed(self, node):
    data = self.tensor(node.input[0])
    mapping, new_rank = _tensor_axis_map(self, data)
    if mapping is None:
        return _op_slice_repair(self, node)
    starts = [int(v) for v in self.tensor(node.input[1]).const.ravel()]
    ends = [int(v) for v in self.tensor(node.input[2]).const.ravel()]
    axes = ([int(v) for v in self.tensor(node.input[3]).const.ravel()]
            if len(node.input) > 3 and node.input[3] else list(range(len(starts))))
    steps = ([int(v) for v in self.tensor(node.input[4]).const.ravel()]
             if len(node.input) > 4 and node.input[4] else [1] * len(starts))
    shape = list(data.shape)
    begin = [0] * len(shape)
    size = list(shape)
    stride = [1] * len(shape)
    for index, axis in enumerate(axes):
        mapped = mapping[axis if axis >= 0 else axis + len(mapping)]
        length = shape[mapped]
        start = starts[index]
        end = ends[index]
        if end < 0:
            end += length
        begin[mapped] = max(0, start)
        size[mapped] = max(1, min(end, length) - begin[mapped])
        stride[mapped] = steps[index]
    out_shape = self.shape_of(node.output[0])
    if len(out_shape) != len(shape):
        out_shape = tuple(size)
    out = self.define(node.output[0], tuple(size))
    if stride == [1] * len(shape):
        self.emit(nnapi.SLICE, [self.operand(data), self.cur.i32(begin), self.cur.i32(size)], out)
    else:
        self.emit(nnapi.STRIDED_SLICE,
                  [self.operand(data), self.cur.i32(begin),
                   self.cur.i32([b + s for b, s in zip(begin, size)]),
                   self.cur.i32(stride), self.cur.i32(0), self.cur.i32(0), self.cur.i32(0)], out)
    self.shapes[node.output[0]] = tuple(size)


def _fix_constant_for(self, tensor, declared, other, name):
    """Re-express a broadcast constant against the flattened data tensor."""
    try:
        _broadcast_raw(tuple(tensor.shape), tuple(other.shape))
        return tensor
    except ValueError:
        pass
    if declared is None or len(declared) < 3:
        return tensor
    row = list(declared)
    row[0] = 1
    if len(row) > 1:
        row[1] = 1
    body = np.asarray(tensor.const, dtype=np.float16)
    if body.ndim > len(row):
        return tensor
    declared_const = (getattr(self, '_orig_shape', {}).get(tensor.name)
                      or self.shapes.get(tensor.name))
    if declared_const is not None and len(declared_const) == len(row) and int(np.prod(declared_const)) == body.size:
        padded = tuple(int(d) for d in declared_const)
    else:
        padded = (1,) * (len(row) - body.ndim) + tuple(body.shape)
    body = body.reshape(padded)
    full = list(row)
    try:
        expanded = np.broadcast_to(body, tuple(full))
    except ValueError:
        return tensor
    flat = expanded.reshape(1, int(np.prod(full[2:])))
    if len(other.shape) >= 2 and tuple(flat.shape) == (1, int(np.prod(tuple(other.shape)[1:]))):
        return self.define(name, flat.shape, const=np.ascontiguousarray(flat))
    if tuple(flat.shape) != tuple(other.shape):
        return tensor
    return self.define(name, flat.shape, const=np.ascontiguousarray(flat))


def _elementwise_fixed(self, code, node):
    declared = self.shapes.get(node.output[0])
    a = self.tensor(node.input[0])
    b = self.tensor(node.input[1])
    needs = False
    for tensor, other in ((a, b), (b, a)):
        if tensor.const is None:
            continue
        try:
            _broadcast_raw(tuple(tensor.shape), tuple(other.shape))
        except ValueError:
            needs = True
    if not needs:
        return _elementwise_v2(self, code, node)
    if a.const is not None:
        a = _fix_constant_for(self, a, declared, b, a.name + '/bc')
    elif b.const is not None:
        b = _fix_constant_for(self, b, declared, a, b.name + '/bc')
    # A folded constant may still be incompatible with a physically flattened
    # operand. Expand in ONNX axis order, compute in the operand's layout, and
    # restore the output layout expected by subsequent Slice/Transpose nodes.
    try:
        _broadcast_raw(a.shape, b.shape)
    except ValueError:
        constant, data = (a, b) if a.const is not None else (b, a)
        if constant.const is None or data.const is not None or declared is None:
            raise
        raw_const = (getattr(self, '_orig_shape', {}).get(constant.name)
                     or self.shapes.get(constant.name) or constant.const.shape)
        if prod(declared) != prod(data.shape):
            raise ValueError('folded elementwise output does not match data size')
        expanded = np.broadcast_to(np.asarray(constant.const).reshape(raw_const), declared)
        fixed = self.define(constant.name + '/physical', data.shape,
                            const=np.ascontiguousarray(expanded.reshape(data.shape)))
        left, right = (fixed, data) if a.const is not None else (data, fixed)
        physical = _binary_v2(self, code, left, right, data.shape, node.output[0] + '/physical')
        out = self.define(node.output[0], self.shape_of(node.output[0]))
        self.emit(nnapi.RESHAPE, [self.operand(physical), self.cur.i32(list(out.shape))], out)
        return
    out_shape = self.shape_of(node.output[0])
    rank = len(out_shape)
    if len(a.shape) == len(b.shape) or max(len(a.shape), len(b.shape)) <= 3:
        a = self.align(a, self.broadcast_shape(a.shape, rank))
        b = self.align(b, self.broadcast_shape(b.shape, rank))
    out = _binary_v2(self, code, a, b, out_shape, node.output[0])
    out.name = node.output[0]
    self.tensors[node.output[0]] = out


Lowerer.op_Slice = _op_slice_fixed
Lowerer.elementwise = _elementwise_fixed


# ---------------------------------------------------------------------------
GS_POINT_BLOCK = 4096
GS_ROW_BLOCK = 8
GS_ROWS_PER_SUB = 1
GS_FUSE_ALL_POINTS = False


def _op_gridsample_chunked(self, node):
    attrs = self.attrs(node)
    if int(attrs.get('align_corners', 0)) != 0:
        raise NotImplementedError('gridsample align_corners')
    if attrs.get('padding_mode', b'zeros') != b'zeros':
        raise NotImplementedError('gridsample padding')
    x = self.tensor(node.input[0])
    grid = self.tensor(node.input[1])
    out_shape = self.shape_of(node.output[0])
    n, c, h, w = x.shape
    ho, wo = out_shape[2], out_shape[3]
    total = ho * wo
    g = self.reshaped(grid, (n, total, 2), node.name + '/flatgrid') if len(grid.shape) != 3 else grid

    point_block = GS_POINT_BLOCK
    row_block = GS_ROW_BLOCK
    pieces = []
    for p0 in range(0, total, point_block):
        p = min(point_block, total - p0)
        tag_p = node.name + f'/p{p0}'
        if not GS_FUSE_ALL_POINTS:
            self.last_use[g.name] = self.position
        if p == total:
            gp = g
        else:
            gp = _slice_t(self, g, (0, p0, 0), (n, p, 2), tag_p + '/g')
        gx = _slice_t(self, gp, (0, 0, 0), (n, p, 1), tag_p + '/gx')
        gy = _slice_t(self, gp, (0, 0, 1), (n, p, 1), tag_p + '/gy')

        def pixel(coord, length, tag):
            one = _minimal(self, coord.shape, 1.0, tag + '/one')
            half = _minimal(self, coord.shape, length / 2.0, tag + '/scale')
            shift = _minimal(self, coord.shape, -0.5, tag + '/shift')
            plus = self.binary(nnapi.ADD, coord, one, coord.shape, tag + '/plus')
            scaled = self.binary(nnapi.MUL, plus, half, coord.shape, tag + '/scaled')
            return self.binary(nnapi.ADD, scaled, shift, coord.shape, tag + '/pixel')

        px = pixel(gx, w, tag_p + '/px')
        py = pixel(gy, h, tag_p + '/py')

        def weights(p_coord, length, tag):
            idx = self.define(tag + '/idx', (1, 1, length),
                              const=np.arange(length, dtype=np.float16).reshape(1, 1, length))
            reps_l = self.define(tag + '/repsl', (3,), dtype=4, const=np.array([1, 1, length], dtype=np.int32))
            reps_r = self.define(tag + '/repsr', (3,), dtype=4, const=np.array([n, p, 1], dtype=np.int32))
            left = self.define(tag + '/left', (n, p, length))
            self.emit(nnapi.TILE, [self.operand(p_coord), self.operand(reps_l)], left)
            right = self.define(tag + '/right', (n, p, length))
            self.emit(nnapi.TILE, [self.operand(idx), self.operand(reps_r)], right)
            delta = self.define(tag + '/delta', (n, p, length))
            self.emit(nnapi.SUB, [self.operand(left), self.operand(right), self.scalar_i32(0)], delta)
            if not GS_FUSE_ALL_POINTS:
                self.last_use[left.name] = self.position
                self.last_use[right.name] = self.position
            absolute = self.define(tag + '/abs', (n, p, length))
            self.emit(nnapi.ABS, [self.operand(delta)], absolute)
            one = _minimal(self, absolute.shape, 1.0, tag + '/one')
            negative = self.define(tag + '/neg', (n, p, length))
            minus_one = _minimal(self, absolute.shape, -1.0, tag + '/m1')
            self.emit(nnapi.MUL, [self.operand(absolute), self.operand(minus_one),
                                  self.scalar_i32(0)], negative)
            shifted = self.binary(nnapi.ADD, negative, one, (n, p, length), tag + '/shift')
            out = self.define(tag + '/relu', (n, p, length))
            self.emit(nnapi.RELU, [self.operand(shifted)], out)
            return out

        wx = weights(px, w, tag_p + '/wx')
        wy = weights(py, h, tag_p + '/wy')
        if not GS_FUSE_ALL_POINTS:
            for tensor in (wx, wy):
                self.last_use[tensor.name] = self.position
            self.flush(self.position)
        wxT = self.transposed(wx, (0, 2, 1), tag_p + '/wxT')
        wyT = self.transposed(wy, (0, 2, 1), tag_p + '/wyT')
        if not GS_FUSE_ALL_POINTS:
            self.last_use[wxT.name] = self.position
            self.last_use[wyT.name] = self.position

        accumulated = None
        for h0 in range(0, h, row_block):
            s = min(row_block, h - h0)
            tag = tag_p + f'/r{h0}'
            xs = _slice_t(self, x, (0, 0, h0, 0), (n, c, s, w), tag + '/xs')
            xr = self.reshaped(xs, (n, c * s, w), tag + '/xr')
            part = self.define(tag + '/part', (n, c * s, p))
            self.emit(nnapi.BATCH_MATMUL, [self.operand(xr), self.operand(wxT),
                                           self.cur.scalar_bool(False), self.cur.scalar_bool(False)], part)
            part4 = self.reshaped(part, (n, c, s, p), tag + '/part4')
            wys = _slice_t(self, wyT, (0, h0, 0), (n, s, p), tag + '/wys')
            wys4 = self.reshaped(wys, (n, 1, s, p), tag + '/wys4')
            weighted = self.binary(nnapi.MUL, part4, wys4, (n, c, s, p), tag + '/weighted')
            summed = self.define(tag + '/sum', (n, c, 1, p))
            self.emit(nnapi.REDUCE_SUM, [self.operand(weighted), self.cur.i32([2]),
                                         self.cur.scalar_bool(True)], summed)
            if accumulated is None:
                accumulated = summed
            else:
                accumulated = self.binary(nnapi.ADD, accumulated, summed, (n, c, 1, p),
                                          tag + '/acc')
            end_group = (GS_ROWS_PER_SUB > 0 and (h0 // row_block + 1) % GS_ROWS_PER_SUB == 0)
            if not GS_FUSE_ALL_POINTS and (end_group or h0 + s == h):
                self.last_use[accumulated.name] = self.position
                self.flush(self.position)
        pieces.append(accumulated)

    if len(pieces) == 1:
        merged = pieces[0]
    else:
        if not GS_FUSE_ALL_POINTS:
            for piece in pieces:
                self.last_use[piece.name] = self.position
        merged = self.define(node.name + '/joined', (n, c, 1, total))
        ins = [self.operand(piece) for piece in pieces]
        ins.append(self.scalar_i32(3))
        self.emit(nnapi.CONCATENATION, ins, merged)
        self.last_use[merged.name] = self.position + 1
        self.flush(self.position)
    if merged.buf is None:
        self.last_use[merged.name] = self.position + 1
        self.flush(self.position)
    out = self.define(node.output[0], (n, c, ho, wo))
    self.emit(nnapi.RESHAPE, [self.operand(merged), self.cur.i32([n, c, ho, wo])], out)


Lowerer.op_GridSample = _op_gridsample_chunked


# Keep spatial axes explicit when the final class-by-query product spans the
# 480x640 image. A single flattened dimension exceeds the MDLA dimension cap.
_matmul_before_wide = Lowerer.op_MatMul


def _op_matmul_wide(self, node):
    out_shape = tuple(self.shape_of(node.output[0]))
    if len(out_shape) != 3 or out_shape[-1] <= 65535:
        return _matmul_before_wide(self, node)
    a = self.tensor(node.input[0])
    b = self.tensor(node.input[1])
    if len(a.shape) != 3 or len(b.shape) != 4:
        raise ValueError(f'wide matmul requires spatial RHS: {a.shape}, {b.shape}')
    batch, channels, height, width = b.shape
    rows = a.shape[-2]
    step = max(1, min(65535, 2000000 // rows) // width)
    parts = []
    for start in range(0, height, step):
        size = min(step, height - start)
        name = node.name + f'/rows{start}'
        piece = _slice_t(self, b, (0, 0, start, 0), (batch, channels, size, width), name + '/slice')
        flat = self.reshaped(piece, (batch, channels, size * width), name + '/flat')
        product = self.define(name + '/mm', (batch, rows, size * width))
        self.emit(nnapi.BATCH_MATMUL,
                  [self.operand(a), self.operand(flat), self.cur.scalar_bool(False), self.cur.scalar_bool(False)],
                  product)
        spatial = self.reshaped(product, (batch, rows, size, width), name + '/spatial')
        self.last_use[spatial.name] = self.position + 1
        parts.append(spatial)
        self.flush(self.position)
    shape = (batch, rows, height, width)
    out = self.define(node.output[0], shape)
    self.emit(nnapi.CONCATENATION, [self.operand(t) for t in parts] + [self.scalar_i32(2)], out)
    self.shapes[node.output[0]] = shape


Lowerer.op_MatMul = _op_matmul_wide


_gemm_before_input_chunk = Lowerer.op_Gemm


def _op_gemm_input_chunk(self, node):
    data = self.tensor(node.input[0])
    if data.shape[-1] <= 4096:
        return _gemm_before_input_chunk(self, node)
    weight = _gemm_weight(self, node, self.tensor(node.input[1]))
    units, features = weight.shape
    rows = prod(data.shape) // features
    self.last_use[data.name] = max(self.last_use.get(data.name, -1), self.position)
    self.flush(self.position)
    flat = self.reshaped(data, (rows, features), node.name + '/input2d')
    zero = self.define(node.name + '/zero_bias', (units,), const=np.zeros(units, np.float16))
    total = None
    for start in range(0, features, 3072):
        size = min(3072, features-start)
        tag = node.name + f'/k{start}'
        piece = _slice_t(self, flat, (0, start), (rows, size), tag + '/slice')
        part_weight = np.ascontiguousarray(weight[:, start:start+size])
        wt = self.define(tag + '/weight', part_weight.shape, const=part_weight)
        part = self.define(tag + '/fc', (rows, units))
        self.emit(nnapi.FULLY_CONNECTED,
                  [self.operand(piece), self.operand(wt), self.operand(zero), self.scalar_i32(0)], part)
        total = part if total is None else self.binary(nnapi.ADD, total, part, part.shape, tag + '/sum')
    if len(node.input) > 2:
        bias = self.align(self.tensor(node.input[2]), (1, units))
        total = self.binary(nnapi.ADD, total, bias, (rows, units), node.name + '/bias')
    out = self.reshaped(total, self.shape_of(node.output[0]), node.output[0])
    self.tensors[node.output[0]] = out


Lowerer.op_Gemm = _op_gemm_input_chunk
