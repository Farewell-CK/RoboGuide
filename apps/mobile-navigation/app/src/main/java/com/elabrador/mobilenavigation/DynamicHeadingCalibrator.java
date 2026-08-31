package com.elabrador.mobilenavigation;

import java.util.ArrayList;
import java.util.List;

/**
 * Aligns the arbitrary horizontal VINS world axes to geographic north. The
 * outdoor mode compares co-moving GPS/VINS displacement; the indoor mode uses
 * the phone heading only at the instant the phone top and D455 optical forward
 * direction are deliberately aligned.
 */
final class DynamicHeadingCalibrator {
    private static final double EARTH_RADIUS_METERS = 6371000.0;
    private static final float MAX_GPS_ACCURACY_METERS = 8f;
    private static final double MIN_GPS_SEGMENT_METERS = 4.0;
    private static final double MIN_VINS_SEGMENT_METERS = 1.5;
    private static final double MIN_SCALE_RATIO = 0.15;
    private static final double MAX_SCALE_RATIO = 5.0;
    private static final int REQUIRED_SAMPLES = 3;
    private static final int SAMPLE_WINDOW_SIZE = 6;
    private static final double MAX_SAMPLE_DEVIATION_DEGREES = 15.0;

    private final List<Double> offsetsDegrees = new ArrayList<>();
    private Sample anchor;
    private boolean collecting;
    private boolean ready;
    private double northOffsetDegrees = Double.NaN;
    private String status = "未标定";

    synchronized void start() {
        offsetsDegrees.clear();
        anchor = null;
        collecting = true;
        ready = false;
        northOffsetDegrees = Double.NaN;
        status = "动态标定中：请带着手机和 D455F 一起直线走动";
    }

    synchronized void resetForVinsRestart() {
        offsetsDegrees.clear();
        anchor = null;
        collecting = false;
        ready = false;
        northOffsetDegrees = Double.NaN;
        status = "VINS 已重启，需要重新动态标定";
    }

    synchronized boolean calibrateAligned(float phoneTrueHeadingDegrees, VinsMono.Pose pose) {
        offsetsDegrees.clear();
        anchor = null;
        collecting = false;
        ready = false;
        northOffsetDegrees = Double.NaN;
        if (!Float.isFinite(phoneTrueHeadingDegrees)) {
            status = "室内同向标定失败：等待手机方向传感器";
            return false;
        }
        if (pose == null || !pose.initialized) {
            status = "室内同向标定失败：等待 VINS 初始化";
            return false;
        }
        double cameraForwardVinsBearing = -Math.toDegrees(pose.egoRightAxisYawRadians());
        northOffsetDegrees = normalizeDegrees(
                phoneTrueHeadingDegrees - cameraForwardVinsBearing);
        ready = true;
        status = String.format(java.util.Locale.CHINA,
                "室内同向标定完成：北向偏角 %+.1f°", northOffsetDegrees);
        return true;
    }

    synchronized void update(double latitude, double longitude, float accuracyMeters,
                             double vinsX, double vinsY, boolean vinsInitialized) {
        if (!collecting || ready) return;
        if (!vinsInitialized) {
            status = "动态标定中：等待 VINS 初始化";
            return;
        }
        if (!Float.isFinite(accuracyMeters) || accuracyMeters > MAX_GPS_ACCURACY_METERS) {
            status = "动态标定中：等待 GPS 精度优于 8 m";
            return;
        }
        Sample current = new Sample(latitude, longitude, vinsX, vinsY);
        if (anchor == null) {
            anchor = current;
            status = "动态标定中：已记录起点，请共同直线移动至少 4 m";
            return;
        }

        double gpsEast = longitudeDeltaMeters(anchor.longitude, current.longitude, anchor.latitude);
        double gpsNorth = latitudeDeltaMeters(anchor.latitude, current.latitude);
        double gpsDistance = Math.hypot(gpsEast, gpsNorth);
        double vinsXDelta = current.vinsX - anchor.vinsX;
        double vinsYDelta = current.vinsY - anchor.vinsY;
        double vinsDistance = Math.hypot(vinsXDelta, vinsYDelta);
        if (gpsDistance < MIN_GPS_SEGMENT_METERS || vinsDistance < MIN_VINS_SEGMENT_METERS) {
            status = "动态标定中：共同移动 " + Math.round(Math.min(gpsDistance, vinsDistance))
                    + " m / 4 m";
            return;
        }
        double scaleRatio = vinsDistance / gpsDistance;
        if (scaleRatio < MIN_SCALE_RATIO || scaleRatio > MAX_SCALE_RATIO) {
            anchor = current;
            status = "动态标定中：GPS 与 VINS 位移不一致，已重取采样起点";
            return;
        }

        double gpsBearing = Math.toDegrees(Math.atan2(gpsEast, gpsNorth));
        double vinsBearing = Math.toDegrees(Math.atan2(vinsXDelta, vinsYDelta));
        offsetsDegrees.add(normalizeDegrees(gpsBearing - vinsBearing));
        while (offsetsDegrees.size() > SAMPLE_WINDOW_SIZE) offsetsDegrees.remove(0);
        anchor = current;
        List<Double> consistentSamples = mostConsistentSamples(offsetsDegrees);
        double mean = circularMean(consistentSamples);
        double worstDeviation = 0.0;
        for (double offset : consistentSamples) {
            worstDeviation = Math.max(worstDeviation, Math.abs(normalizeDegrees(offset - mean)));
        }
        if (consistentSamples.size() >= REQUIRED_SAMPLES
                && worstDeviation <= MAX_SAMPLE_DEVIATION_DEGREES) {
            ready = true;
            collecting = false;
            northOffsetDegrees = mean;
            status = String.format(java.util.Locale.CHINA,
                    "动态标定完成：北向偏角 %+.1f°，样本 %d", northOffsetDegrees,
                    consistentSamples.size());
        } else {
            status = String.format(java.util.Locale.CHINA,
                    "动态标定中：一致样本 %d/%d（窗口 %d），偏差 %.1f°",
                    consistentSamples.size(), REQUIRED_SAMPLES, offsetsDegrees.size(),
                    worstDeviation);
        }
    }

    synchronized void waitForTimeAlignedVinsPose() {
        if (collecting && !ready) status = "动态标定中：等待 GPS 时刻对应的 VINS 位姿";
    }

    synchronized void waitForFreshGps() {
        if (collecting && !ready) status = "动态标定中：等待新鲜的 GPS 定位";
    }

    synchronized boolean isReady() { return ready; }

    synchronized String status() { return status; }

    synchronized double northOffsetDegrees() { return northOffsetDegrees; }

    synchronized float relativeTargetDegrees(float geographicBearingDegrees, VinsMono.Pose pose) {
        if (!ready || pose == null || !pose.initialized) return Float.NaN;
        // VINS bearing convention used above: +Y is 0 degrees and +X is +90 degrees.
        double targetVinsBearing = normalizeDegrees(geographicBearingDegrees - northOffsetDegrees);
        double cameraForwardVinsBearing = -Math.toDegrees(pose.egoRightAxisYawRadians());
        return (float) normalizeDegrees(targetVinsBearing - cameraForwardVinsBearing);
    }

    static double normalizeDegrees(double degrees) {
        return ((degrees + 540.0) % 360.0) - 180.0;
    }

    private static double circularMean(List<Double> angles) {
        double sin = 0.0, cos = 0.0;
        for (double angle : angles) {
            double radians = Math.toRadians(angle);
            sin += Math.sin(radians);
            cos += Math.cos(radians);
        }
        return Math.toDegrees(Math.atan2(sin, cos));
    }

    private static List<Double> mostConsistentSamples(List<Double> samples) {
        List<Double> best = new ArrayList<>();
        double bestDeviation = Double.POSITIVE_INFINITY;
        int combinations = 1 << samples.size();
        for (int mask = 1; mask < combinations; mask++) {
            List<Double> candidate = new ArrayList<>();
            for (int index = 0; index < samples.size(); index++) {
                if ((mask & (1 << index)) != 0) candidate.add(samples.get(index));
            }
            double mean = circularMean(candidate);
            double worstDeviation = 0.0;
            for (double value : candidate) {
                worstDeviation = Math.max(worstDeviation,
                        Math.abs(normalizeDegrees(value - mean)));
            }
            if (worstDeviation <= MAX_SAMPLE_DEVIATION_DEGREES
                    && (candidate.size() > best.size()
                    || (candidate.size() == best.size() && worstDeviation < bestDeviation))) {
                best = candidate;
                bestDeviation = worstDeviation;
            }
        }
        return best;
    }

    private static double latitudeDeltaMeters(double firstLatitude, double secondLatitude) {
        return Math.toRadians(secondLatitude - firstLatitude) * EARTH_RADIUS_METERS;
    }

    private static double longitudeDeltaMeters(double firstLongitude, double secondLongitude,
                                               double referenceLatitude) {
        return Math.toRadians(secondLongitude - firstLongitude) * EARTH_RADIUS_METERS
                * Math.cos(Math.toRadians(referenceLatitude));
    }

    private static final class Sample {
        final double latitude, longitude, vinsX, vinsY;
        Sample(double latitude, double longitude, double vinsX, double vinsY) {
            this.latitude = latitude;
            this.longitude = longitude;
            this.vinsX = vinsX;
            this.vinsY = vinsY;
        }
    }
}
