package com.elabrador.mobilenavigation;

import java.nio.ByteBuffer;

/** JNI facade for the device-only MediaTek TFLite Shim benchmark. */
public final class NativeNeuronShim {
    static {
        System.loadLibrary("elabrador_native");
    }

    private NativeNeuronShim() {}

    /** Runs the vendor interpreter without changing the production semantic pipeline. */
    public static native String nativeBenchmark(String modelPath, int warmupRuns, int runs,
                                                boolean allowFp16);

    /** Creates one persistent vendor interpreter for the real-time semantic pipeline. */
    public static native long nativeCreate(String modelPath, boolean allowFp16);

    /** Runs one frame and returns the synchronous vendor invocation time in nanoseconds. */
    public static native long nativeRun(long handle, ByteBuffer input, ByteBuffer output);

    public static native void nativeDestroy(long handle);
}
