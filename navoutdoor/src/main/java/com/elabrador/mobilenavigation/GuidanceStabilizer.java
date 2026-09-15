package com.elabrador.mobilenavigation;

/** Output-only timing. Never delays map processing, planning, or an explicit stop. */
final class GuidanceStabilizer {
    static final long CHANGE_INTERVAL_MILLIS = 5000L;
    static final long STOP_RELEASE_MILLIS = 1500L;
    static final long WAITING_SAFETY_TIMEOUT_MILLIS = 1500L;
    private String displayed = "";
    private long changedAt;
    private long waitingSince = -1;
    private long safeSince = -1;
    private long lastSafeAt = -1;
    private int safeFrames;
    private FrameEvidence lastSafeEvidence;

    String updateValidated(String next, boolean active, long now, FrameEvidence evidence) {
        if(active && evidence!=null && !evidence.fresh(now))next="停止";
        if(active && evidence==null && !"停止".equals(next))next="";
        return updateInternal(next,active,now,evidence,true);
    }

    String updateWithEvidence(String next,boolean active,long now,FrameEvidence evidence){
        return updateValidated(next,active,now,evidence);
    }

    /** Clock-only entry for callers that already validate each observation. */
    String update(String next,boolean active,long now){
        return updateInternal(next,active,now,null,false);
    }

    private String updateInternal(String next,boolean active,long now,FrameEvidence evidence,boolean validate){
        if(!active){reset();return displayed;}
        if("停止".equals(next)){
            clearRecovery();waitingSince=-1;
            if(!"停止".equals(displayed)){displayed="停止";changedAt=now;}
            return displayed;
        }
        if(next==null||next.isEmpty()){
            clearRecovery();
            if(waitingSince<0)waitingSince=now;
            if(now-waitingSince>=WAITING_SAFETY_TIMEOUT_MILLIS && !"停止".equals(displayed)){
                displayed="停止";changedAt=now;
            }
            return displayed;
        }
        waitingSince=-1;
        if("停止".equals(displayed)){
            if(lastSafeAt>=0 && (now<lastSafeAt || now-lastSafeAt>=WAITING_SAFETY_TIMEOUT_MILLIS))clearRecovery();
            if(validate && lastSafeEvidence!=null && evidence.generation!=lastSafeEvidence.generation)clearRecovery();
            if(validate && lastSafeEvidence!=null && evidence.cameraSeconds<lastSafeEvidence.cameraSeconds){
                clearRecovery();return displayed;
            }
            boolean newFrame=!validate||lastSafeEvidence==null||evidence.cameraSeconds>lastSafeEvidence.cameraSeconds;
            if(newFrame){
                if(safeSince<0)safeSince=now;
                safeFrames++;lastSafeAt=now;lastSafeEvidence=evidence;
            }
            // A redraw of one safe frame cannot release stop. All ordinary directions
            // count as safe; the newest one is emitted, never a queued earlier turn.
            if(newFrame && safeFrames>=2 && now-safeSince>=STOP_RELEASE_MILLIS){
                displayed=next;changedAt=now;clearRecovery();
            }
            return displayed;
        }
        clearRecovery();
        if(next.equals(displayed))return displayed;
        // A validated straight cue means the turn is complete. Stop recovery above
        // still applies. Every return to straight starts a new turn hold interval.
        if(displayed.isEmpty()||"直走".equals(next)||now-changedAt>=CHANGE_INTERVAL_MILLIS){
            displayed=next;changedAt=now;
        }
        return displayed;
    }

    private void clearRecovery(){safeSince=-1;lastSafeAt=-1;safeFrames=0;lastSafeEvidence=null;}
    void reset(){displayed="";changedAt=0;waitingSince=-1;clearRecovery();}
}
