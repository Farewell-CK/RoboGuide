package com.elabrador.mobilenavigation;

import com.intel.realsense.librealsense.TimestampDomain;

/** Android equivalent of realsense2_camera::frameSystemTimeSec(). */
final class RealSenseTimestampMapper {
    private boolean hardwareClockInitialized;
    private double cameraTimeBaseMilliseconds;
    private double systemTimeBaseMilliseconds;

    synchronized double toSystemTimeMilliseconds(
            double frameTimeMilliseconds, TimestampDomain domain,
            double currentSystemTimeMilliseconds) {
        if (domain != TimestampDomain.HARDWARE_CLOCK) return frameTimeMilliseconds;
        if (!hardwareClockInitialized) {
            hardwareClockInitialized = true;
            cameraTimeBaseMilliseconds = frameTimeMilliseconds;
            systemTimeBaseMilliseconds = currentSystemTimeMilliseconds;
        }
        return systemTimeBaseMilliseconds
                + frameTimeMilliseconds - cameraTimeBaseMilliseconds;
    }

    synchronized void reset() {
        hardwareClockInitialized = false;
        cameraTimeBaseMilliseconds = 0.0;
        systemTimeBaseMilliseconds = 0.0;
    }
}
