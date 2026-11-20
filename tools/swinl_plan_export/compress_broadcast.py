"""Remove exact repeated leading axes from static binary operands."""
import argparse
import hashlib
import json
import pathlib
import numpy as np
import nnapi as n
from plan_tools import read
from optimize_plan import simplify
from check_ops2 import check_plan


def compress(plan):
    keys={(plan.buffers[b][2],hashlib.sha256(data).digest()):b for b,data in plan.initializers.items() if plan.buffers[b][1]==n.TENSOR_FLOAT16}
    converted=[]
    for si,sub in enumerate(plan.subs):
        uses={}
        for code,ins,outs in sub.ops:
            for v in ins:uses.setdefault(v,[]).append((code,ins,outs))
        for vi,(dtype,dims,literal,binding) in enumerate(list(sub.values)):
            data=literal if literal is not None else plan.initializers.get(binding)
            consumers=uses.get(vi,[])
            if dtype!=n.TENSOR_FLOAT16 or data is None or len(data)<32768 or len(dims)<2 or not consumers:continue
            if any(c not in (n.ADD,n.SUB,n.MUL,n.DIV,n.MAXIMUM,n.MINIMUM) or vi not in ins[:2] for c,ins,_ in consumers):continue
            array=np.frombuffer(data,np.float16).reshape(dims)
            for axis in range(len(dims)-1):
                if array.shape[axis]<=1:continue
                candidate=np.take(array,[0],axis=axis)
                if np.array_equal(np.broadcast_to(candidate,array.shape),array):array=candidate
            if array.shape==dims:continue
            if any(np.broadcast_shapes(array.shape,sub.values[ins[1] if ins[0]==vi else ins[0]][1])!=sub.values[outs[0]][1] for _,ins,outs in consumers):continue
            payload=np.ascontiguousarray(array).tobytes();key=(array.shape,hashlib.sha256(payload).digest())
            if key not in keys:
                b=plan.buffer('broadcast_static_'+str(len(keys)),n.TENSOR_FLOAT16,array.shape)
                keys[key]=b;plan.initializers[b]=payload
            sub.values[vi]=(dtype,array.shape,None,keys[key])
            if vi not in sub.inputs:sub.inputs.append(vi)
            converted.append(dict(sub=si,value=vi,before=dims,after=array.shape,operand_bytes_saved=len(data)-len(payload)))
    simplify(plan)
    return dict(converted=converted,static_bytes=sum(map(len,plan.initializers.values())))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('input');p.add_argument('output');a=p.parse_args()
    plan=read(a.input);stats=compress(plan)
    errors=check_plan(plan)
    if errors:raise ValueError(errors[:8])
    stats.update(subgraphs=len(plan.subs),operations=sum(len(s.ops) for s in plan.subs),bytes=plan.write(a.output))
    with open(a.output,'rb') as f:stats['md5']=hashlib.file_digest(f,'md5').hexdigest()
    pathlib.Path(a.output).with_suffix('.manifest.json').write_text(json.dumps(stats,indent=2))
    print({k:v for k,v in stats.items() if k!='converted'},'compressed_operands',len(stats['converted']))
