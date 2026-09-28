package com.elabrador.mobilenavigation;

import org.junit.Test;
import static org.junit.Assert.*;

public class CalibrationStatusFormatterTest {
    @Test public void panelShowsCurrentAccuracyThresholdAndProgress() {
        DynamicHeadingCalibrator calibrator = new DynamicHeadingCalibrator();
        calibrator.start();
        String text = CalibrationStatusFormatter.format(6.4f, 900L, calibrator);
        assertTrue(text.contains("6.4 米"));
        assertTrue(text.contains("要求 ≤ 8 米"));
        assertTrue(text.contains("GPS 精度达标"));
        assertTrue(text.contains("0/17"));
        assertFalse(text.contains("0/3"));
    }

    @Test public void panelMakesRejectedAccuracyUnambiguous() {
        DynamicHeadingCalibrator calibrator = new DynamicHeadingCalibrator();
        calibrator.start();
        String text = CalibrationStatusFormatter.format(12.3f, 1500L, calibrator);
        assertTrue(text.contains("12.3 米"));
        assertTrue(text.contains("GPS 精度未达标"));
        assertFalse(CalibrationStatusFormatter.accuracyAccepted(12.3f));
        assertTrue(CalibrationStatusFormatter.accuracyAccepted(8.0f));
    }

    @Test public void panelShowsMissingAccuracy() {
        DynamicHeadingCalibrator calibrator = new DynamicHeadingCalibrator();
        assertTrue(CalibrationStatusFormatter.format(Float.NaN, Long.MAX_VALUE, calibrator)
                .contains("暂无有效数据"));
    }

    @Test public void accurateButStaleGpsMustNotBeShownAsEligible(){
        DynamicHeadingCalibrator c=new DynamicHeadingCalibrator();c.start();
        String text=CalibrationStatusFormatter.format(3,5001,c);
        assertTrue(text.contains("GPS 数据过期"));assertFalse(text.contains("GPS 精度达标"));
        assertTrue(CalibrationStatusFormatter.isFresh(5000));
        assertFalse(CalibrationStatusFormatter.isFresh(-1));
    }

    @Test public void rejectedJumpIsShownEvenWhenAccuracyLooksGood(){
        DynamicHeadingCalibrator c=new DynamicHeadingCalibrator();c.start();
        String text=CalibrationStatusFormatter.format(3,300,c,"GPS 位移突跳，暂不采用");
        assertTrue(text.contains("当前定位不采用：GPS 位移突跳"));
        assertFalse(text.contains("GPS 精度达标"));
    }
}
