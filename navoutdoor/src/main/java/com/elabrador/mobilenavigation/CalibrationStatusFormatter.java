package com.elabrador.mobilenavigation;

import java.util.Locale;

/** Builds the user-facing outdoor GPS/VINS trajectory collection panel. */
final class CalibrationStatusFormatter {
    private CalibrationStatusFormatter() {}

    static String format(float gpsAccuracyMeters, long gpsAgeMillis,
                         DynamicHeadingCalibrator calibrator) {
        return format(gpsAccuracyMeters,gpsAgeMillis,calibrator,"");
    }

    static String format(float gpsAccuracyMeters, long gpsAgeMillis,
                         DynamicHeadingCalibrator calibrator, String rejectedReason) {
        boolean hasAccuracy = Float.isFinite(gpsAccuracyMeters) && gpsAccuracyMeters > 0f;
        boolean accuracyAccepted = hasAccuracy
                && gpsAccuracyMeters <= DynamicHeadingCalibrator.MAX_GPS_ACCURACY_METERS;
        String accuracy = hasAccuracy
                ? String.format(Locale.CHINA, "%.1f 米", gpsAccuracyMeters)
                : "暂无有效数据";
        String age = gpsAgeMillis >= 0 && gpsAgeMillis < Long.MAX_VALUE
                ? String.format(Locale.CHINA, "（%.1f 秒前）", gpsAgeMillis / 1000f)
                : "";
        String verdict;
        if(!hasAccuracy)verdict="等待 GPS 定位";
        else if(!isFresh(gpsAgeMillis))verdict="GPS 数据过期或时间异常，暂停采集";
        else if(!rejectedReason.isEmpty())verdict="当前定位不采用："+rejectedReason;
        else if(!accuracyAccepted)verdict="GPS 精度未达标，暂停采集";
        else verdict="GPS 精度达标；采集仍需同步位姿和有效移动";
        String progress = String.format(Locale.CHINA,
                "当前窗口：%d/%d 点 · 累计接收 %d 点\n已采集位移 %.1f 米；仅用最近一段轨迹",
                calibrator.sampleCount(),
                DynamicHeadingCalibrator.MIN_READY_SAMPLES,
                calibrator.totalSampleCount(),
                calibrator.sampledDistanceMeters());
        if (calibrator.isReady()) {
            verdict = "方向轨迹标定已完成并锁定\n当前定位："+verdict;
            progress = String.format(Locale.CHINA, "已采用 %d 个有效轨迹点",
                    calibrator.sampleCount());
        }
        return String.format(Locale.CHINA,
                "方向轨迹采集状态\nGPS 精度：%s%s · 要求 ≤ %.0f 米\n%s\n%s\n%s\n%s",
                accuracy, age, DynamicHeadingCalibrator.MAX_GPS_ACCURACY_METERS,
                verdict, progress, calibrator.status(),calibrator.qualityDetails());
    }

    static boolean isFresh(long ageMillis){return ageMillis>=0 && ageMillis<=5000;}

    static boolean accuracyAccepted(float gpsAccuracyMeters) {
        return Float.isFinite(gpsAccuracyMeters) && gpsAccuracyMeters > 0f
                && gpsAccuracyMeters <= DynamicHeadingCalibrator.MAX_GPS_ACCURACY_METERS;
    }
}
