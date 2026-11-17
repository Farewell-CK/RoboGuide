from pathlib import Path
import hashlib,json,requests,time
from PIL import Image
from io import BytesIO
ROOT=Path('/home/seaway/sdb/niulinqiang/roboguide_demo');work=ROOT/'navi-obstacle'
manifest=json.loads((work/'model-manifest.json').read_text())
for name,expected in manifest['files'].items():
 d=work/'model'/name;sha=hashlib.sha256()
 with d.open('rb') as src:
  for chunk in iter(lambda:src.read(4*1024*1024),b''):sha.update(chunk)
 assert sha.hexdigest()==expected['sha256'],name
print('MODEL_HASHES_VERIFIED')
print('OBSTACLE_HEALTH',requests.get('http://127.0.0.1:8003/health',timeout=5).json())
image=Image.new('RGB',(512,768),(160,160,160));out=BytesIO();image.save(out,'JPEG')
measurements=[]
for i in range(3):
 started=time.perf_counter()
 r=requests.post('http://127.0.0.1:8003/analyze',files={'file':('frame.jpg',out.getvalue(),'image/jpeg')},data={'frame_id':str(i)},timeout=10);r.raise_for_status()
 measurements.append(dict(roundtrip_ms=round((time.perf_counter()-started)*1000),**r.json()))
print('BLANK_FRAME_PROBES',json.dumps(measurements,ensure_ascii=False))
