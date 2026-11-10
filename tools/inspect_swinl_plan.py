"""Read model metadata and operator counts without loading model weights."""
import argparse, collections, hashlib, importlib.util, json, struct
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('plan',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
spec=importlib.util.spec_from_file_location('nnapi',Path(__file__).parent/'swinl_plan_export/nnapi.py')
nnapi=importlib.util.module_from_spec(spec);spec.loader.exec_module(nnapi)
names={v:k for k,v in list(vars(nnapi).items())[list(vars(nnapi)).index('ADD'):] if k.isupper() and isinstance(v,int)}
ops=collections.Counter();types=collections.Counter();subcounts=[];initbytes=0
with a.plan.open('rb') as f:
    def scalar(code):return struct.unpack('<'+code,f.read(struct.calcsize('<'+code)))[0]
    def u32():return scalar('I')
    def indices():return [u32() for _ in range(u32())]
    magic=u32();version=u32();assert magic==nnapi.MAGIC
    buffers=[]
    for _ in range(u32()):
        name=f.read(u32()).decode();dtype=scalar('i');dims=indices();buffers.append({'name':name,'dtype':dtype,'shape':dims})
    if version==2:
        for _ in range(u32()):
            binding=u32();length=scalar('Q');f.seek(length,1);initbytes+=length
    for _ in range(u32()):
        for _ in range(u32()):
            dtype=scalar('i');types[dtype]+=1;dims=indices();f.seek(scalar('Q'),1);binding=u32()
        n=u32();subcounts.append(n)
        for _ in range(n):
            op=scalar('i');ops[names.get(op,str(op))]+=1;indices();indices()
        indices();indices()
    results=indices();assert not f.read(1)
with a.plan.open('rb') as f: md5=hashlib.file_digest(f,'md5').hexdigest()
result={'file':str(a.plan),'bytes':a.plan.stat().st_size,'decimal_mb':a.plan.stat().st_size/1e6,
 'mib':a.plan.stat().st_size/1024**2,'md5':md5,'container_version':version,'subgraphs':len(subcounts),
 'operations':sum(subcounts),'operator_counts':dict(ops.most_common()),'operand_type_counts':dict(types),
 'initializer_bytes':initbytes,'output_buffers':[buffers[i] for i in results]}
a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False,indent=2))
