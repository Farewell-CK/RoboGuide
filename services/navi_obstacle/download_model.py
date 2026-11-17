import hashlib,json,pathlib,urllib.request
repo='facebook/mask2former-swin-large-mapillary-vistas-semantic'
revision='4772b6bf101d91f2534c106dc524d906aeb3c68a'
work=pathlib.Path(__file__).resolve().parent;model=work/'model';model.mkdir(exist_ok=True)
files={}
for name in ['config.json','preprocessor_config.json','README.md','model.safetensors']:
 dest=model/name
 if not dest.exists():
  with urllib.request.urlopen('https://huggingface.co/'+repo+'/resolve/'+revision+'/'+name,timeout=90) as response,dest.with_suffix(dest.suffix+'.part').open('wb') as out:
   while True:
    chunk=response.read(4*1024*1024)
    if not chunk:break
    out.write(chunk)
  dest.with_suffix(dest.suffix+'.part').rename(dest)
 sha=hashlib.sha256()
 with dest.open('rb') as src:
  for chunk in iter(lambda:src.read(4*1024*1024),b''):sha.update(chunk)
 files[name]=dict(size=dest.stat().st_size,sha256=sha.hexdigest())
 print(name,files[name],flush=True)
(work/'model-manifest.json').write_text(json.dumps(dict(repo=repo,revision=revision,files=files),indent=2))
