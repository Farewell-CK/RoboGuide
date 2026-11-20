package com.elabrador.mobilenavigation;
import org.junit.Test;
import static org.junit.Assert.*;

public class RefreshStatisticsTest {
    @Test public void stalledDisplayDecaysToZeroHzAndIncludesCurrentGap() {
        ObservationRefreshTracker tracker=new ObservationRefreshTracker();
        for(int i=0;i<50;i++)tracker.observe(new FrameEvidence(i,1,1000+i*100),1000+i*100);
        assertEquals(10,tracker.recentHz(5900),.001);
        assertEquals(0,tracker.recentHz(11000),0);
        assertEquals(5100,tracker.silentMillis(11000));
        assertEquals(5100,tracker.maximumGapMillis(11000));
    }
    @Test public void redrawingOneCameraFrameCannotInflateTheRefreshRate() {
        ObservationRefreshTracker tracker=new ObservationRefreshTracker();FrameEvidence frame=new FrameEvidence(1,1,1000);
        assertTrue(tracker.observe(frame,1000));
        for(int i=0;i<100;i++)assertFalse(tracker.observe(frame,1001+i));
        assertEquals(.2,tracker.recentHz(1200),.001);assertEquals(1,tracker.count);
    }
}
