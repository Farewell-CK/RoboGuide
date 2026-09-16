package com.elabrador.mobilenavigation;

import org.junit.Test;
import static org.junit.Assert.*;

public class CalibrationMotionGateTest {
    private static final double M=180.0/Math.PI/6371000.0;
    @Test public void largeGpsStepWithSmallVinsStepResetsWindow(){
        CalibrationMotionGate gate=new CalibrationMotionGate();
        assertEquals("",gate.observe(34,113,5.7f,0,0,1_000_000_000L));
        assertTrue(gate.observe(34+21.3*M,113,3.8f,0,1.6,4_897_000_000L).contains("位移不符"));
        assertTrue(gate.observe(34+31*M,113,3.7f,0,2.6,5_910_000_000L).contains("位移不符"));
        assertEquals("",gate.observe(34+32*M,113,3.7f,0,3.6,6_910_000_000L));
    }
    @Test public void equalMotionDoesNotRequirePhoneOrWorldAxesToAlign(){
        CalibrationMotionGate gate=new CalibrationMotionGate();
        for(int i=0;i<20;i++)assertEquals("",gate.observe(34+i*2*M,113,3,i*2,0,(i+1)*2_000_000_000L));
    }
    @Test public void estimatorJumpAndLongPairingGapAreAlsoDiscontinuities(){
        CalibrationMotionGate gate=new CalibrationMotionGate();gate.observe(34,113,3,0,0,1_000_000_000L);
        assertTrue(gate.observe(34+M,113,3,20,0,2_000_000_000L).contains("位移不符"));
        assertTrue(gate.observe(34+2*M,113,3,21,0,8_000_000_000L).contains("中断"));
    }
    @Test public void discontinuityStartsNewWindowAndCleanDataCanComplete(){
        DynamicHeadingCalibrator c=new DynamicHeadingCalibrator();c.start();
        c.updateTimed(34,113,3,0,0,true,1_000_000_000L);
        c.updateTimed(34+21*M,113,3,0,1,true,2_000_000_000L);
        assertEquals(1,c.sampleCount());assertFalse(c.isReady());
        for(int i=1;i<=16;i++)c.updateTimed(34+(21+i*2)*M,113,3,0,1+i*2,true,(i+2)*2_000_000_000L);
        assertTrue(c.qualityDetails(),c.isReady());assertEquals(0,c.northOffsetDegrees(),.1);
    }
    @Test public void duplicateAndReorderedFixesCannotFillWindow(){
        DynamicHeadingCalibrator c=new DynamicHeadingCalibrator();c.start();
        c.updateTimed(34,113,3,0,0,true,2_000_000_000L);
        c.updateTimed(34+20*M,113,3,0,20,true,2_000_000_000L);
        c.updateTimed(34+20*M,113,3,0,20,true,1_000_000_000L);
        assertEquals(1,c.sampleCount());assertFalse(c.isReady());
    }
}
