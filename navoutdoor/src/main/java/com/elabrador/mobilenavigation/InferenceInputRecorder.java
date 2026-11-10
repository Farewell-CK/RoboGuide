package com.elabrador.mobilenavigation;

import com.elabrador.mobilenavigation.archive.NavigationArchive;
import org.json.JSONObject;
import java.io.ByteArrayOutputStream;
import java.util.concurrent.*;
import java.util.zip.GZIPOutputStream;

final class InferenceInputRecorder {
    private static final ThreadPoolExecutor worker=new ThreadPoolExecutor(1,1,0,TimeUnit.SECONDS,
        new ArrayBlockingQueue<>(4),r->{Thread t=new Thread(r,"inference-input-archive");t.setDaemon(true);return t;});
    static void offer(byte[] rgb,int w,int h,int stride,byte[] aligned,int dw,int dh,int ds,float units,
                      InferenceCapture capture,FrameEvidence e) {
        if(!NavigationRecording.accepts(e))return;
        try {worker.execute(()->NavigationRecording.safe(()->{
            JSONObject meta=NavigationRecording.evidence(e);
            NavigationRecording.put(meta,"capture",capture==null?null:capture.metadata);
            NavigationRecording.put(meta,"width",w);NavigationRecording.put(meta,"height",h);NavigationRecording.put(meta,"jpeg_quality",80);
            NavigationArchive.blobFor(e.recordingSessionId,"inference_rgb","jpg",ArchiveImages.jpeg(rgb,w,h,stride),meta);
            if(capture!=null)depth("inference_raw_depth",capture.raw.data,capture.raw.width,capture.raw.height,capture.raw.stride,capture.raw.units,e,capture);
            else NavigationArchive.problemFor(e.recordingSessionId,"missing_raw_depth",NavigationRecording.evidence(e));
            depth("inference_aligned_depth",aligned,dw,dh,ds,units,e,capture);
        }));}catch(RejectedExecutionException error){NavigationArchive.problemFor(e.recordingSessionId,"inference_input_queue_full",NavigationRecording.evidence(e));}
    }
    private static void depth(String kind,byte[] bytes,int w,int h,int stride,float units,FrameEvidence e,InferenceCapture capture) {
        try {
            ByteArrayOutputStream compressed=new ByteArrayOutputStream();
            try(GZIPOutputStream gzip=new GZIPOutputStream(compressed)){gzip.write(bytes);}
            JSONObject meta=NavigationRecording.evidence(e);
            NavigationRecording.put(meta,"width",w);NavigationRecording.put(meta,"height",h);NavigationRecording.put(meta,"stride_bytes",stride);
            NavigationRecording.put(meta,"depth_unit_m",units);NavigationRecording.put(meta,"format","gzip:little_endian_uint16_Z16_with_row_stride");
            NavigationRecording.put(meta,"uncompressed_bytes",bytes.length);NavigationRecording.put(meta,"capture",capture==null?null:capture.metadata);
            NavigationArchive.blobFor(e.recordingSessionId,kind,"gz",compressed.toByteArray(),meta);
        }catch(Exception error){NavigationArchive.problemFor(e.recordingSessionId,"depth_write_error",NavigationRecording.j("error",error.toString()));}
    }
    static void finish(){
        try {worker.prestartCoreThread();FutureTask<Void> barrier=new FutureTask<>(()->null);if(!worker.getQueue().offer(barrier,5,TimeUnit.SECONDS))throw new TimeoutException("Input queue did not drain");barrier.get(10,TimeUnit.SECONDS);}
        catch(Exception error){NavigationArchive.problemFor(NavigationArchive.activeSessionId(),"inference_input_flush_error",NavigationRecording.j("error",error.toString()));}
    }
}
