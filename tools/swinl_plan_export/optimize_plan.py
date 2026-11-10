"""Remove dead lowering artifacts and fold byte-preserving constant views."""
import argparse
import collections
import hashlib
import json
import pathlib
import numpy as np
import nnapi as n


def simplify(plan,uniform_constants=True,broadcast_constants=False):
    before=sum(len(s.ops) for s in plan.subs)
    def compact(sub):
        live=set(sub.outputs);operations=[]
        for code,ins,outs in reversed(sub.ops):
            if not live.intersection(outs):continue
            operations.append((code,ins,outs));live.update(ins)
        sub.ops=list(reversed(operations))
        sub.inputs=[v for v in sub.inputs if v in live]
        order=sorted(live);mapping={v:i for i,v in enumerate(order)}
        sub.values=[sub.values[v] for v in order]
        sub.ops=[(c,[mapping[v] for v in ins],[mapping[v] for v in outs]) for c,ins,outs in sub.ops]
        sub.inputs=[mapping[v] for v in sub.inputs];sub.outputs=[mapping[v] for v in sub.outputs]
    folded=0;uniform=0;broadcast_tiles=0
    for sub in plan.subs:
        compact(sub)
        producers={v:(code,ins) for code,ins,outs in sub.ops for v in outs}
        operations=[]
        for code,ins,outs in sub.ops:
            ins=list(ins)
            if broadcast_constants and code in (n.ADD,n.SUB,n.MUL,n.DIV,n.MAXIMUM,n.MINIMUM):
                for slot in (0,1):
                    producer=producers.get(ins[slot])
                    if not producer or producer[0]!=n.TILE:continue
                    source=producer[1][0]
                    dtype,dims,data,_=sub.values[source]
                    other=sub.values[ins[1-slot]][1];target=sub.values[outs[0]][1]
                    if dtype==n.TENSOR_FLOAT16 and data is not None and dims[-1]==target[-1] and np.broadcast_shapes(dims,other)==target:
                        ins[slot]=source;broadcast_tiles+=1
            operations.append((code,ins,outs))
        sub.ops=operations;compact(sub)
        operations=[]
        constants={}
        for code,ins,outs in sub.ops:
            ins=list(ins)
            if uniform_constants and code in (n.ADD,n.SUB,n.MUL,n.DIV,n.MAXIMUM,n.MINIMUM) and len(outs)==1:
                for slot in (0,1):
                    dtype,dims,data,_=sub.values[ins[slot]]
                    if dtype!=n.TENSOR_FLOAT16 or data is None or len(data)<=2:continue
                    bits=np.frombuffer(data,np.uint16)
                    if not np.all(bits==bits[0]):continue
                    other=sub.values[ins[1-slot]][1];outshape=sub.values[outs[0]][1]
                    rank=max(len(dims),len(other));smallshape=(1,)*rank
                    key=(smallshape,data[:2])
                    if key not in constants:constants[key]=sub.val(dtype,smallshape,const=data[:2])
                    ins[slot]=constants[key];uniform+=1
                    broadcast=(1,)*(rank-len(other))+tuple(other)
                    if broadcast!=outshape:
                        assert len(outshape)==rank and all(a==b or b==1 for a,b in zip(outshape,broadcast))
                        intermediate=sub.val(dtype,broadcast)
                        operations.append((code,ins,[intermediate]))
                        reps=[a//b for a,b in zip(outshape,broadcast)]
                        operations.append((n.TILE,[intermediate,sub.i32(reps)],outs))
                        break
                else:
                    operations.append((code,ins,outs));continue
                continue
            operations.append((code,ins,outs))
        sub.ops=operations;compact(sub)
        operations=[]
        for code,ins,outs in sub.ops:
            # Only view and permutation operations: retain every arithmetic
            # operation on MDLA to preserve the validated FP16 rounding.
            can_fold=code in (n.RESHAPE,n.TRANSPOSE,n.SLICE) and len(outs)==1
            can_fold=can_fold and outs[0] not in sub.outputs and all(sub.values[v][2] is not None for v in ins)
            if not can_fold:operations.append((code,ins,outs));continue
            dtype,dims,data,_=sub.values[ins[0]]
            dt={n.TENSOR_FLOAT16:np.float16,n.TENSOR_FLOAT32:np.float32,n.TENSOR_INT32:np.int32}.get(dtype)
            if dt is None:operations.append((code,ins,outs));continue
            array=np.frombuffer(data,dt).reshape(dims)
            if code==n.RESHAPE:array=array.reshape(sub.values[outs[0]][1])
            elif code==n.TRANSPOSE:array=array.transpose(np.frombuffer(sub.values[ins[1]][2],np.int32))
            else:
                start=np.frombuffer(sub.values[ins[1]][2],np.int32)
                count=np.frombuffer(sub.values[ins[2]][2],np.int32)
                array=array[tuple(slice(int(a),None if b==-1 else int(a+b)) for a,b in zip(start,count))]
            odtype,oshape,_,binding=sub.values[outs[0]]
            assert array.shape==oshape and dtype==odtype
            sub.values[outs[0]]=(odtype,oshape,np.ascontiguousarray(array).tobytes(),binding)
            folded+=1
        sub.ops=operations;compact(sub)
    used={s.values[v][3] for s in plan.subs for v in s.inputs}
    plan.initializers={b:data for b,data in plan.initializers.items() if b in used}
    return dict(before_ops=before,after_ops=sum(len(s.ops) for s in plan.subs),constant_views=folded,
                uniform_binary_constants=uniform,broadcast_constant_tiles=broadcast_tiles)


if __name__=='__main__':
    from plan_tools import read
    from dynamic_fc import convert
    from fuse_plan import merge
    from check_ops2 import check_plan
    p=argparse.ArgumentParser()
    p.add_argument('input');p.add_argument('output')
    p.add_argument('--merge',type=int,default=1)
    p.add_argument('--max-ops',type=int,default=400)
    p.add_argument('--shared-matmul',action='store_true')
    p.add_argument('--no-uniform',action='store_true')
    p.add_argument('--broadcast-constants',action='store_true')
    args=p.parse_args();plan=read(args.input)
    stats={'simplify':simplify(plan,not args.no_uniform,args.broadcast_constants)}
    stats['shared']=convert(plan,dynamic_affine=True,dynamic_matmul=args.shared_matmul)
    stats['fusion']=merge(plan,args.merge,args.max_ops)
    stats['final_simplify']=simplify(plan,not args.no_uniform,args.broadcast_constants)
    errors=check_plan(plan)
    if errors:raise ValueError(errors[:8])
    stats.update(subgraphs=len(plan.subs),operations=sum(len(s.ops) for s in plan.subs),bytes=plan.write(args.output))
    with open(args.output,'rb') as f:stats['md5']=hashlib.file_digest(f,'md5').hexdigest()
    pathlib.Path(args.output).with_suffix('.manifest.json').write_text(json.dumps(stats,indent=2))
    print(json.dumps(stats,indent=2))
