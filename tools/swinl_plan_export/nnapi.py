"""Minimal writer for the Swin-L NNAPI plan container consumed by mdla_plan.cpp."""
import struct

# ANeuralNetworksOperandCode
FLOAT32 = 0
INT32 = 1
TENSOR_FLOAT32 = 3
TENSOR_INT32 = 4
BOOL = 6
TENSOR_FLOAT16 = 8
TENSOR_BOOL8 = 9
FLOAT16 = 10

# ANeuralNetworksOperationType (subset used by the lowering)
ADD = 0
CONCATENATION = 2
CONV_2D = 3
FLOOR = 8
FULLY_CONNECTED = 9
LOGISTIC = 14
MUL = 18
RELU = 19
RESHAPE = 22
RESIZE_BILINEAR = 23
SOFTMAX = 25
TANH = 28
DIV = 30
MEAN = 31
PAD = 32
PAD_V2 = 69
SQUEEZE = 34
STRIDED_SLICE = 35
SUB = 36
TRANSPOSE = 37
ABS = 38
ARGMAX = 39
CAST = 45
EXP = 49
EXPAND_DIMS = 50
MAXIMUM = 65
MINIMUM = 66
NEG = 67
REDUCE_MAX = 77
REDUCE_SUM = 80
RSQRT = 83
SELECT = 84
SLICE = 86
SPLIT = 87
TILE = 89
BATCH_MATMUL = 102
GATHER = 51

MAGIC = 0x4E414C50


def dtype_width(dtype):
    return 2 if dtype == TENSOR_FLOAT16 else 4 if dtype in (TENSOR_FLOAT32, TENSOR_INT32) else 1


def nbytes(dtype, dims):
    n = 1
    for d in dims:
        n *= d
    return n * dtype_width(dtype)


class Plan:
    def __init__(self):
        self.buffers = []   # (name, dtype, dims)
        self.subs = []
        self.results = []
        self.initializers = {}  # Global input buffers preloaded from plan v2.

    def buffer(self, name, dtype, dims):
        self.buffers.append((name, dtype, tuple(dims)))
        return len(self.buffers) - 1

    def add_sub(self, sub):
        self.subs.append(sub)
        return len(self.subs) - 1

    def write(self, path):
        out = bytearray()
        out += struct.pack('<II', MAGIC, 2 if self.initializers else 1)
        out += struct.pack('<I', len(self.buffers))
        for name, dtype, dims in self.buffers:
            raw = name.encode()
            out += struct.pack('<I', len(raw)) + raw
            out += struct.pack('<iI', dtype, len(dims))
            for d in dims:
                out += struct.pack('<I', int(d))
        if self.initializers:
            out += struct.pack('<I', len(self.initializers))
            for binding, data in sorted(self.initializers.items()):
                out += struct.pack('<IQ', binding, len(data)) + data
        out += struct.pack('<I', len(self.subs))
        for sub in self.subs:
            out += struct.pack('<I', len(sub.values))
            for dtype, dims, const, binding in sub.values:
                out += struct.pack('<iI', dtype, len(dims))
                for d in dims:
                    out += struct.pack('<I', d)
                data = const if const is not None else b
                out += struct.pack('<Q', len(data)) + data
                out += struct.pack('<I', 0xFFFFFFFF if binding is None else binding)
            out += struct.pack('<I', len(sub.ops))
            for code, ins, outs in sub.ops:
                out += struct.pack('<iI', code, len(ins))
                for i in ins:
                    out += struct.pack('<I', i)
                out += struct.pack('<I', len(outs))
                for o in outs:
                    out += struct.pack('<I', o)
            out += struct.pack('<I', len(sub.inputs))
            for i in sub.inputs:
                out += struct.pack('<I', i)
            out += struct.pack('<I', len(sub.outputs))
            for o in sub.outputs:
                out += struct.pack('<I', o)
        out += struct.pack('<I', len(self.results))
        for r in self.results:
            out += struct.pack('<I', r)
        with open(path, 'wb') as handle:
            handle.write(out)
        return len(out)


class Sub:
    """One NNAPI model: values + ops + IO indices."""

    def __init__(self, plan=None):
        self.plan = plan
        self.values = []    # (dtype, dims, const_bytes, binding)
        self.ops = []
        self.inputs = []
        self.outputs = []

    def val(self, dtype, dims, const=None, binding=None, as_input=False):
        idx = len(self.values)
        self.values.append((dtype, tuple(dims), const, binding))
        if as_input:
            self.inputs.append(idx)
        return idx

    def bind(self, name, dtype, dims, as_input=False):
        """Value backed by a named global buffer (host allocation)."""
        binding = self.plan.buffer(name, dtype, dims)
        if name.startswith('in.'):
            as_input = True
        return self.val(dtype, dims, binding=binding, as_input=as_input)

    def op(self, code, ins, outs):
        outs = list(outs) if isinstance(outs, (list, tuple)) else [outs]
        for value in list(ins) + outs:
            if not isinstance(value, int):
                raise TypeError(f'op {code} operand {value!r} is not an index')
        self.ops.append((code, list(ins), outs))
        return outs[0]

    def out(self, *ids):
        self.outputs.extend(ids)

    # ---- convenience: INT32 constants -------------------------------------
    def i32(self, values, dims=None):
        import numpy as np
        arr = np.asarray(values, dtype=np.int32)
        if dims is None:
            dims = arr.shape if arr.ndim else ()
        return self.val(TENSOR_INT32, dims, const=arr.tobytes())

    def scalar_i32(self, value):
        import numpy as np
        return self.val(INT32, (), const=np.int32(value).tobytes())

    def scalar_bool(self, value):
        return self.val(BOOL, (), const=bytes([1 if value else 0]))

    def scalar_f16(self, value):
        import numpy as np
        return self.val(FLOAT16, (), const=np.float16(value).tobytes())

    def scalar_f32(self, value):
        import numpy as np
        return self.val(FLOAT32, (), const=np.float32(value).tobytes())

    # ---- tensor constants -------------------------------------------------
    def const(self, dtype, dims, array=None):
        import numpy as np
        dims = tuple(dims)
        count = 1
        for d in dims:
            count *= d
        if array is None:
            phase = np.arange(count, dtype=np.float32) % 97
            array = (0.05 + 0.3 * np.sin(phase * 0.07)).astype(np.float32)
        np_dtype = {TENSOR_FLOAT16: np.float16, TENSOR_FLOAT32: np.float32,
                    TENSOR_INT32: np.int32, TENSOR_BOOL8: np.uint8}[dtype]
        data = np.asarray(array, dtype=np_dtype).reshape(dims).tobytes()
        return self.val(dtype, dims, const=data)

    def const_f16(self, dims, array=None):
        return self.const(TENSOR_FLOAT16, dims, array)

# ---------------------------------------------------------------------------
def _write_int(self, path):
    import struct as _s
    out = bytearray()

    def u32(value):
        out.extend(_s.pack('<I', int(value)))

    def i32(value):
        out.extend(_s.pack('<i', int(value)))

    def u64(value):
        out.extend(_s.pack('<Q', int(value)))

    u32(MAGIC)
    u32(2 if self.initializers else 1)
    u32(len(self.buffers))
    for name, dtype, dims in self.buffers:
        raw = name.encode()
        u32(len(raw))
        out.extend(raw)
        i32(dtype)
        u32(len(dims))
        for d in dims:
            u32(d)
    if self.initializers:
        u32(len(self.initializers))
        for binding, data in sorted(self.initializers.items()):
            u32(binding)
            u64(len(data))
            out.extend(data)
    u32(len(self.subs))
    for sub in self.subs:
        u32(len(sub.values))
        for dtype, dims, const, binding in sub.values:
            i32(dtype)
            u32(len(dims))
            for d in dims:
                u32(d)
            data = const if const is not None else b''
            u64(len(data))
            out.extend(data)
            u32(0xFFFFFFFF if binding is None else binding)
        u32(len(sub.ops))
        for code, ins, outs in sub.ops:
            i32(code)
            u32(len(ins))
            for i in ins:
                u32(i)
            u32(len(outs))
            for o in outs:
                u32(o)
        u32(len(sub.inputs))
        for i in sub.inputs:
            u32(i)
        u32(len(sub.outputs))
        for o in sub.outputs:
            u32(o)
    u32(len(self.results))
    for r in self.results:
        u32(r)
    with open(path, 'wb') as handle:
        handle.write(out)
    return len(out)


Plan.write = _write_int
