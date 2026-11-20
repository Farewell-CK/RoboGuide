"""Make shape-only boundary models share their producer's existing allocation."""
import argparse
import json
import hashlib
import pathlib
import numpy as np
import nnapi as n
from plan_tools import read
from check_ops2 import check_plan


def alias(plan):
    aliases={};removed=[];kept=[]
    def root(binding):
        while binding in aliases:binding=aliases[binding]
        return binding
    for index,sub in enumerate(plan.subs):
        sub.values=[(t,sh,data,root(b)) if b is not None else (t,sh,data,b) for t,sh,data,b in sub.values]
        if len(sub.ops)==1 and len(sub.inputs)==len(sub.outputs)==1:
            code,ins,outs=sub.ops[0];source=sub.inputs[0];target=sub.outputs[0]
            st,sh,_,sb=sub.values[source];tt,th,_,tb=sub.values[target]
            valid=(st==tt and sb is not None and tb is not None and ins[0]==source and outs[0]==target and n.nbytes(st,sh)==n.nbytes(tt,th))
            is_view=code==n.RESHAPE
            if code==n.TRANSPOSE:
                perm=np.frombuffer(sub.values[ins[1]][2],np.int32)
                is_view=[int(i) for i in perm if sh[i]!=1]==[i for i,d in enumerate(sh) if d!=1]
            if valid and is_view:
                aliases[tb]=sb;removed.append(dict(sub=index,source=sh,target=th,bytes=n.nbytes(st,sh)));continue
        kept.append(sub)
    plan.subs=kept;plan.results=[root(b) for b in plan.results]
    return dict(removed=len(removed),copy_bytes=sum(q['bytes'] for q in removed),views=removed)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('input');p.add_argument('output');a=p.parse_args()
    plan=read(a.input);stats=alias(plan);errors=check_plan(plan)
    if errors:raise ValueError(errors[:8])
    stats.update(subgraphs=len(plan.subs),operations=sum(len(s.ops) for s in plan.subs),bytes=plan.write(a.output))
    with open(a.output,'rb') as f:stats['md5']=hashlib.file_digest(f,'md5').hexdigest()
    pathlib.Path(a.output).with_suffix('.manifest.json').write_text(json.dumps(stats,indent=2))
    print({k:v for k,v in stats.items() if k!='views'})
