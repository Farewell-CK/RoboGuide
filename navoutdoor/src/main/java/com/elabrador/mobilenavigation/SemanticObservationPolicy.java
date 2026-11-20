package com.elabrador.mobilenavigation;

/** Separate measured semantic acquisition age from the faster pose watchdog. */
final class SemanticObservationPolicy {
    // Upstream follower retains a map for 10 s after receipt. This is stricter:
    // measure from original acquisition, never receipt, reprojection or redraw.
    static final long SWIN_MAP_MAX_MILLIS=10000;
    static boolean usable(FrameEvidence frame,long now) {
        if(frame==null)return false;long age=frame.ageMillis(now);
        return age>=0 && age<SWIN_MAP_MAX_MILLIS;
    }
}
