package com.elabrador.mobilenavigation;

/** Counts acquisition identities, never pose-only reprojects or repeated UI draws. */
final class ObservationRefreshTracker {
    private FrameEvidence last;
    private long lastAt;
    long intervalMillis=-1;
    long count;
    boolean observe(FrameEvidence frame,long now){
        if(frame==null||!frame.fresh(now))return false;
        if(last!=null&&(frame.generation<last.generation
                ||(frame.generation==last.generation&&frame.cameraSeconds<=last.cameraSeconds)))return false;
        intervalMillis=last==null?-1:now-lastAt;last=frame;lastAt=now;count++;return true;
    }
    void reset(){last=null;lastAt=0;intervalMillis=-1;count=0;}
}
