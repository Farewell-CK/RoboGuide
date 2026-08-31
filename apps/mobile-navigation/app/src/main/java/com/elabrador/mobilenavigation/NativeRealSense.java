package com.elabrador.mobilenavigation;

/** Batch JNI boundary that calls librealsense's exact deprojection implementation. */
final class NativeRealSense {
    static {
        System.loadLibrary("elabrador_native");
    }

    private NativeRealSense() {}

    static native void nativeDeprojectPixels(
            int width, int height, float ppx, float ppy, float fx, float fy,
            int distortionModel, float[] coefficients, float[] pixels,
            float[] depths, float[] xyz);
}
