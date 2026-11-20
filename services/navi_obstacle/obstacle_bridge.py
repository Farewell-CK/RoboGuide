"""Bounded loopback bridge mounted in the existing DINOv2 GPU gateway."""
import requests
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.concurrency import run_in_threadpool
router=APIRouter(prefix='/obstacles')
BASE='http://127.0.0.1:8003'

def forward(path, data=None, frame_id=None):
    try:
        if data is None:r=requests.get(BASE+path,timeout=(.3,2))
        else:r=requests.post(BASE+path,files={'file':('frame.jpg',data,'image/jpeg')},data={'frame_id':frame_id},timeout=(.3,3))
        if r.status_code!=200:raise HTTPException(r.status_code,'Obstacle observation unavailable')
        return r.json()
    except (requests.RequestException,ValueError):raise HTTPException(503,'Obstacle observation unavailable')

@router.get('/health')
async def health():return await run_in_threadpool(forward,'/health')

@router.post('/analyze')
async def analyze(file:UploadFile=File(...),frame_id:str=Form(...)):
    try:
        data=await file.read(3*1024*1024+1)
        if len(data)>3*1024*1024:raise HTTPException(413,'Frame too large')
        if len(frame_id)>80:raise HTTPException(400,'Invalid frame ID')
        return await run_in_threadpool(forward,'/analyze',data,frame_id)
    finally:await file.close()
