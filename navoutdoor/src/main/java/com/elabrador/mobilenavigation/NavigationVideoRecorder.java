package com.elabrador.mobilenavigation;

import android.graphics.ImageFormat;
import android.media.*;
import android.os.SystemClock;
import com.elabrador.mobilenavigation.archive.NavigationArchive;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.File;
import java.nio.ByteBuffer;
import java.util.Objects;
import java.util.concurrent.*;

/** Bounded RGB -> hardware AVC -> independently playable MP4 segments. No camera ownership. */
final class NavigationVideoRecorder {
    private static final ExecutorService worker=Executors.newSingleThreadExecutor(r->{Thread t=new Thread(r,"navigation-video");t.setDaemon(true);t.setPriority(Thread.MIN_PRIORITY);return t;});
    // At 640x480 RGB this bounds queued/working copies to 7.04 MiB. The short
    // buffer absorbs codec startup/rotation without blocking the camera thread.
    private static final Semaphore capacity=new Semaphore(8);
    private static String offeredSession,closedSession;
    private static final VideoFrameCadence cadence=new VideoFrameCadence();
    private static Encoder encoder;
    static synchronized void offer(byte[] rgb,int w,int h,int stride,double unixMs,long frameNumber) {
        String id=NavigationArchive.activeSessionId();if(id==null||id.equals(closedSession)||NavigationArchive.isClosing(id))return;
        long capture=SystemClock.elapsedRealtimeNanos()-(long)Math.max(0,System.currentTimeMillis()-unixMs)*1000000L;
        if(!Objects.equals(id,offeredSession)){offeredSession=id;cadence.reset();}
        if(!cadence.accept(capture))return;
        if(!capacity.tryAcquire()){NavigationArchive.problemFor(id,"video_frame_dropped",NavigationRecording.j("reason","encoder_queue_full","capture_ns",capture));return;}
        byte[] owned=rgb.clone();
        worker.execute(()->{
            try {
                if(encoder!=null&&(!encoder.id.equals(id)||capture-encoder.start>=30_000_000_000L)) {encoder.close();encoder=null;}
                if(encoder==null)encoder=new Encoder(id,w,h,capture);
                encoder.frame(owned,stride,capture,frameNumber,unixMs);
            }catch(Exception error){NavigationArchive.problemFor(id,"video_encoding_error",NavigationRecording.j("error",error.toString(),"capture_ns",capture));
                if(encoder!=null){encoder.abort();encoder=null;}
            }finally{capacity.release();}
        });
    }
    static void finish() {
        Future<?> barrier;
        synchronized(NavigationVideoRecorder.class) {
            closedSession=NavigationArchive.activeSessionId();
            barrier=worker.submit(()->{if(encoder!=null){try{encoder.close();}catch(Exception error){NavigationArchive.problemFor(encoder.id,"video_seal_error",NavigationRecording.j("error",error.toString()));encoder.abort();}encoder=null;}});
        }
        try {barrier.get(10,TimeUnit.SECONDS);}
        catch(Exception error){NavigationArchive.problemFor(NavigationArchive.activeSessionId(),"video_flush_timeout",NavigationRecording.j("error",error.toString()));}
    }
    private static final class Encoder {
        final String id;final int width,height;final long start;final File file;
        MediaCodec codec;MediaMuxer muxer;int track=-1;boolean started;int frameCount;long lastPts=-1;JSONArray frames=new JSONArray();
        final byte[][] rowBuffers=new byte[3][];
        Encoder(String id,int width,int height,long start)throws Exception {
            this.id=id;this.width=width;this.height=height;this.start=start;
            file=NavigationArchive.stagingFileFor(id,"mp4");
            try {
                MediaCodecInfo selected=null;
                for(MediaCodecInfo info:new MediaCodecList(MediaCodecList.REGULAR_CODECS).getCodecInfos()) {
                    if(!info.isEncoder()||!info.isHardwareAccelerated())continue;
                    for(String type:info.getSupportedTypes())if(type.equalsIgnoreCase("video/avc")) {
                        MediaCodecInfo.CodecCapabilities caps=info.getCapabilitiesForType(type);
                        for(int color:caps.colorFormats)if(color==MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Flexible&&caps.getVideoCapabilities().isSizeSupported(width,height))selected=info;
                    }
                    if(selected!=null)break;
                }
                if(selected==null)throw new IllegalStateException("No hardware AVC flexible YUV encoder");
                MediaFormat format=MediaFormat.createVideoFormat("video/avc",width,height);
                format.setInteger(MediaFormat.KEY_COLOR_FORMAT,MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Flexible);
                format.setInteger(MediaFormat.KEY_BIT_RATE,1_000_000);format.setInteger(MediaFormat.KEY_FRAME_RATE,15);format.setInteger(MediaFormat.KEY_I_FRAME_INTERVAL,2);
                codec=MediaCodec.createByCodecName(selected.getName());codec.configure(format,null,null,MediaCodec.CONFIGURE_FLAG_ENCODE);codec.start();
                muxer=new MediaMuxer(file.getAbsolutePath(),MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4);
                NavigationArchive.eventFor(id,"video_segment_started",NavigationRecording.j("capture_start_ns",start,"codec",selected.getName(),"width",width,"height",height,"fps",15,"target_bitrate",1000000));
            }catch(Exception error){abort();throw error;}
        }
        void frame(byte[] rgb,int stride,long capture,long number,double unixMs)throws Exception {
            drain(false);
            int index=codec.dequeueInputBuffer(10000);
            if(index<0){NavigationArchive.problemFor(id,"video_frame_dropped",NavigationRecording.j("reason","codec_input_busy","frame",number));return;}
            android.media.Image image=codec.getInputImage(index);
            if(image==null)throw new IllegalStateException("Encoder did not expose flexible YUV image input");
            try {fill(image,rgb,stride,width,height,rowBuffers);}finally{image.close();}
            long pts=Math.max(lastPts+1,(capture-start)/1000);lastPts=pts;
            codec.queueInputBuffer(index,0,width*height*3/2,pts,0);
            frames.put(NavigationRecording.j("frame_number",number,"capture_elapsed_ns",capture,"capture_unix_ms",unixMs,"pts_us",pts));frameCount++;
            drain(false);
        }
        void drain(boolean eos)throws Exception {
            MediaCodec.BufferInfo info=new MediaCodec.BufferInfo();long deadline=SystemClock.elapsedRealtime()+3000;
            while(true) {
                int index=codec.dequeueOutputBuffer(info,eos?10000:0);
                if(index==MediaCodec.INFO_OUTPUT_FORMAT_CHANGED){if(started)throw new IllegalStateException("Repeated encoder format");track=muxer.addTrack(codec.getOutputFormat());muxer.start();started=true;continue;}
                if(index<0){if(!eos)return;if(SystemClock.elapsedRealtime()>deadline)throw new IllegalStateException("Encoder EOS timed out");continue;}
                try {
                    ByteBuffer data=codec.getOutputBuffer(index);
                    if((info.flags&MediaCodec.BUFFER_FLAG_CODEC_CONFIG)!=0)info.size=0;
                    if(info.size>0){if(!started||data==null)throw new IllegalStateException("Missing video format");data.position(info.offset);data.limit(info.offset+info.size);muxer.writeSampleData(track,data,info);}
                }finally{codec.releaseOutputBuffer(index,false);}
                if((info.flags&MediaCodec.BUFFER_FLAG_END_OF_STREAM)!=0)return;
            }
        }
        void close()throws Exception {
            if(frameCount==0){abort();return;}
            int input=codec.dequeueInputBuffer(100000);
            if(input<0)throw new IllegalStateException("Cannot finish encoder input");
            codec.queueInputBuffer(input,0,0,lastPts+1,MediaCodec.BUFFER_FLAG_END_OF_STREAM);drain(true);
            codec.stop();codec.release();codec=null;
            if(started)muxer.stop();muxer.release();muxer=null;
            JSONObject metadata=NavigationRecording.j("start_elapsed_ns",start,"last_pts_us",lastPts,"frame_count",frameCount,
                "width",width,"height",height,"target_fps",15,"target_bitrate",1000000,"codec","H.264","audio",false,"frames",frames);
            if(!NavigationArchive.fileFor(id,"navigation_video","mp4",file,metadata))throw new IllegalStateException("Could not register sealed video");
        }
        void abort(){try{if(codec!=null){codec.stop();codec.release();}}catch(Exception ignored){}finally{codec=null;}try{if(muxer!=null)muxer.release();}catch(Exception ignored){}finally{muxer=null;}}
    }
    /** Writes YUV420 using per-plane row/pixel stride (never assumes NV12 or I420 layout). */
    static void fill(android.media.Image image,byte[] rgb,int stride,int w,int h,byte[][] rows) {
        android.media.Image.Plane[] planes=image.getPlanes();
        ByteBuffer[] buffers=new ByteBuffer[3];int[] rowStrides=new int[3],pixelStrides=new int[3],lengths=new int[3];
        for(int i=0;i<3;i++){
            buffers[i]=planes[i].getBuffer();rowStrides[i]=planes[i].getRowStride();pixelStrides[i]=planes[i].getPixelStride();
            lengths[i]=((i==0?w:w/2)-1)*pixelStrides[i]+1;
            if(rows[i]==null||rows[i].length!=lengths[i])rows[i]=new byte[lengths[i]];
        }
        for(int y=0;y<h;y++){
            for(int x=0;x<w;x++) {
                int p=y*stride+x*3,r=rgb[p]&255,g=rgb[p+1]&255,b=rgb[p+2]&255;
                int l=((66*r+129*g+25*b+128)>>8)+16;
                rows[0][x*pixelStrides[0]]=(byte)Math.max(0,Math.min(255,l));
            }
            buffers[0].position(y*rowStrides[0]);buffers[0].put(rows[0]);
        }
        for(int y=0;y<h;y+=2){
            for(int x=0;x<w;x+=2) {
                int r=0,g=0,b=0;
                for(int dy=0;dy<2;dy++)for(int dx=0;dx<2;dx++){int p=(y+dy)*stride+(x+dx)*3;r+=rgb[p]&255;g+=rgb[p+1]&255;b+=rgb[p+2]&255;}
                r/=4;g/=4;b/=4;
                int u=((-38*r-74*g+112*b+128)>>8)+128,v=((112*r-94*g-18*b+128)>>8)+128;
                rows[1][x/2*pixelStrides[1]]=(byte)Math.max(0,Math.min(255,u));
                rows[2][x/2*pixelStrides[2]]=(byte)Math.max(0,Math.min(255,v));
            }
            for(int i=1;i<3;i++){
                int offset=y/2*rowStrides[i];
                if(pixelStrides[i]==1){buffers[i].position(offset);buffers[i].put(rows[i]);}
                // U/V views can share interleaved storage. Never overwrite the
                // other plane's bytes in the gaps between our chroma samples.
                else for(int x=0;x<w/2;x++)buffers[i].put(offset+x*pixelStrides[i],rows[i][x*pixelStrides[i]]);
            }
        }
    }
}
