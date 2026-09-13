package com.elabrador.mobilenavigation;

import java.util.Arrays;
import java.util.concurrent.TimeUnit;

/** Conservative cost hysteresis for nearly unchanged projection frames.
 * Only new semantic/depth frames count as evidence, never UI or pose-only refreshes.
 * This does not estimate stationarity or modify VINS poses.
 */
final class LocalMapStabilizer {
    static final long CONFIRM_NANOS = TimeUnit.MILLISECONDS.toNanos(400);
    static final long MAX_GAP_NANOS = TimeUnit.MILLISECONDS.toNanos(750);
    private VinsMono.Pose anchor;
    private long[] lowerSince;
    private int[] lowerFrames;
    private boolean[] missing;
    private Snapshot previous;

    static final class Snapshot {
        final int[] costs;
        final VinsMono.Pose pose;
        final long observedAt;
        final double frameTime;

        Snapshot(int[] costs, VinsMono.Pose pose, long now, double frameTime) {
            this.costs = costs;
            this.pose = pose;
            this.observedAt = now;
            this.frameTime = frameTime;
        }

        /** Read-only overlay: repeated projections cannot confirm an obstacle removal. */
        int[] reproject(int[] raw, VinsMono.Pose current, long now) {
            // Sensor/map freshness is checked by SemanticSegmenter. Wall time alone
            // must not lower a held obstacle without another real observation.
            if (!compatible(pose, current) || now < observedAt
                    || raw.length != costs.length) return raw;
            int[] result = raw.clone();
            for (int i = 0; i < result.length; i++) {
                // Unknown must never inherit historical free space.
                if ((raw[i] >= 0 || Env.isObstacleCost(costs[i])) && costs[i] > raw[i]) {
                    result[i] = costs[i];
                }
            }
            return result;
        }
    }

    Snapshot update(int[] raw, VinsMono.Pose pose, long now, double frameTime) {
        if (previous != null && frameTime <= previous.frameTime) return previous;
        if (previous == null || raw.length != previous.costs.length
                || !compatible(anchor, pose) || now < previous.observedAt
                || now - previous.observedAt > MAX_GAP_NANOS) {
            anchor = pose;
            lowerSince = new long[raw.length];
            Arrays.fill(lowerSince, -1L);
            lowerFrames = new int[raw.length];
            missing = new boolean[raw.length];
            return previous = new Snapshot(raw.clone(), pose, now, frameTime);
        }
        int[] result = raw.clone();
        for (int i = 0; i < raw.length; i++) {
            int old = previous.costs[i];
            boolean falling = old > raw[i] && (raw[i] >= 0 || Env.isObstacleCost(old));
            if (!falling) {
                lowerSince[i] = -1L;
                lowerFrames[i] = 0;
                continue; // Increases, new obstacles and loss of free-space evidence: immediate.
            }
            // A missing voxel is not a positive observation of free space.
            if (lowerSince[i] < 0L || missing[i] != (raw[i] < 0)) {
                lowerSince[i] = now;
                lowerFrames[i] = 0;
            }
            missing[i] = raw[i] < 0;
            lowerFrames[i]++;
            if (now - lowerSince[i] < CONFIRM_NANOS || lowerFrames[i] < 3) {
                result[i] = old;
            } else {
                lowerSince[i] = -1L;
                lowerFrames[i] = 0;
            }
        }
        return previous = new Snapshot(result, pose, now, frameTime);
    }

    void clear() {
        previous = null;
        anchor = null;
        lowerSince = null;
        lowerFrames = null;
        missing = null;
    }

    private static boolean compatible(VinsMono.Pose a, VinsMono.Pose b) {
        if (a == null || b == null || !a.initialized || !b.initialized) return false;
        double angle = b.egoRightAxisYawRadians() - a.egoRightAxisYawRadians();
        angle = Math.atan2(Math.sin(angle), Math.cos(angle));
        // Compare against the original anchor, not the previous frame: slow movement
        // cannot accumulate indefinitely inside a per-frame deadband. At 7.5 m the
        // angular tolerance corresponds to <2 cm, well below the 20 cm grid size.
        return Math.hypot(b.x - a.x, b.y - a.y) <= 0.02
                && Math.abs(b.z - a.z) <= 0.02
                && Math.abs(angle) <= Math.toRadians(0.15);
    }
}
