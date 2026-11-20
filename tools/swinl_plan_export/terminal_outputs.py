"""Export terminal producer values directly when MDLA's IO rule permits it."""
import argparse
import collections
import hashlib
import json
import pathlib
import numpy as np
import nnapi as n
from plan_tools import read
from optimize_plan import simplify
from check_ops2 import check_plan


def remove(plan):
    changes=[]
    for si,sub in enumerate(plan.subs):
        consumers=collections.Counter(v for _,ins,_ in sub.ops for v in ins)
        producers={v:(oi,code,ins) for oi,(code,ins,outs) in enumerate(sub.ops) for v in outs}
        removed=set()
        for position,output in enumerate(list(sub.outputs)):
            oi,code,ins=producers[output]
            dtype,dims,_,binding=sub.values[output]
            source=None
            if code==n.ADD and dtype==n.TENSOR_FLOAT16 and np.frombuffer(sub.values[ins[2]][2],np.int32)[0]==0:
                for slot in (0,1):
                    t,_,data,_=sub.values[ins[slot]]
                    if t==dtype and data is not None and not np.frombuffer(data,np.float16).any():source=ins[1-slot];break
            elif code==n.RESHAPE and sub.values[ins[0]][1]==dims:source=ins[0]
            if source is None or consumers[source]!=1 or source in sub.inputs or source in sub.outputs:continue
            st,sh,data,old_binding=sub.values[source]
            if st!=dtype or sh!=dims or data is not None or old_binding is not None:continue
            sub.values[source]=(st,sh,None,binding)
            sub.outputs[position]=source;removed.add(oi)
            changes.append(dict(sub=si,shape=dims,bytes=n.nbytes(dtype,dims)))
        sub.ops=[op for i,op in enumerate(sub.ops) if i not in removed]
    simplify(plan)
    return dict(removed=len(changes),copy_bytes=sum(c['bytes'] for c in changes),changes=changes)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('input');p.add_argument('output');a=p.parse_args()
    plan=read(a.input);stats=remove(plan);errors=check_plan(plan)
    if errors:raise ValueError(errors[:8])
    stats.update(subgraphs=len(plan.subs),operations=sum(len(s.ops) for s in plan.subs),bytes=plan.write(a.output))
    with open(a.output,'rb') as f:stats['md5']=hashlib.file_digest(f,'md5').hexdigest()
    pathlib.Path(a.output).with_suffix('.manifest.json').write_text(json.dumps(stats,indent=2))
    print({k:v for k,v in stats.items() if k!='changes'})
