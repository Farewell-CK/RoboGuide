package com.elabrador.mobilenavigation;

import org.junit.Test;
import static org.junit.Assert.*;

public class Calibration17Test {
    private static final double M=180.0/Math.PI/6371000.0;
    private DynamicHeadingCalibrator start(){
        DynamicHeadingCalibrator c=new DynamicHeadingCalibrator();c.start();return c;
    }
    private void point(DynamicHeadingCalibrator c,double north,double east,double x,double y){
        c.update(34+north*M,113+east*M/Math.cos(Math.toRadians(34)),3,x,y,true);
    }

    @Test public void constantGpsOffsetDoesNotRotateTheFit(){
        DynamicHeadingCalibrator c=start();
        for(int i=0;i<17;i++)point(c,30+i*2,20,0,i*2);
        assertTrue(c.isReady());assertEquals(0,c.northOffsetDegrees(),.1);
    }

    @Test public void laterBadGpsCannotChangeLockedDirectionAndResetInvalidatesIt(){
        DynamicHeadingCalibrator c=start();
        for(int i=0;i<17;i++)point(c,i*2,0,0,i*2);
        double offset=c.northOffsetDegrees();
        for(int i=17;i<60;i++)point(c,0,i*2,0,i*2);
        assertEquals(offset,c.northOffsetDegrees(),0);assertEquals(17,c.sampleCount());
        c.resetForVinsRestart();assertFalse(c.isReady());assertEquals(0,c.sampleCount());
        assertEquals(0,c.sampledDistanceMeters(),0);
    }

    @Test public void curveIsAllowedWhenGpsAndVinsAgree(){
        DynamicHeadingCalibrator c=start();
        for(int i=0;i<17;i++){
            double angle=i*.09, east=24*Math.sin(angle),north=24*(1-Math.cos(angle));
            point(c,north,east,east,north);
        }
        assertTrue(c.qualityDetails(),c.isReady());assertEquals(0,c.northOffsetDegrees(),.1);
    }

    @Test public void failureNumbersRemainVisibleThroughSynchronizationWaits(){
        DynamicHeadingCalibrator c=start();
        for(int i=0;i<17;i++)point(c,i*2,i%2==0?8:-8,0,i*2);
        assertFalse(c.isReady());
        String quality=c.qualityDetails();
        assertTrue(quality.contains("上次未通过"));assertTrue(quality.contains("残差"));
        assertTrue(quality.contains("30 米"));
        c.waitForTimeAlignedVinsPose();c.waitForFreshGps();
        point(c,32,8,0,32.2); // insufficient new movement must not erase diagnosis
        assertEquals(quality,c.qualityDetails());
        assertTrue(CalibrationStatusFormatter.format(3,100,c).contains("上次未通过"));
    }

    @Test public void unchangedPositionCannotFillSeventeenPoints(){
        DynamicHeadingCalibrator c=start();
        for(int i=0;i<100;i++)point(c,i*2,0,0,0);
        assertFalse(c.isReady());assertEquals(1,c.sampleCount());
    }

    @Test public void seventeenSamplesAreNotForcedSuccessOnFrozenGps(){
        DynamicHeadingCalibrator c=start();
        for(int i=0;i<17;i++)point(c,0,0,0,i*2);
        assertFalse(c.isReady());assertTrue(c.qualityDetails().contains("GPS 轨迹几乎没有移动"));
    }
}
