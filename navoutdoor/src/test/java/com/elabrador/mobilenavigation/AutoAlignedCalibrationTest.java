package com.elabrador.mobilenavigation;

import org.junit.Test;
import static org.junit.Assert.*;

public class AutoAlignedCalibrationTest {
    private VinsMono.Pose pose(double seconds,double yaw,boolean initialized){
        double half=Math.toRadians(yaw)/2;
        return new VinsMono.Pose(new double[]{0,0,0,0,0,Math.sin(half),Math.cos(half),0,0,0,
                initialized?1:0,seconds},new double[]{1,0,0,0,1,0,0,0,1},new double[3]);
    }
    private DynamicHeadingCalibrator auto(){
        DynamicHeadingCalibrator c=new DynamicHeadingCalibrator();c.startAutoAligned();return c;
    }
    @Test public void cardinalHeadingsAndArbitraryVinsYawPreserveLeftRight(){
        for(float heading:new float[]{0,90,180,270,359}){
            for(double yaw:new double[]{0,35,-100,170}){
                DynamicHeadingCalibrator c=auto();
                VinsMono.Pose pose=pose(10,yaw,true);
                assertTrue(c.updateAutoAligned(heading,pose,0,10000));
                assertEquals(0,c.relativeTargetDegrees(heading,pose),.001);
                assertEquals(90,c.relativeTargetDegrees((heading+90)%360,pose),.001);
                assertEquals(-90,c.relativeTargetDegrees((heading+270)%360,pose),.001);
                assertEquals(0,c.sampleCount());
            }
        }
    }
    @Test public void singleValidObservationLocksUntilVinsRestart(){
        DynamicHeadingCalibrator c=auto();
        assertTrue(c.updateAutoAligned(90,pose(10,0,true),0,10000));
        // Phone rotates on its own: it must not recalibrate the camera.
        assertFalse(c.updateAutoAligned(270,pose(11,0,true),0,11000));
        assertEquals(0,c.relativeTargetDegrees(90,pose(11,0,true)),.001);
        // Camera rotates left in its own VINS frame: old forward is now right.
        assertEquals(90,c.relativeTargetDegrees(90,pose(11,90,true)),.001);
        c.resetForVinsRestart();
        assertFalse(c.isReady());
        assertTrue(c.status().contains("同向"));
        assertTrue(Float.isNaN(c.relativeTargetDegrees(90,pose(11,0,true))));
        assertTrue(c.updateAutoAligned(270,pose(12,40,true),0,12000));
        assertEquals(0,c.relativeTargetDegrees(270,pose(12,40,true)),.001);
    }
    @Test public void invalidAndStaleInputsNeverClaimSuccess(){
        DynamicHeadingCalibrator c=auto();
        assertFalse(c.updateAutoAligned(0,null,0,10000));
        assertFalse(c.updateAutoAligned(0,pose(10,0,false),0,10000));
        assertFalse(c.updateAutoAligned(Float.NaN,pose(10,0,true),0,10000));
        assertFalse(c.updateAutoAligned(0,pose(10,0,true),-1,10000));
        assertFalse(c.updateAutoAligned(0,pose(10,0,true),2_000_000_001L,10000));
        assertFalse(c.updateAutoAligned(0,pose(8.5,0,true),0,10000));
        assertFalse(c.updateAutoAligned(0,pose(11,0,true),0,10000));
        assertFalse(c.updateAutoAligned(0,pose(10,Double.NaN,true),0,10000));
        assertFalse(c.isReady());
        assertTrue(c.updateAutoAligned(0,pose(10,0,true),0,10000));
    }
    @Test public void gpsTrajectoryCannotCompleteOrOverrideAutomaticAlignment(){
        DynamicHeadingCalibrator c=auto();
        for(int i=0;i<20;i++){
            c.update(34+i*.00002,113,3,0,i*2,true);
            c.updateTimed(34+i*.00002,113,3,0,i*2,true,(i+1)*1000000000L);
        }
        assertEquals(0,c.sampleCount());assertFalse(c.isReady());
        assertFalse(c.qualityDetails().contains("残差"));
        assertTrue(c.updateAutoAligned(15,pose(10,20,true),0,10000));
    }
}
