"""Candidate-only operators; no changes to the validated 640x480 lowerer."""
import numpy as np
import lower
import nnapi

def install():
    old_binary = lower.Lowerer.binary
    def binary(self, code, a, b, shape, name, **kwargs):
        if a.const is not None and b.const is not None:
            operations={nnapi.ADD:np.add,nnapi.SUB:np.subtract,nnapi.MUL:np.multiply,nnapi.DIV:np.divide,nnapi.MAXIMUM:np.maximum,nnapi.MINIMUM:np.minimum}
            if code in operations:
                data=np.asarray(operations[code](a.const,b.const),np.float16)
                data=np.broadcast_to(data,shape).copy()
                return self.define(name,shape,const=data)
        return old_binary(self,code,a,b,shape,name,**kwargs)
    lower.Lowerer.binary=binary

    # The MDLA firmware rejects batch broadcasting in BATCH_MATMUL. The
    # reference graph permits it for fixed attention weights; materialize the
    # small constant across the batch dimensions in this candidate only.
    old_matmul = lower.Lowerer.op_MatMul
    def op_matmul(self, node):
        a = self.tensor(node.input[0])
        b = self.tensor(node.input[1])
        if b.const is not None and len(a.shape) == len(b.shape) and len(a.shape) >= 3:
            target = tuple(max(x,y) for x,y in zip(a.shape[:-2], b.shape[:-2])) + tuple(b.shape[-2:])
            if target[:-2] != b.shape[:-2] and all(x == y or y == 1 for x,y in zip(a.shape[:-2], b.shape[:-2])):
                expanded = np.ascontiguousarray(np.broadcast_to(np.asarray(b.const).reshape(b.shape), target))
                replacement = self.define(node.input[1]+'/batch_expanded', target, const=expanded)
                original = self.tensor
                self.tensor = lambda name: replacement if name == node.input[1] else original(name)
                try:
                    return old_matmul(self, node)
                finally:
                    self.tensor = original
        return old_matmul(self, node)
    lower.Lowerer.op_MatMul = op_matmul

    def op_and(self, node):
        self.elementwise(nnapi.MUL, node)

    def op_not(self, node):
        data = self.tensor(node.input[0])
        shape = self.shape_of(node.output[0])
        one = self.define(node.output[0]+'/one', (1,)*len(shape), const=np.ones((1,)*len(shape), np.float16))
        result = self.binary(nnapi.SUB, one, data, shape, node.output[0])
        self.tensors[node.output[0]] = result

    def op_equal(self, node):
        a, b = (self.tensor(x) for x in node.input)
        shape = self.shape_of(node.output[0])
        rank = len(shape)
        a = self.align(a, self.broadcast_shape(a.shape, rank))
        b = self.align(b, self.broadcast_shape(b.shape, rank))
        diff = self.binary(nnapi.SUB, a, b, shape, node.output[0]+'/diff')
        absolute = self.define(node.output[0]+'/absolute', shape)
        self.emit(nnapi.ABS, [self.operand(diff)], absolute)
        one = self.define(node.output[0]+'/one', (1,)*rank, const=np.ones((1,)*rank, np.float16))
        # Equal is used for integral attention-mask counts only.
        clipped = self.binary(nnapi.MINIMUM, absolute, one, shape, node.output[0]+'/different', fuse=False)
        self.binary(nnapi.SUB, one, clipped, shape, node.output[0])

    old_cast = lower.Lowerer.op_Cast
    def op_cast(self, node):
        target = int(self.attrs(node)['to'])
        data = self.tensor(node.input[0])
        if target == 6 and data.dtype == nnapi.TENSOR_INT32:
            self.tensors[node.output[0]] = data
        elif target == 7:  # BOOL -> INT64 for a small, integral mask count.
            assert self.dtypes.get(node.input[0]) == 9, (node.name, self.dtypes.get(node.input[0]))
            self.tensors[node.output[0]] = data
        else:
            old_cast(self, node)

    old_where = lower.Lowerer.op_Where
    def op_where(self, node):
        # Existing attention lowering already uses this finite FP16 sentinel.
        # Apply it to the Hugging Face export's corresponding masked logits.
        for name in node.input[1:]:
            value = self.tensor(name)
            if value.const is not None and np.isneginf(value.const).all():
                value.const = np.full(value.const.shape, -10000, np.float16)
        old_where(self, node)

    lower.Lowerer.op_And = op_and
    lower.Lowerer.op_Not = op_not
    lower.Lowerer.op_Equal = op_equal
    lower.Lowerer.op_Cast = op_cast
    lower.Lowerer.op_Where = op_where
