import concurrent.futures,json,pathlib,statistics,time,requests
root=pathlib.Path('/home/seaway/sdb/niulinqiang/roboguide_demo');work=root/'navi-obstacle'
data=(work/'bus-test.jpg').read_bytes()
def post(port,path,i):
 t=time.perf_counter();r=requests.post(f'http://127.0.0.1:{port}'+path,files={('image' if path=='/encode/navigation' else 'file'):('frame.jpg',data,'image/jpeg')},data={'frame_id':str(i)},timeout=10)
 r.raise_for_status();return dict(ms=round((time.perf_counter()-t)*1000),body=r.json())
baseline=[post(8002,'/encode/navigation',i) for i in range(3)]
results=[]
with concurrent.futures.ThreadPoolExecutor(2) as pool:
 for i in range(5):
  a=pool.submit(post,8002,'/encode/navigation',i);b=pool.submit(post,8002,'/obstacles/analyze',i)
  results.append(dict(dino=a.result(),obstacle=b.result()))
# Check that auxiliary inference does not change the existing embedding output.
first=baseline[0]['body'];key=next(k for k in ['embedding','features'] if k in first)
a=first[key]
for result in results:
 assert len(result['dino']['body'][key])==768
 assert max(abs(x-y) for x,y in zip(a,result['dino']['body'][key]))<1e-5
assert any(d['class_id']==19 for d in results[0]['obstacle']['body']['detections'])
r=requests.post('http://127.0.0.1:8002/obstacles/analyze',files={'file':('invalid.jpg',b'not an image','image/jpeg')},data={'frame_id':'bad'},timeout=5)
assert r.status_code==400
report=dict(model_revision=json.loads((work/'model-manifest.json').read_text())['revision'],
 dino_only_ms=[v['ms'] for v in baseline],dino_concurrent_ms=[v['dino']['ms'] for v in results],
 obstacle_concurrent_ms=[v['obstacle']['ms'] for v in results],embedding_preserved=True,
 invalid_image_status=r.status_code,positive_observation=results[0]['obstacle']['body'])
(work/'verification-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(report,ensure_ascii=False))
