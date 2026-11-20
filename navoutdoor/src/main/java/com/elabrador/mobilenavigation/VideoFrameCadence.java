package com.elabrador.mobilenavigation;

/** Selects 15 Hz from capture times without accumulating camera timestamp jitter. */
final class VideoFrameCadence {
    private static final long PERIOD_NS=1_000_000_000L/15, JITTER_NS=2_000_000L;
    private long next=-1,last=-1;
    void reset(){next=last=-1;}
    boolean accept(long capture){
        if(capture<0||capture<=last)return false;
        if(next>=0&&capture+JITTER_NS<next)return false;
        // Re-anchor after an input gap; never try to catch up by recording a burst.
        next=next<0||capture-next>=PERIOD_NS?capture+PERIOD_NS:next+PERIOD_NS;
        last=capture;
        return true;
    }
}
