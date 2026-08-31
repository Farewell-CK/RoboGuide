package com.elabrador.mobilenavigation;

/** JNI replacement for the original /feature_tracker ROS process boundary. */
final class NativeVinsFeatureTracker {
    static { System.loadLibrary("vins_feature_tracker"); }
    private NativeVinsFeatureTracker() {}

    static native long nativeCreate(int width, int height, double fx, double fy,
                                    double cx, double cy, int distortionModel,
                                    double[] distortionCoefficients,
                                    int maxCount, int minDistance,
                                    double fundamentalThreshold, boolean equalize);
    static native void nativeDestroy(long handle);
    static native void nativeReset(long handle);
    static native boolean nativeConsumeRestart(long handle);

    /** Flat source feature records: id,x,y,z,u,v,velocityX,velocityY. */
    static native double[] nativeTrack(long handle, byte[] grayscale,
                                       int width, int height, int stride,
                                       double timestampSeconds);
}
