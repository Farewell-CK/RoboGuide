package com.elabrador.mobilenavigation;

import org.junit.Test;
import static org.junit.Assert.*;

public class SourceProjectionPoseTest {
    private VinsMono.Pose pose(double time,boolean initialized) {
        double[] data=new double[12];data[6]=1;data[10]=initialized?1:0;data[11]=time;
        return new VinsMono.Pose(data,new double[]{1,0,0,0,1,0,0,0,1},new double[3]);
    }
    @Test public void onlyRecentMeasuredPoseFromTheSameWorldCanReplaceCaptureOrientation() {
        VinsMono.Pose capture=pose(100,true),current=pose(102,true);
        assertSame(current,SourceProjectionPose.select(capture,current,1499,true));
        assertSame(capture,SourceProjectionPose.select(capture,current,1500,true));
        assertSame(capture,SourceProjectionPose.select(capture,current,-1,true));
        assertSame(capture,SourceProjectionPose.select(capture,current,100,false));
        assertSame(capture,SourceProjectionPose.select(capture,pose(99,true),100,true));
        assertSame(capture,SourceProjectionPose.select(capture,pose(102,false),100,true));
        assertSame(capture,SourceProjectionPose.select(capture,null,100,true));
    }
    @Test public void frameAgeAndNewMapCounterAreIndependentOfDisplayUpdates() {
        ObservationRefreshTracker tracker=new ObservationRefreshTracker();
        FrameEvidence frame=new FrameEvidence(100,1,1000);
        assertTrue(tracker.observeDrawn(frame,3550));
        assertEquals(2550,tracker.captureToDrawMillis());
        assertFalse(tracker.observeDrawn(frame,4550));
        assertEquals(2550,tracker.captureToDrawMillis());
        assertEquals(3550,frame.ageMillis(4550));
        tracker.reset();assertEquals(-1,tracker.captureToDrawMillis());
    }
}
