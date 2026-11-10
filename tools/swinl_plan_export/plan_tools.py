"""Extract compact probe plans without allocating unrelated full-network buffers."""
import nnapi
import struct


def read(path):
    """Read the v1/v2 container for lossless offline optimization and inspection."""
    plan = nnapi.Plan()
    with open(path, 'rb') as stream:
        def scalar(code):
            size = struct.calcsize(code)
            data = stream.read(size)
            if len(data) != size: raise ValueError('Truncated plan')
            return struct.unpack(code, data)[0]
        def u32(): return scalar('<I')
        def indices(): return [u32() for _ in range(u32())]
        magic,version = u32(),u32()
        if magic!=nnapi.MAGIC or version not in (1,2): raise ValueError('Unsupported plan')
        for _ in range(u32()):
            name = stream.read(u32()).decode()
            dtype = scalar('<i');dims=indices()
            plan.buffer(name,dtype,dims)
        if version==2:
            for _ in range(u32()):
                binding=u32();length=scalar('<Q')
                plan.initializers[binding]=stream.read(length)
                if len(plan.initializers[binding])!=length: raise ValueError('Truncated initializer')
        for _ in range(u32()):
            sub=nnapi.Sub(plan)
            for _ in range(u32()):
                dtype=scalar('<i');dims=indices();length=scalar('<Q')
                data=stream.read(length)
                if len(data)!=length:raise ValueError('Truncated constant')
                binding=u32()
                sub.val(dtype,dims,const=data if length else None,
                        binding=None if binding==0xffffffff else binding)
            for _ in range(u32()):
                code=scalar('<i');ins=indices();outs=indices()
                sub.op(code,ins,outs)
            sub.inputs=indices();sub.outputs=indices()
            plan.add_sub(sub)
        plan.results=indices()
        if stream.read(1):raise ValueError('Unexpected trailing data')
    return plan


def select_subgraphs(plan, indices):
    result = nnapi.Plan()
    mapping = {}
    for index in indices:
        source = plan.subs[index]
        sub = nnapi.Sub(result)
        for dtype, shape, constant, binding in source.values:
            if binding is not None:
                if binding not in mapping:
                    mapping[binding] = result.buffer(*plan.buffers[binding])
                    if binding in plan.initializers:
                        result.initializers[mapping[binding]] = plan.initializers[binding]
                binding = mapping[binding]
            sub.values.append((dtype, shape, constant, binding))
        sub.ops = list(source.ops)
        sub.inputs = list(source.inputs)
        sub.outputs = list(source.outputs)
        result.add_sub(sub)
    return result
