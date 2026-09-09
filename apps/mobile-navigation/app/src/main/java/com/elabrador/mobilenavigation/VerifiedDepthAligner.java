package com.elabrador.mobilenavigation;

import android.util.Log;
import com.intel.realsense.librealsense.*;
import java.util.Arrays;

/** Stream-local calibration cache. Any reference mismatch disables it for this stream. */
final class VerifiedDepthAligner implements AutoCloseable {
    private long handle;
    private long frames;
    private boolean rejected;
    private float[] calibration;
    private float[] colorCalibration, rotation, translation;
    private int outputWidth, outputHeight;
    private int validatedFrames;
    private final boolean enabled = "PIDNET_NEURON".equals(BuildConfig.SEMANTIC_MODEL_KIND);

    DepthImage process(FrameSet frameset, DepthFrame raw, Frame color, Align reference)
            throws Exception {
        frames++;
        DepthImage candidate = null;
        long start = System.nanoTime();
        if (enabled && !rejected) {
            try {
                DepthImage source = DepthImage.copy(raw);
                if (handle == 0L) {
                    try (VideoStreamProfile depthProfile = raw.getProfile();
                         StreamProfile colorProfile = color.getProfile()) {
                        VideoStreamProfile video = colorProfile.as(Extension.VIDEO_PROFILE);
                        Intrinsic target = video.getIntrinsic();
                        Extrinsic transform = depthProfile.getExtrinsicTo(colorProfile);
                        calibration = pack(depthProfile.getIntrinsic());
                        colorCalibration = pack(target);
                        rotation = transform.getRotation();
                        translation = transform.getTranslation();
                        outputWidth = video.getWidth();
                        outputHeight = video.getHeight();
                        Log.i("DepthAlign", "ALIGN_CALIBRATION frame=" + frames
                                + " color=" + Arrays.toString(colorCalibration)
                                + " translation=" + Arrays.toString(translation));
                        handle = nativeCreate(calibration, colorCalibration, rotation, translation);
                        validatedFrames = 0;
                    }
                }
                if (handle == 0L) throw new IllegalStateException("Depth alignment creation failed");
                byte[] result = new byte[outputWidth * outputHeight * 2];
                nativeAlign(handle, source.data, source.stride, source.units, result, true);
                candidate = new DepthImage(result, outputWidth, outputHeight,
                        outputWidth * 2, source.units);
            } catch (RuntimeException error) {
                rejected = true;
                Log.e("DepthAlign", "Cached alignment disabled; using SDK", error);
            }
        }
        long optimized = System.nanoTime();
        // Compare startup frames and periodically audit the exact frame actually published.
        if (candidate == null || validatedFrames < 30 || frames % 120 == 0) {
            DepthImage expected;
            int expectedNumber;
            try (FrameSet aligned = frameset.applyFilter(reference);
                 Frame depth = aligned.first(StreamType.DEPTH)) {
                expectedNumber = depth.getNumber();
                expected = DepthImage.copy(depth.as(Extension.DEPTH_FRAME));
            }
            if (candidate != null) {
                if (candidate.width != expected.width || candidate.height != expected.height
                        || candidate.stride != expected.stride || candidate.units != expected.units
                        || raw.getNumber() != expectedNumber) {
                    rejected = true;
                    Log.e("DepthAlign", "Frame geometry/identity mismatch; using SDK");
                    return expected;
                }
                int differences = 0;
                for (int i = 0; i < expected.data.length; i += 2) {
                    if (candidate.data[i] != expected.data[i]
                            || candidate.data[i + 1] != expected.data[i + 1]) differences++;
                }
                Log.i("DepthAlign", "ALIGN_VERIFY frame=" + frames + " differing_pixels="
                        + differences + " fast_ms=" + (optimized - start) / 1e6
                        + " sdk_ms=" + (System.nanoTime() - optimized) / 1e6
                        + " raw_frame=" + raw.getNumber() + " sdk_frame=" + expectedNumber);
                if (!Arrays.equals(candidate.data, expected.data)) {
                    DepthImage source = DepthImage.copy(raw);
                    byte[] direct = new byte[candidate.data.length];
                    nativeAlign(handle, source.data, source.stride, source.units, direct, false);
                    int directDifferences = 0;
                    for (int i = 0; i < direct.length; i += 2) {
                        if (direct[i] != candidate.data[i] || direct[i + 1] != candidate.data[i + 1])
                            directDifferences++;
                    }
                    Log.e("DepthAlign", "DIRECT_VS_CACHE differing_pixels=" + directDifferences);
                    try (VideoStreamProfile profile = raw.getProfile()) {
                        Log.e("DepthAlign", "CALIBRATION initial=" + Arrays.toString(calibration)
                                + " current=" + Arrays.toString(pack(profile.getIntrinsic()))
                                + " units=" + source.units + " sdk_units=" + expected.units);
                    }
                    int nonzero = 0, sdkNonzero = 0, first = -1;
                    for (int i = 0; i < direct.length; i += 2) {
                        if (direct[i] != 0 || direct[i + 1] != 0) nonzero++;
                        if (expected.data[i] != 0 || expected.data[i + 1] != 0) sdkNonzero++;
                        if (first < 0 && (direct[i] != expected.data[i]
                                || direct[i + 1] != expected.data[i + 1])) first = i / 2;
                    }
                    Log.e("DepthAlign", "DIFFERENCE candidate_valid=" + nonzero
                            + " sdk_valid=" + sdkNonzero + " first_pixel=" + first);
                    rejected = true;
                    Log.e("DepthAlign", "SDK mismatch; cached path disabled for this stream");
                } else {
                    int validPixels = 0;
                    for (int i = 0; i < expected.data.length; i += 2)
                        if (expected.data[i] != 0 || expected.data[i + 1] != 0) validPixels++;
                    if (validPixels >= 10000) validatedFrames++;
                }
            }
            return expected;
        }
        return candidate;
    }

    private static float[] pack(Intrinsic intrinsics) {
        float[] values = new float[12];
        values[0] = intrinsics.getWidth(); values[1] = intrinsics.getHeight();
        values[2] = intrinsics.getPpx(); values[3] = intrinsics.getPpy();
        values[4] = intrinsics.getFx(); values[5] = intrinsics.getFy();
        values[6] = intrinsics.getModel().value();
        System.arraycopy(intrinsics.getCoeffs(), 0, values, 7, 5);
        return values;
    }

    @Override public void close() {
        if (handle != 0L) nativeDestroy(handle);
        handle = 0L;
        outputWidth = outputHeight = 0;
    }
    static { System.loadLibrary("elabrador_native"); }
    private static native long nativeCreate(float[] depth, float[] color, float[] rotation,
                                            float[] translation);
    private static native void nativeAlign(long handle, byte[] depth, int stride, float units,
                                           byte[] output, boolean cached);
    private static native void nativeDestroy(long handle);
}
