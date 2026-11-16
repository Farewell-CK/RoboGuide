import concurrent.futures,json,pathlib,requests
work=pathlib.Path('/home/seaway/sdb/niulinqiang/roboguide_demo/navi-obstacle')
data=(work/'bus-test.jpg').read_bytes()
def call(i):
 r=requests.post('http://127.0.0.1:8002/obstacles/analyze',files={'file':('bus.jpg',data,'image/jpeg')},data={'frame_id':str(i)},timeout=5)
 return r.status_code
with concurrent.futures.ThreadPoolExecutor(4) as pool:statuses=list(pool.map(call,range(4)))
assert 200 in statuses and 429 in statuses,statuses
r=requests.get('http://127.0.0.1:8002/health',timeout=3);r.raise_for_status()
print('PARALLEL_CLIENT_STATUS',statuses,'DINO_HEALTH',r.status_code)
