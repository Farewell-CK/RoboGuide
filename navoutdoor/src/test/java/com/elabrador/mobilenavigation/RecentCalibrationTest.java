package com.elabrador.mobilenavigation;

import org.junit.Test;
import static org.junit.Assert.*;

public class RecentCalibrationTest {
    private static final double M=180.0/Math.PI/6371000.0;
    private void sample(DynamicHeadingCalibrator c,int i,double east,double north){
        c.update(34+north*M,113+east*M/Math.cos(Math.toRadians(34)),3,0,i*2,true);
    }
    @Test public void badStartupIsReplacedByRecentCleanTrajectoryWithoutRelaxingGates(){
        DynamicHeadingCalibrator c=new DynamicHeadingCalibrator();c.start();
        for(int i=0;i<30;i++)sample(c,i,i%2==0?12:-12,i*2);
        assertFalse(c.isReady());assertEquals(17,c.sampleCount());assertEquals(30,c.totalSampleCount());
        for(int i=30;i<47;i++)sample(c,i,12,i*2);
        assertTrue(c.qualityDetails(),c.isReady());
        assertEquals(0,c.northOffsetDegrees(),.5);
        assertEquals(17,c.sampleCount());assertTrue(c.totalSampleCount()<=47);
    }
    @Test public void continuingBadDataCannotPassSimplyBecauseWindowRolls(){
        DynamicHeadingCalibrator c=new DynamicHeadingCalibrator();c.start();
        for(int i=0;i<120;i++)sample(c,i,i%2==0?12:-12,i*2);
        assertFalse(c.isReady());assertEquals(17,c.sampleCount());assertEquals(120,c.totalSampleCount());
        assertTrue(c.qualityDetails().contains("未通过"));
    }
    @Test public void rollingWindowDoesNotTurnFrozenGpsIntoDirection(){
        DynamicHeadingCalibrator c=new DynamicHeadingCalibrator();c.start();
        for(int i=0;i<100;i++)sample(c,i,0,0);
        assertFalse(c.isReady());assertTrue(c.qualityDetails().contains("GPS 轨迹几乎没有移动"));
    }
    @Test public void resetClearsCurrentAndTotalSamples(){
        DynamicHeadingCalibrator c=new DynamicHeadingCalibrator();c.start();
        for(int i=0;i<30;i++)sample(c,i,i%2==0?12:-12,i*2);
        c.resetForVinsRestart();assertEquals(0,c.sampleCount());assertEquals(0,c.totalSampleCount());
        assertFalse(c.isReady());
    }
    @Test public void smoothButWrongScaleCannotPassTheRollingWindow(){
        DynamicHeadingCalibrator c=new DynamicHeadingCalibrator();c.start();
        for(int i=0;i<100;i++)sample(c,i,0,i*4);
        assertFalse(c.isReady());assertTrue(c.qualityDetails().contains("尺度不符"));
    }
}
