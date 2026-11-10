package com.elabrador.mobilenavigation;

import java.nio.ByteBuffer;

/** Persistent Swin-L v4 plan runner, locked to MediaTek MDLA. */
final class NativeSwinlPlan implements AutoCloseable {
    static {
        System.loadLibrary("swinl_plan");
    }

    private long handle;

    NativeSwinlPlan(String planPath, String cacheDirectory) {
        handle = nativeCreate(planPath, cacheDirectory);
        if (handle == 0L) throw new IllegalStateException("Swin-L MDLA plan creation failed");
    }

    NativeSwinlPlan(String planPath, String cacheDirectory, int preference, boolean highPriority,
                    boolean measureTiming, boolean burst) {
        handle = nativeCreateTuned(planPath, cacheDirectory, preference, highPriority, measureTiming, burst);
        if (handle == 0L) throw new IllegalStateException("Swin-L tuned MDLA plan creation failed");
    }

    synchronized long run(ByteBuffer input, ByteBuffer mask, ByteBuffer confidence,
                           long[] timings) {
        if (handle == 0L) throw new IllegalStateException("Swin-L runner is closed");
        return nativeRun(handle, input, mask, confidence, timings);
    }

    @Override public synchronized void close() {
        if (handle != 0L) {
            nativeDestroy(handle);
            handle = 0L;
        }
    }

    private static native long nativeCreate(String planPath, String cacheDirectory);
    private static native long nativeCreateTuned(String planPath, String cacheDirectory,
                                                 int preference, boolean highPriority, boolean measureTiming, boolean burst);
    static native void nativePrepareRgb(byte[] rgb, int stride, int xOffset, int yOffset,
                                        int cropWidth, int cropHeight, int outputWidth, int outputHeight, ByteBuffer output);
    static native void nativeDecode(ByteBuffer maskBytes, ByteBuffer confidenceBytes,
                                    int[] mask, float[] confidence);
    private static native long nativeRun(long handle, ByteBuffer input, ByteBuffer mask,
                                         ByteBuffer confidence, long[] timings);
    private static native void nativeDestroy(long handle);
}
