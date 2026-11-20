"""Extract only navigation data from a local diagnostic heap; never print other app values."""
import argparse, collections, json, struct
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('heap',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
data=a.heap.read_bytes();view=memoryview(data);offset=data.index(b'\0')+1
id_size=struct.unpack_from('>I',data,offset)[0];offset+=12
width={2:id_size,4:1,5:2,6:4,7:8,8:1,9:2,10:4,11:8};formats={4:'?',5:'H',6:'f',7:'d',8:'b',9:'h',10:'i',11:'q'}
strings={};class_names={};classes={};objects={};arrays={}
def u(pos,n):return int.from_bytes(view[pos:pos+n],'big')
def identifier(pos):return u(pos,id_size)
def heap(start,end):
    q=start
    roots={0xff:id_size,1:id_size*2,2:id_size+8,3:id_size+8,4:id_size+4,5:id_size,6:id_size+4,7:id_size,8:id_size+8,
           0x89:id_size,0x8a:id_size,0x8b:id_size,0x8c:id_size,0x8d:id_size,0x8e:id_size+8,0x90:id_size,0xfe:id_size+4}
    while q<end:
        tag=data[q];q+=1
        if tag in roots:q+=roots[tag];continue
        object_id=identifier(q);q+=id_size
        if tag==0x20:
            q+=4;super_id=identifier(q);q+=id_size*6+4
            count=u(q,2);q+=2
            for _ in range(count):
                q+=2;kind=data[q];q+=1+width[kind]
            count=u(q,2);q+=2
            for _ in range(count):q+=id_size;kind=data[q];q+=1+width[kind]
            count=u(q,2);q+=2;fields=[]
            for _ in range(count):fields.append((identifier(q),data[q+id_size]));q+=id_size+1
            classes[object_id]=(super_id,fields)
        elif tag==0x21:
            q+=4;class_id=identifier(q);q+=id_size;length=u(q,4);q+=4
            objects[object_id]=(class_id,q,length);q+=length
        elif tag==0x22:
            q+=4;length=u(q,4);q+=4;array_class=identifier(q);q+=id_size
            arrays[object_id]=(2,length,q,array_class);q+=length*id_size
        elif tag in (0x23,0xc3):
            q+=4;length=u(q,4);q+=4;kind=data[q];q+=1
            arrays[object_id]=(kind,length,q,0)
            if tag!=0xc3:q+=length*width[kind]
        else:raise ValueError(f'Unknown heap tag {tag:x} at {q}')
    assert q==end
while offset<len(data):
    tag=data[offset];length=u(offset+5,4);start=offset+9;end=start+length
    if tag==1:strings[identifier(start)]=data[start+id_size:end].decode('utf-8',errors='replace')
    elif tag==2:class_names[identifier(start+4)]=identifier(start+8+id_size)
    elif tag in (0x0c,0x1c):heap(start,end)
    offset=end
def name(ref):
    cid=objects.get(ref,(ref,))[0]
    return strings.get(class_names.get(cid),'').replace('/','.')
def fields(ref):
    cid,q,length=objects[ref];result={};start=q
    while cid:
        parent,definitions=classes[cid]
        for key,kind in definitions:
            value=identifier(q) if kind==2 else struct.unpack_from('>'+formats[kind],view,q)[0]
            result[strings[key]]=value;q+=width[kind]
        cid=parent
    assert q-start==length,(name(ref),q-start,length)
    return result
def array(ref):
    kind,length,q,_=arrays[ref]
    return [identifier(q+i*id_size) for i in range(length)] if kind==2 else list(struct.unpack_from('>'+str(length)+formats[kind],view,q))
def pose(ref):
    f=fields(ref)
    return {k:f[k] for k in ['x','y','z','qx','qy','qz','qw','timestamp','initialized']}|{
       'rotation':array(f['imuFromCameraRotation']),'translation':array(f['imuFromCameraTranslation'])}
summary=[]
for ref,(cid,q,length) in objects.items():
    n=name(ref)
    if n in ['com.elabrador.mobilenavigation.SemanticSegmenter','com.elabrador.mobilenavigation.DepthImage',
       'com.elabrador.mobilenavigation.LiveDepthMap','com.elabrador.mobilenavigation.VinsPoseHistory'] or n.startswith('com.elabrador.mobilenavigation.SemanticSegmenter$$'):
        fs=fields(ref);summary.append({'ref':ref,'class':n,'fields':{k:(name(v) or 'array' if v in objects or v in arrays else type(v).__name__) for k,v in fs.items()}})
a.output.mkdir(parents=True,exist_ok=True)
(a.output/'navigation-heap-schema.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2))
frames=[];clouds=[];maps=[];histories=[]
for ref in objects:
    n=name(ref)
    if n=='com.elabrador.mobilenavigation.VinsPoseHistory':
        q=fields(fields(ref)['poses']);elements=q.get('elements',q.get('array'))
        history=[pose(v) for v in array(elements) if v and name(v)=='com.elabrador.mobilenavigation.VinsMono$Pose']
        histories.extend(history)
    if n=='com.elabrador.mobilenavigation.SemanticPointCloud$Data':
        f=fields(ref);clouds.append({k:array(f[k]) for k in ['xyz','semanticRgb','confidence']})
    if n=='com.elabrador.mobilenavigation.LiveDepthMap':
        f=fields(ref);maps.append({k:f[k] for k in ['groundHeight','known','freeCells','points']}|
          {'geometry':array(f['geometry']),'costs':array(f['costs']),'pose':pose(f['pose']),
           'evidence':fields(f['depthEvidence'])})
    if n.startswith('com.elabrador.mobilenavigation.SemanticSegmenter$$ExternalSyntheticLambda'):
        f=fields(ref)
        intrinsic=[v for v in f.values() if isinstance(v,int) and v in objects and name(v)=='com.intel.realsense.librealsense.Intrinsic']
        evidence_ref=[v for v in f.values() if isinstance(v,int) and v in objects and name(v)=='com.elabrador.mobilenavigation.FrameEvidence']
        if not intrinsic or not evidence_ref:continue
        intr=fields(intrinsic[0]);e=fields(evidence_ref[0]);frame={'evidence':e,'intrinsic':{k:array(v) if v in arrays else v for k,v in intr.items() if not k.startswith('shadow$')}}
        depth_ref=[v for v in f.values() if isinstance(v,int) and v in objects and name(v)=='com.elabrador.mobilenavigation.DepthImage']
        if depth_ref:
            d=fields(depth_ref[0]);depth=d['data'];frame['depth']={k:d[k] for k in ['width','height','stride','units']}
        elif 'f$6' in f and f['f$6'] in arrays:
            depth=f['f$6'];frame['depth']={'width':f['f$7'],'height':f['f$8'],'stride':f['f$9'],'units':f['f$10']}
        else:continue
        _,length,q,_=arrays[depth];filename=f'depth-{len(frames)}.raw';(a.output/filename).write_bytes(view[q:q+length]);frame['depth_file']=filename
        frames.append(frame)
plans=[]
for ref in objects:
    if name(ref)=='com.elabrador.mobilenavigation.OutdoorNavController':
        controller=fields(ref);plan=fields(controller['latestLocalPlan']);path_list=fields(plan['worldPath'])
        path_array=path_list.get('elementData',path_list.get('array'))
        path=[] if not path_array else [array(v) for v in array(path_array)[:path_list['size']]]
        plans.append({k:plan[k] for k in ['planned','success','blocked','steeringDegrees']}|{'path':path})
memory=[]
for ref in objects:
    if name(ref)=='com.elabrador.mobilenavigation.WorldObstacleMemory$Cell':
        f=fields(ref);memory.append({k:f[k] for k in ['x','y','z','wx','wy','wz','lastHit','clearFrames']})
result={'frames':frames,'poses':sorted(histories,key=lambda p:p['timestamp']),'live_maps':maps,'clouds':clouds,'plans':plans,
        'obstacle_memory':memory}
(a.output/'device-frames.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print('EXTRACTED',json.dumps({'frames':len(frames),'poses':len(histories),'maps':len(maps),'clouds':len(clouds),
  'intrinsics':[f['intrinsic'] for f in frames],'frame_timestamps':[f['evidence']['cameraSeconds'] for f in frames],
  'pose_span':[min(p['timestamp'] for p in histories),max(p['timestamp'] for p in histories)]},ensure_ascii=False))
matches=[]
def float32(value):return struct.unpack('<f',struct.pack('<f',value))[0]
for ci,cloud in enumerate(clouds):
    cloud_z=cloud['xyz'][2::3]
    for fi,frame in enumerate(frames):
        raw=(a.output/frame['depth_file']).read_bytes();d=frame['depth']
        for w,h in [(160,120),(384,288)]:
            z=[]
            for y in range(h):
                sy=y*d['height']//h
                for x in range(w):
                    sx=x*d['width']//w;i=sy*d['stride']+sx*2
                    value=float32(int.from_bytes(raw[i:i+2],'little')*d['units'])
                    if value>.1:z.append(value)
            if z==cloud_z:matches.append({'cloud':ci,'frame':fi,'sample_width':w,'sample_height':h,'points':len(z)})
result['exact_depth_cloud_matches']=matches
(a.output/'device-frames.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print('EXACT_DEPTH_CLOUD_MATCHES',json.dumps(matches))
