package com.elabrador.mobilenavigation;

/** Counts acquisition identities, never pose-only reprojects or repeated UI draws. */
final class ObservationRefreshTracker {
    private final java.util.ArrayDeque<Long> arrivals=new java.util.ArrayDeque<>();
    private FrameEvidence last;
    private long lastAt;
    long intervalMillis=-1;
    long count;
    private long maximumIntervalMillis;
    private long lastCaptureToDrawMillis=-1;
    synchronized boolean observe(FrameEvidence frame,long now){
        if(frame==null||!frame.fresh(now))return false;
        return observeDrawn(frame,now);
    }
    /** Completed semantic results can be slow while still being new screen observations. */
    synchronized boolean observeDrawn(FrameEvidence frame,long now){
        if(frame==null||frame.captureElapsedMillis<0||frame.ageMillis(now)<0)return false;
        if(last!=null&&(frame.generation<last.generation
                ||(frame.generation==last.generation&&frame.cameraSeconds<=last.cameraSeconds)))return false;
        intervalMillis=last==null?-1:now-lastAt;
        lastCaptureToDrawMillis=frame.ageMillis(now);
        maximumIntervalMillis=Math.max(maximumIntervalMillis,intervalMillis);
        last=frame;lastAt=now;count++;arrivals.addLast(now);prune(now);return true;
    }
    private void prune(long now){while(!arrivals.isEmpty()&&arrivals.peekFirst()<=now-5000)arrivals.removeFirst();}
    synchronized double recentHz(long now){prune(now);return arrivals.size()/5.0;}
    synchronized long silentMillis(long now){return last==null?-1:Math.max(0,now-lastAt);}
    synchronized long maximumGapMillis(long now){return Math.max(maximumIntervalMillis,silentMillis(now));}
    synchronized long captureToDrawMillis(){return lastCaptureToDrawMillis;}
    synchronized void reset(){last=null;lastAt=0;intervalMillis=-1;count=0;maximumIntervalMillis=0;lastCaptureToDrawMillis=-1;arrivals.clear();}
}
