package com.elabrador.mobilenavigation;

import java.nio.ByteBuffer;

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

    /** Exact PIDNet argmax and softmax confidence decode on a direct NCHW logits buffer. */
    public static native void nativeDecodePidNet(ByteBuffer logits, int classCount, int plane,
                                                  int[] classMap, int[] mask,
                                                  float[] confidence);

    /** Nearest-neighbor RGB resize and ImageNet NCHW normalization into a direct buffer. */
    public static native void nativePreparePidNet(byte[] rgb, int stride, int xOffset,
                                                   int yOffset, int cropWidth, int cropHeight,
                                                   int modelWidth, int modelHeight,
                                                   ByteBuffer input);
}
