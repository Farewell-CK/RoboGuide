package com.elabrador.mobilenavigation;

import java.util.ArrayDeque;
import java.util.Deque;

/** Short TF-style pose buffer used to align delayed semantic output to its camera frame. */
final class VinsPoseHistory {
    private static final int MAX_POSES = 600;
    private static final double MAX_HISTORY_SECONDS = 30.0;
    private final Deque<VinsMono.Pose> poses = new ArrayDeque<>();

    synchronized void add(VinsMono.Pose pose) {
        if (pose == null || !pose.initialized) return;
        VinsMono.Pose last = poses.peekLast();
        if (last != null && pose.timestamp < last.timestamp) return;
        if (last != null && pose.timestamp == last.timestamp) poses.removeLast();
        poses.addLast(pose);
        while (poses.size() > MAX_POSES
                || (poses.size() > 1
                && pose.timestamp - poses.peekFirst().timestamp > MAX_HISTORY_SECONDS)) {
            poses.removeFirst();
        }
    }

    synchronized VinsMono.Pose at(double timestamp) {
        if (poses.isEmpty() || timestamp < poses.peekFirst().timestamp
                || timestamp > poses.peekLast().timestamp) return null;
        VinsMono.Pose before = null;
        for (VinsMono.Pose pose : poses) {
            if (pose.timestamp == timestamp) return pose;
            if (pose.timestamp > timestamp) {
                return VinsMono.Pose.interpolate(before, pose, timestamp);
            }
            before = pose;
        }
        return null;
    }

    synchronized VinsMono.Pose atOrNearest(double timestamp, double maxDeltaSeconds) {
        if (poses.isEmpty() || !Double.isFinite(timestamp) || maxDeltaSeconds < 0.0) return null;
        VinsMono.Pose first = poses.peekFirst();
        VinsMono.Pose last = poses.peekLast();
        if (timestamp < first.timestamp) {
            return first.timestamp - timestamp <= maxDeltaSeconds ? first : null;
        }
        if (timestamp > last.timestamp) {
            return timestamp - last.timestamp <= maxDeltaSeconds ? last : null;
        }
        return at(timestamp);
    }

    synchronized void clear() {
        poses.clear();
    }
}
