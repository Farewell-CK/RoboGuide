package com.elabrador.mobilenavigation;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.ContentResolver;
import android.content.ContentUris;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.os.Bundle;
import android.provider.MediaStore;
import android.os.SystemClock;
import android.util.Log;

import java.io.File;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.FloatBuffer;
import java.nio.IntBuffer;
import java.io.InputStream;
import java.util.Collections;
import java.util.Arrays;
import java.util.Locale;

import ai.onnxruntime.OnnxTensor;
import ai.onnxruntime.OrtEnvironment;
import ai.onnxruntime.OrtProvider;
import ai.onnxruntime.OrtSession;

/** Device-only benchmark. This class is packaged in androidTest, never in the application APK. */
public final class SemanticBenchmarkInstrumentation extends Instrumentation {
    private static final String TAG = "SemanticBench";
    private static final int WIDTH = 640;
    private static final int HEIGHT = 480;
    private static final long MODEL_BYTES = 910448873L;
    private Bundle arguments;

    @Override
    public void onCreate(Bundle arguments) {
        super.onCreate(arguments);
        this.arguments = arguments;
        start();
    }

    @Override
    public void onStart() {
        Bundle result = new Bundle();
        int code = Activity.RESULT_OK;
        try {
            runBenchmarks();
            result.putString("result", "completed; see SemanticBench logcat");
        } catch (Throwable error) {
            code = Activity.RESULT_CANCELED;
            result.putString("error", Log.getStackTraceString(error));
            Log.e(TAG, "benchmark failed", error);
        }
        finish(code, result);
    }

    private void runBenchmarks() throws Exception {
        OrtEnvironment environment = OrtEnvironment.getEnvironment();
        Log.i(TAG, "available providers=" + OrtEnvironment.getAvailableProviders());
        File model = new File(getTargetContext().getFilesDir(),
                "models/mask2former-swinL-mapillary-semantic-640x480.onnx");
        if (!model.isFile() || model.length() != MODEL_BYTES) {
            throw new IllegalStateException("target Swin-L model missing or wrong size: " + model);
        }

        String imagePath = arguments == null ? null : arguments.getString("image_path");
        FloatBuffer input = imagePath == null ? deterministicInput() : imageInput(imagePath);
        Log.i(TAG, imagePath == null ? "input=deterministic 640x480" :
                "input=image " + imagePath + " resized=640x480");
        BenchmarkResult reference;
        try (OrtSession.SessionOptions options = xnnpackOptions(4);
             OrtSession session = environment.createSession(model.getAbsolutePath(), options)) {
            reference = benchmark(environment, session, input, "XNNPACK-4T");
        }
        System.gc();

        if (OrtEnvironment.getAvailableProviders().contains(OrtProvider.WEBGPU)) {
            try (OrtSession.SessionOptions options = new OrtSession.SessionOptions()) {
                options.setOptimizationLevel(OrtSession.SessionOptions.OptLevel.ALL_OPT);
                options.setIntraOpNumThreads(1);
                options.setInterOpNumThreads(1);
                options.addWebGPU(Collections.emptyMap());
                try (OrtSession session = environment.createSession(model.getAbsolutePath(), options)) {
                    BenchmarkResult first = benchmark(environment, session, input, "WEBGPU-1");
                    compare(reference, first, "XNNPACK-4T", "WEBGPU-1");
                    BenchmarkResult second = benchmark(environment, session, input, "WEBGPU-2");
                    compare(first, second, "WEBGPU-1", "WEBGPU-2");
                }
            } catch (Throwable error) {
                Log.e(TAG, "WebGPU unavailable for exact Swin-L graph", error);
            }
        } else {
            Log.w(TAG, "WebGPU provider is not registered in this ONNX Runtime package/device");
        }
    }

    private static OrtSession.SessionOptions xnnpackOptions(int threads) throws Exception {
        OrtSession.SessionOptions options = new OrtSession.SessionOptions();
        options.setOptimizationLevel(OrtSession.SessionOptions.OptLevel.ALL_OPT);
        options.setIntraOpNumThreads(threads);
        options.setInterOpNumThreads(1);
        options.addConfigEntry("session.intra_op.allow_spinning", "0");
        options.addConfigEntry("session.inter_op.allow_spinning", "0");
        options.addXnnpack(Collections.singletonMap(
                "intra_op_num_threads", Integer.toString(threads)));
        return options;
    }

    private static BenchmarkResult benchmark(OrtEnvironment environment, OrtSession session,
                                             FloatBuffer input, String backend) throws Exception {
        input.position(0);
        long started = SystemClock.elapsedRealtimeNanos();
        int[] mask;
        float[] confidence;
        try (OnnxTensor tensor = OnnxTensor.createTensor(
                environment, input, new long[]{1, 3, HEIGHT, WIDTH});
             OrtSession.Result outputs = session.run(Collections.singletonMap("image", tensor))) {
            IntBuffer maskBuffer = ((OnnxTensor) outputs.get(0)).getIntBuffer();
            FloatBuffer confidenceBuffer = ((OnnxTensor) outputs.get(1)).getFloatBuffer();
            mask = new int[maskBuffer.remaining()];
            confidence = new float[confidenceBuffer.remaining()];
            maskBuffer.get(mask);
            confidenceBuffer.get(confidence);
        }
        long millis = (SystemClock.elapsedRealtimeNanos() - started) / 1_000_000L;
        long maskHash = hash(IntBuffer.wrap(mask));
        long confidenceHash = hash(FloatBuffer.wrap(confidence));
        Log.i(TAG, String.format(Locale.US,
                "backend=%s elapsed=%dms mask_hash=%016x confidence_hash=%016x",
                backend, millis, maskHash, confidenceHash));
        int[] histogram = new int[65];
        for (int value : mask) {
            if (value >= 0 && value < histogram.length) {
                histogram[value]++;
            }
        }
        Integer[] order = new Integer[histogram.length];
        for (int i = 0; i < order.length; i++) {
            order[i] = i;
        }
        Arrays.sort(order, (left, right) -> Integer.compare(histogram[right], histogram[left]));
        StringBuilder top = new StringBuilder();
        for (int i = 0; i < 8 && i < order.length; i++) {
            if (histogram[order[i]] == 0) break;
            if (top.length() > 0) top.append(',');
            top.append(order[i]).append(':').append(histogram[order[i]]);
        }
        int center = mask[(HEIGHT / 2) * WIDTH + WIDTH / 2];
        Log.i(TAG, "mask_shape=" + HEIGHT + "x" + WIDTH
                + " center_class=" + center + " top_classes=" + top);
        return new BenchmarkResult(mask, confidence);
    }

    private static void compare(BenchmarkResult left, BenchmarkResult right,
                                String leftName, String rightName) {
        if (left.mask.length != right.mask.length
                || left.confidence.length != right.confidence.length) {
            throw new IllegalStateException("output shape mismatch");
        }
        int maskDifferences = 0;
        double absoluteErrorSum = 0.0;
        float maxAbsoluteError = 0.0f;
        for (int i = 0; i < left.mask.length; i++) {
            if (left.mask[i] != right.mask[i]) {
                maskDifferences++;
            }
        }
        for (int i = 0; i < left.confidence.length; i++) {
            float error = Math.abs(left.confidence[i] - right.confidence[i]);
            absoluteErrorSum += error;
            maxAbsoluteError = Math.max(maxAbsoluteError, error);
        }
        Log.i(TAG, String.format(Locale.US,
                "compare=%s/%s mask_differences=%d/%d confidence_mean_abs=%.9g confidence_max_abs=%.9g",
                leftName, rightName, maskDifferences, left.mask.length,
                absoluteErrorSum / left.confidence.length, maxAbsoluteError));
    }

    private static final class BenchmarkResult {
        final int[] mask;
        final float[] confidence;

        BenchmarkResult(int[] mask, float[] confidence) {
            this.mask = mask;
            this.confidence = confidence;
        }
    }

    private static FloatBuffer deterministicInput() {
        FloatBuffer input = ByteBuffer.allocateDirect(3 * WIDTH * HEIGHT * Float.BYTES)
                .order(ByteOrder.nativeOrder()).asFloatBuffer();
        int plane = WIDTH * HEIGHT;
        for (int y = 0; y < HEIGHT; y++) {
            for (int x = 0; x < WIDTH; x++) {
                int pixel = y * WIDTH + x;
                input.put(pixel, (float) ((x * 17 + y * 3) & 0xff));
                input.put(plane + pixel, (float) ((x * 5 + y * 11) & 0xff));
                input.put(2 * plane + pixel, (float) ((x * 7 + y * 13) & 0xff));
            }
        }
        input.position(0);
        return input;
    }

    private FloatBuffer imageInput(String path) {
        Bitmap decoded = BitmapFactory.decodeFile(path);
        if (decoded == null) {
            ContentResolver resolver = getContext().getContentResolver();
            String[] projection = {MediaStore.Images.Media._ID, MediaStore.Images.Media.DATA};
            String displayName = new File(path).getName();
            try (android.database.Cursor cursor = resolver.query(
                    MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
                    projection, MediaStore.Images.Media.DISPLAY_NAME + "=?",
                    new String[]{displayName}, null)) {
                if (cursor != null && cursor.moveToFirst()) {
                    long id = cursor.getLong(0);
                    android.net.Uri uri = ContentUris.withAppendedId(
                            MediaStore.Images.Media.EXTERNAL_CONTENT_URI, id);
                    try (InputStream stream = resolver.openInputStream(uri)) {
                        if (stream != null) decoded = BitmapFactory.decodeStream(stream);
                    } catch (java.io.IOException ignored) {
                        // The decode error below reports the path consistently.
                    }
                }
            }
        }
        if (decoded == null) {
            throw new IllegalArgumentException("cannot decode image: " + path);
        }
        int[] crop = SemanticSegmenter.modelAspectCrop(
                decoded.getWidth(), decoded.getHeight(), WIDTH, HEIGHT);
        Bitmap cropped = Bitmap.createBitmap(decoded, crop[0], crop[1], crop[2], crop[3]);
        Bitmap bitmap = Bitmap.createScaledBitmap(cropped, WIDTH, HEIGHT, true);
        Log.i(TAG, "decoded=" + decoded.getWidth() + "x" + decoded.getHeight()
                + " crop=" + crop[0] + "," + crop[1] + "," + crop[2] + "x" + crop[3]);
        int[] pixels = new int[WIDTH * HEIGHT];
        bitmap.getPixels(pixels, 0, WIDTH, 0, 0, WIDTH, HEIGHT);
        FloatBuffer input = ByteBuffer.allocateDirect(3 * WIDTH * HEIGHT * Float.BYTES)
                .order(ByteOrder.nativeOrder()).asFloatBuffer();
        int plane = WIDTH * HEIGHT;
        for (int pixel = 0; pixel < pixels.length; pixel++) {
            int argb = pixels[pixel];
            input.put(pixel, (float) ((argb >> 16) & 0xff));
            input.put(plane + pixel, (float) ((argb >> 8) & 0xff));
            input.put(2 * plane + pixel, (float) (argb & 0xff));
        }
        input.position(0);
        if (bitmap != cropped) bitmap.recycle();
        if (cropped != decoded) cropped.recycle();
        decoded.recycle();
        return input;
    }

    private static long hash(IntBuffer values) {
        long hash = 0xcbf29ce484222325L;
        while (values.hasRemaining()) {
            hash ^= values.get();
            hash *= 0x100000001b3L;
        }
        return hash;
    }

    private static long hash(FloatBuffer values) {
        long hash = 0xcbf29ce484222325L;
        while (values.hasRemaining()) {
            hash ^= Float.floatToIntBits(values.get());
            hash *= 0x100000001b3L;
        }
        return hash;
    }
}
