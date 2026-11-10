"""Pack the exactly matched real cloud with its original evidence and full measured pose history."""
import argparse, json, struct
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('input',type=Path);p.add_argument('output',type=Path);p.add_argument('--cloud',type=int);a=p.parse_args()
d=json.loads(a.input.read_text(encoding='utf-8'))
match=next(m for m in d['exact_depth_cloud_matches'] if m['sample_width']==160 and (a.cloud is None or m['cloud']==a.cloud))
cloud=d['clouds'][match['cloud']];frame=d['frames'][match['frame']];evidence=frame['evidence']
poses=sorted({p['timestamp']:p for p in d['poses']}.values(),key=lambda p:p['timestamp'])
def pose(f,p):
    f.write(struct.pack('>7d?',*(p[k] for k in ['x','y','z','qx','qy','qz','qw']),p['initialized']))
    f.write(struct.pack('>d9d3d',p['timestamp'],*p['rotation'],*p['translation']))
with a.output.open('wb') as f:
    f.write(struct.pack('>4sI',b'DCR2',len(cloud['semanticRgb'])))
    for key,fmt in [('xyz','f'),('semanticRgb','i'),('confidence','f')]:
        f.write(struct.pack('>'+str(len(cloud[key]))+fmt,*cloud[key]))
    f.write(struct.pack('>dqqq',evidence['cameraSeconds'],evidence['generation'],
                        evidence['captureElapsedMillis'],evidence['submittedElapsedMillis']))
    f.write(struct.pack('>I',len(poses)))
    for p in poses:pose(f,p)
    path=d['plans'][0]['path'] if d['plans'] else []
    f.write(struct.pack('>I',len(path)))
    for p in path:f.write(struct.pack('>2f',*p))
print(json.dumps({'file':str(a.output),'points':match['points'],'frame_seconds':evidence['cameraSeconds'],
    'original_capture_elapsed_ms':evidence['captureElapsedMillis'],'pose_samples':len(poses),
    'exact_depth_z_match':True,'path_points':len(path),'bytes':a.output.stat().st_size},indent=2))
