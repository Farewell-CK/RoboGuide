"""Expose FC weights as shared inputs to reusable BATCH_MATMUL templates."""
import hashlib
import numpy as np
import nnapi as n

def convert(plan, dynamic_affine=False, dynamic_matmul=False):
    weights = {(tuple(plan.buffers[b][2]),hashlib.sha256(data).digest()):b
               for b,data in plan.initializers.items() if plan.buffers[b][1]==n.TENSOR_FLOAT16}
    converted = 0
    def binding(array):
        array = np.ascontiguousarray(array, dtype=np.float16)
        data = array.tobytes()
        key = (array.shape, hashlib.sha256(data).digest())
        if key not in weights:
            b = plan.buffer('static_' + str(len(weights)), n.TENSOR_FLOAT16, array.shape)
            plan.initializers[b] = data
            weights[key] = b
        return weights[key]
    for sub in plan.subs:
        operations = []
        for code, ins, outs in sub.ops:
            if code != n.FULLY_CONNECTED:
                operations.append((code,ins,outs)); continue
            x,w,b,fuse = ins
            _,xd,_,_ = sub.values[x]
            _,wd,wdata,_ = sub.values[w]
            _,bd,bdata,_ = sub.values[b]
            od = sub.values[outs[0]][1]
            activation = np.frombuffer(sub.values[fuse][2],np.int32)[0]
            if (len(xd)!=2 or len(wd)!=2 or wdata is None or bdata is None
                    or activation!=0 or tuple(od)!=(xd[0],wd[0])):
                operations.append((code,ins,outs)); continue
            rows,k = xd; units = wd[0]
            if k != wd[1]: raise ValueError('FC input width')
            wi = np.frombuffer(wdata,np.float16).reshape(wd).T.reshape(1,k,units)
            bi = np.frombuffer(bdata,np.float16).reshape(1,1,units)
            dynw = sub.val(n.TENSOR_FLOAT16,wi.shape,binding=binding(wi),as_input=True)
            dynb = sub.val(n.TENSOR_FLOAT16,bi.shape,binding=binding(bi),as_input=True)
            x3 = sub.val(n.TENSOR_FLOAT16,(1,rows,k))
            operations.append((n.RESHAPE,[x,sub.i32([1,rows,k])],[x3]))
            mm = sub.val(n.TENSOR_FLOAT16,(1,rows,units))
            operations.append((n.BATCH_MATMUL,[x3,dynw,sub.scalar_bool(False),sub.scalar_bool(False)],[mm]))
            plus = sub.val(n.TENSOR_FLOAT16,(1,rows,units))
            operations.append((n.ADD,[mm,dynb,sub.scalar_i32(0)],[plus]))
            operations.append((n.RESHAPE,[plus,sub.i32(list(od))],outs))
            converted += 1
        sub.ops = operations
        # Remove obsolete embedded weight operands before NNAPI model creation.
        used = set(sub.inputs + sub.outputs)
        used.update(v for _,ins,outs in operations for v in ins+outs)
        order = sorted(used)
        remap = {v:i for i,v in enumerate(order)}
        sub.values = [sub.values[v] for v in order]
        sub.ops = [(c,[remap[i] for i in ins],[remap[o] for o in outs]) for c,ins,outs in operations]
        sub.inputs = [remap[i] for i in sub.inputs]
        sub.outputs = [remap[i] for i in sub.outputs]
    affine = 0
    if dynamic_affine:
        for sub in plan.subs:
            uses={}
            for code,ins,_ in sub.ops:
                for index in ins:uses.setdefault(index,set()).add(code)
            for index,(dtype,dims,data,old_binding) in enumerate(list(sub.values)):
                if dtype!=n.TENSOR_FLOAT16 or data is None or len(data)<=2:continue
                if not uses.get(index) or not uses[index].issubset({n.ADD,n.MUL,n.SUB,n.DIV}):continue
                vector = dims[-1]>1 and int(np.prod(dims[:-1]))==1 and len(data)<=8192
                relative_bias = len(dims)==4 and dims[-2:]==(144,144) and len(data)<=4*1024*1024
                exact_shape = dims[-1]>1 and len(data)<=32*1024*1024
                if exact_shape:
                    for code,ins,_ in sub.ops:
                        if index not in ins[:2]:continue
                        other=ins[1] if ins[0]==index else ins[0]
                        if sub.values[other][1][-1]!=dims[-1]:exact_shape=False;break
                if not (vector or relative_bias or exact_shape):continue
                b=binding(np.frombuffer(data,np.float16).reshape(dims))
                sub.values[index]=(dtype,dims,None,b)
                if index not in sub.inputs:sub.inputs.append(index)
                affine+=1
    matmul=0
    if dynamic_matmul:
        for sub in plan.subs:
            candidates={v for code,ins,_ in sub.ops if code==n.BATCH_MATMUL for v in ins[:2]}
            for index in sorted(candidates):
                dtype,dims,data,_=sub.values[index]
                if dtype!=n.TENSOR_FLOAT16 or data is None or len(data)>32*1024*1024:continue
                b=binding(np.frombuffer(data,np.float16).reshape(dims))
                sub.values[index]=(dtype,dims,None,b)
                if index not in sub.inputs:sub.inputs.append(index)
                matmul+=1
    return dict(converted_fc=converted,dynamic_affine_operands=affine,dynamic_matmul_operands=matmul,
                static_buffers=len(weights),static_bytes=sum(map(len,plan.initializers.values())))
