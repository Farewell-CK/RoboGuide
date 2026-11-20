package com.elabrador.mobilenavigation;

import org.junit.Test;
import static org.junit.Assert.*;

public class SwinObservationTimingTest {
    private FrameEvidence frame(double camera,long capture) {
        return new FrameEvidence(camera,1,capture);
    }
    @Test public void twoDistinctSlowFramesCanReleaseStopButRedrawsCannot() {
        GuidanceStabilizer guidance=new GuidanceStabilizer();
        long ageLimit=SemanticObservationPolicy.SWIN_MAP_MAX_MILLIS;
        guidance.updateValidated("停止",true,0,null,ageLimit);
        FrameEvidence first=frame(1,100);
        assertEquals("停止",guidance.updateValidated("直走",true,3100,first,ageLimit));
        for(long now=3200;now<6100;now+=100)
            assertEquals("停止",guidance.updateValidated("直走",true,now,first,ageLimit));
        assertEquals("直走",guidance.updateValidated("直走",true,6100,frame(4,3100),ageLimit));
        assertEquals("停止",guidance.updateValidated("直走",true,13100,frame(4,3100),ageLimit));
    }
    @Test public void semanticLifetimeUsesCaptureAndRejectsFutureOrMissingEvidence() {
        FrameEvidence observation=frame(2,2000);
        assertFalse(SemanticObservationPolicy.usable(null,5000));
        assertFalse(SemanticObservationPolicy.usable(observation,1999));
        assertTrue(SemanticObservationPolicy.usable(observation,5000));
        assertTrue(SemanticObservationPolicy.usable(observation,11999));
        assertFalse(SemanticObservationPolicy.usable(observation,12000));
        assertFalse(observation.fresh(5000)); // Pose/legacy policy is still 1.5 seconds.
    }
    @Test public void completedSlowMapsCountOnlyOnceAndRateFallsToZeroWhenStalled() {
        ObservationRefreshTracker tracker=new ObservationRefreshTracker();
        FrameEvidence first=frame(1,100);
        assertTrue(tracker.observeDrawn(first,3100));
        for(long now=3200;now<6100;now+=100)assertFalse(tracker.observeDrawn(first,now));
        assertTrue(tracker.observeDrawn(frame(4,3100),6100));
        assertEquals(3000,tracker.intervalMillis);
        assertEquals(2,tracker.count);
        assertEquals(.4,tracker.recentHz(6100),.0001);
        assertEquals(0,tracker.recentHz(11101),0);
        assertEquals(5001,tracker.silentMillis(11101));
    }
}
