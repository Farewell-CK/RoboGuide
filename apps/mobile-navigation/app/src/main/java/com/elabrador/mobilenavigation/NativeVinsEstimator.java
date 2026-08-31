package com.elabrador.mobilenavigation;

/** JNI replacement for the original /vins_estimator ROS process boundary. */
final class NativeVinsEstimator {
    static { System.loadLibrary("vins_estimator"); }
    private NativeVinsEstimator() {}

    static native long nativeCreate(int width, int height, double focalLength,
                                    double accNoise, double gyroNoise,
                                    double accRandomWalk, double gyroRandomWalk,
                                    double gravity, double solverTime, int iterations,
                                    double keyframeParallax, boolean estimateTimeOffset,
                                    double timeOffset, int extrinsicMode,
                                    double[] imuFromCameraRotation,
                                    double[] imuFromCameraTranslation);
    static native void nativeDestroy(long handle);
    static native void nativeReset(long handle);

    /** IMU records are t,ax,ay,az,gx,gy,gz; features use tracker records above. */
    static native boolean nativeProcess(long handle, double imageTimestampSeconds,
                                        double[] imu, double[] features);

    static native double nativeCurrentTimeOffset(long handle);

    /** Pose, solver flag/time, followed by current row-major imu_R_camera and imu_T_camera. */
    static native double[] nativeLatestPose(long handle);
}
