package com.elabrador.mobilenavigation;

import org.junit.Test;
import static org.junit.Assert.assertEquals;

public class GuidanceStabilizerTest {
    @Test public void burstsAreCoalescedToLatestDirection() {
        GuidanceStabilizer filter = new GuidanceStabilizer();
        assertEquals("直走", filter.update("直走", true, 0));
        assertEquals("直走", filter.update("左转", true, 100));
        assertEquals("直走", filter.update("向后转", true, 400));
        assertEquals("直走", filter.update("右转", true, 999));
        assertEquals("右转", filter.update("右转", true, 1000));
        assertEquals("右转", filter.update("左转", true, 1100));
        assertEquals("左转", filter.update("左转", true, 2000));
    }

    @Test public void changeAfterSteadyDirectionIsImmediate() {
        GuidanceStabilizer filter = new GuidanceStabilizer();
        filter.update("直走", true, 0);
        assertEquals("直走", filter.update("直走", true, 5000));
        assertEquals("左转", filter.update("左转", true, 5010));
        assertEquals("左转", filter.update("左转", true, 5020));
    }

    @Test public void emergencyStopIsImmediateAndReleaseIsHeld() {
        GuidanceStabilizer filter = new GuidanceStabilizer();
        filter.update("直走", true, 0);
        assertEquals("停止", filter.update("停止", true, 50));
        assertEquals("停止", filter.update("左转", true, 100));
        assertEquals("左转", filter.update("左转", true, 1050));
    }

    @Test public void endingOrLosingGuidanceClearsImmediately() {
        GuidanceStabilizer filter = new GuidanceStabilizer();
        filter.update("左转", true, 0);
        assertEquals("", filter.update("右转", false, 10));
        assertEquals("右转", filter.update("右转", true, 20));
        assertEquals("", filter.update("", true, 30));
        assertEquals("直走", filter.update("直走", true, 40));
    }
}
