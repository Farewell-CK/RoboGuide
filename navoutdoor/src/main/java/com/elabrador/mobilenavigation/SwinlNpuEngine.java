package com.elabrador.mobilenavigation;

import android.content.Context;
import android.os.SystemClock;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;

/** Shared RGB -> Swin-L -> 65-class mask path for navigation and device measurements. */
final class SwinlNpuEngine implements AutoCloseable {
    static final int WIDTH = BuildConfig.SEMANTIC_MODEL_WIDTH;
    static final int HEIGHT = BuildConfig.SEMANTIC_MODEL_HEIGHT, PIXELS = WIDTH * HEIGHT;
    private final ByteBuffer input = direct(PIXELS * 3 * 2);
    final ByteBuffer maskBytes = direct(PIXELS * 4);
    final ByteBuffer confidenceBytes = direct(PIXELS * 2);
    final int[] mask = new int[PIXELS];
    final float[] confidence = new float[PIXELS];
    final long[] timings = new long[5];
    long modelNanos, decodeNanos;
    private final NativeSwinlPlan runner;
    private final boolean javaIo;

    SwinlNpuEngine(String model, String cache) { this(model, cache, 1, false); }

    SwinlNpuEngine(String model, String cache, int preference, boolean highPriority) {
        this(model, cache, preference, highPriority, false, false, false);
    }

    SwinlNpuEngine(String model, String cache, int preference, boolean highPriority,
                   boolean measureTiming, boolean javaIo, boolean burst) {
        this.javaIo = javaIo;
        runner = preference == 1 && !highPriority && !measureTiming && burst
                ? new NativeSwinlPlan(model, cache)
                : new NativeSwinlPlan(model, cache, preference, highPriority, measureTiming, burst);
    }

    private static ByteBuffer direct(int bytes) {
        return ByteBuffer.allocateDirect(bytes).order(ByteOrder.nativeOrder());
    }

    static File prepareModel(Context context) throws Exception {
        File directory = new File(context.getFilesDir(), "models");
        if (!directory.isDirectory() && !directory.mkdirs()) throw new IllegalStateException("无法创建模型目录");
        File model = new File(directory, new File(BuildConfig.SEMANTIC_MODEL_ASSET).getName());
        if (model.isFile() && model.length() == BuildConfig.SEMANTIC_MODEL_BYTES) return model;
        File temporary = new File(directory, model.getName() + ".tmp");
        try (InputStream in = context.getAssets().open(BuildConfig.SEMANTIC_MODEL_ASSET);
             FileOutputStream out = new FileOutputStream(temporary)) {
            byte[] block = new byte[1024 * 1024];
            int n;
            while ((n = in.read(block)) != -1) out.write(block, 0, n);
        }
        if (temporary.length() != BuildConfig.SEMANTIC_MODEL_BYTES || !temporary.renameTo(model))
            throw new IllegalStateException("Swin-L 模型复制不完整");
        return model;
    }

    void prepareRgb(byte[] rgb, int stride, int xOffset, int yOffset, int cropWidth, int cropHeight) {
        if (cropWidth <= 0 || cropHeight <= 0 || xOffset < 0 || yOffset < 0 ||
                stride < (xOffset + cropWidth) * 3 ||
                (long) (yOffset + cropHeight - 1) * stride + (xOffset + cropWidth) * 3 > rgb.length)
            throw new IllegalArgumentException("invalid RGB frame");
        // The plan includes ImageNet normalization. Feed raw RGB 0..255 FP16 NCHW.
        if (!javaIo) {
            NativeSwinlPlan.nativePrepareRgb(rgb, stride, xOffset, yOffset, cropWidth, cropHeight, WIDTH, HEIGHT, input);
            return;
        }
        for (int y = 0; y < HEIGHT; y++) {
            int sourceY = yOffset + Math.min(cropHeight - 1, y * cropHeight / HEIGHT);
            for (int x = 0; x < WIDTH; x++) {
                int sourceX = xOffset + Math.min(cropWidth - 1, x * cropWidth / WIDTH);
                int source = sourceY * stride + sourceX * 3;
                int pixel = y * WIDTH + x;
                input.putShort(pixel * 2, rgbHalf(rgb[source] & 255));
                input.putShort((PIXELS + pixel) * 2, rgbHalf(rgb[source + 1] & 255));
                input.putShort((PIXELS * 2 + pixel) * 2, rgbHalf(rgb[source + 2] & 255));
            }
        }
    }

    void infer() {
        long started = SystemClock.elapsedRealtimeNanos();
        if (runner.run(input, maskBytes, confidenceBytes, timings) < 0)
            throw new IllegalStateException("Swin-L MDLA 执行失败，查看 SwinLPlan 日志");
        long inferred = SystemClock.elapsedRealtimeNanos();
        if (javaIo) {
            maskBytes.position(0);
            maskBytes.asIntBuffer().get(mask);
            for (int i = 0; i < PIXELS; i++) confidence[i] = halfToFloat(confidenceBytes.getShort(i * 2));
        } else {
            NativeSwinlPlan.nativeDecode(maskBytes, confidenceBytes, mask, confidence);
        }
        modelNanos = inferred - started;
        decodeNanos = SystemClock.elapsedRealtimeNanos() - inferred;
    }

    private static short rgbHalf(int value) {
        if (value == 0) return 0;
        int bits = Float.floatToRawIntBits((float) value);
        return (short) (((bits >>> 13) & 1023) | ((((bits >>> 23) & 255) - 112) << 10));
    }

    static float halfToFloat(short value) {
        int bits = value & 65535, sign = (bits & 32768) << 16;
        int exponent = (bits >>> 10) & 31, mantissa = bits & 1023;
        if (exponent == 0) {
            if (mantissa == 0) return Float.intBitsToFloat(sign);
            while ((mantissa & 1024) == 0) { mantissa <<= 1; exponent--; }
            exponent++; mantissa &= 1023;
        } else if (exponent == 31) return Float.intBitsToFloat(sign | 0x7f800000 | (mantissa << 13));
        return Float.intBitsToFloat(sign | ((exponent + 112) << 23) | (mantissa << 13));
    }

    @Override public void close() { runner.close(); }
}
