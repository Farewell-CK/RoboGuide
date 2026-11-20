"""Swin-L / Mask2Former semantic assistance for NAVI RGB frames.
Runs locally on CUDA; the DINO gateway exposes only the two proxy endpoints.
"""
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
import asyncio, json, os, time
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError
import torch
import torch.nn.functional as F
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from transformers import Mask2FormerForUniversalSegmentation, Mask2FormerImageProcessor
import uvicorn
from obstacle_policy import observe

WORK=Path(__file__).resolve().parent
model=None
processor=None
PROFILE=os.environ.get('NAVI_OBSTACLE_PROFILE','indoor')
MODEL_DIR=WORK/('model-indoor' if PROFILE=='indoor' else 'model')
MANIFEST_PATH=WORK/('model-indoor-manifest.json' if PROFILE=='indoor' else 'model-manifest.json')
busy=False
MAX_BYTES=3*1024*1024
Image.MAX_IMAGE_PIXELS=16_000_000
manifest=json.loads(MANIFEST_PATH.read_text())

def infer(image):
    # Use the checkpoint's official processor and project back to the whole view.
    image=ImageOps.exif_transpose(image).convert('RGB')
    if min(image.size)<32 or max(image.size)>8192:raise ValueError('Invalid image size')
    w,h=image.size; scale=min(1.,512/max(w,h))
    nw,nh=max(1,round(w*scale)),max(1,round(h*scale))
    inputs=processor(images=image,return_tensors='pt').to('cuda')
    with torch.inference_mode(),torch.autocast('cuda',dtype=torch.float16):
        outputs=model(**inputs)
        classes=outputs.class_queries_logits.float().softmax(-1)[...,:-1]
        masks=F.interpolate(outputs.masks_queries_logits.float(),size=(nh,nw),mode='bilinear',align_corners=False).sigmoid()
        scores=classes.transpose(1,2)@masks.flatten(2)
        scores=scores.reshape(1,-1,nh,nw)
        # A normalized semantic share, not the exporter's modulo-wrapped score.
        values,labels=scores.max(1)
        confidence=values/scores.sum(1).clamp_min(1e-6)
    return observe(labels[0].cpu().numpy(),confidence[0].cpu().numpy(),model.config.id2label,PROFILE)

@asynccontextmanager
async def lifespan(app):
    global model,processor
    if not torch.cuda.is_available() or torch.cuda.device_count()!=1:
        raise RuntimeError('Exactly one designated CUDA GPU is required')
    torch.set_num_threads(2)
    if PROFILE not in ('indoor','outdoor'):raise RuntimeError('Unknown scene profile')
    model=Mask2FormerForUniversalSegmentation.from_pretrained(str(MODEL_DIR),local_files_only=True,use_safetensors=True).to('cuda').eval()
    expected=(150,'person') if PROFILE=='indoor' else (65,'Person')
    if len(model.config.id2label)!=expected[0] or model.config.id2label.get(12 if PROFILE=='indoor' else 19)!=expected[1]:
        raise RuntimeError('Checkpoint labels do not match the requested scene')
    processor=Mask2FormerImageProcessor.from_pretrained(str(MODEL_DIR),local_files_only=True)
    await run_in_threadpool(infer,Image.new('RGB',(384,288)))
    yield

app=FastAPI(lifespan=lifespan)
@app.get('/health')
async def health():
    return dict(status='healthy',model=manifest['repo'],revision=manifest['revision'],device='cuda',pid=os.getpid(),
                input='RGB monocular',profile=PROFILE,policy_version=2,
                distance_available=False,automatic_detour=False,busy=busy)

@app.post('/analyze')
async def analyze(file:UploadFile=File(...),frame_id:str=Form(...)):
    global busy
    if len(frame_id)>80:raise HTTPException(400,'Invalid frame ID')
    if busy:raise HTTPException(429,'Previous observation still running; drop this frame')
    busy=True;started=time.perf_counter()
    try:
        data=await file.read(MAX_BYTES+1)
        if len(data)>MAX_BYTES:raise HTTPException(413,'Frame too large')
        def process():
            with Image.open(BytesIO(data)) as image:
                return infer(image)
        result=await run_in_threadpool(process)
        return dict(result,frame_id=frame_id,inference_ms=round((time.perf_counter()-started)*1000),
                    model='Swin-L / Mask2Former',model_revision=manifest['revision'])
    except (ValueError,UnidentifiedImageError,Image.DecompressionBombError):
        raise HTTPException(400,'Invalid image')
    finally:
        busy=False;await file.close()

if __name__=='__main__':uvicorn.run(app,host='127.0.0.1',port=int(os.environ.get('NAVI_OBSTACLE_PORT','8003')),workers=1)
