package com.elabrador.mobilenavigation;

/** JNI boundary for the source OctoMap/semantic-octree implementation. */
public final class NativeOctomap {
    static {
        System.loadLibrary("elabrador_native");
    }

    private NativeOctomap() {}

    public static native long nativeCreate(float resolution);
    public static native void nativeDestroy(long handle);
    public static native void nativeClear(long handle);
    public static native int nativeInsert(long handle, float[] xyz, int[] semanticRgb,
                                          float[] confidence, float[] sensorToWorld);
    public static native int nativeLeafCount(long handle);
    /** Flat records: x,y,z,occupancy,r,g,b,semanticConfidence. */
    public static native float[] nativeExportLeafs(long handle);
}
