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
import java.nio.LongBuffer;
import java.io.InputStream;
import java.util.Collections;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Locale;

import ai.onnxruntime.OnnxTensor;
import ai.onnxruntime.OrtEnvironment;
import ai.onnxruntime.OrtProvider;
import ai.onnxruntime.OrtSession;
import org.tensorflow.lite.Interpreter;
import org.tensorflow.lite.gpu.GpuDelegate;
import org.tensorflow.lite.nnapi.NnApiDelegate;

/** Device-only benchmark. This class is packaged in androidTest, never in the application APK. */
public final class SemanticBenchmarkInstrumentation extends Instrumentation {
    private static final String TAG = "SemanticBench";
    private static final int WIDTH = 640;
    private static final int HEIGHT = 480;
    private static final long MODEL_BYTES = 910377637L;
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
        String mode = arguments == null ? null : arguments.getString("mode");
        if ("neuron-probe".equals(mode)) {
            Log.i(TAG, NativeNeuronProbe.nativeProbe());
            return;
        }
        OrtEnvironment environment = OrtEnvironment.getEnvironment();
        Log.i(TAG, "available providers=" + OrtEnvironment.getAvailableProviders());
        String modelName = arguments == null ? null : arguments.getString("model_name");
        if (modelName == null || modelName.isEmpty()) {
            modelName = ("pidnet-neuron".equals(mode) || "pidnet-gpu".equals(mode)
                    || "pidnet-nnapi".equals(mode))
                    ? "pidnet-s-cityscapes-1024.tflite"
                    : "mask2former-swinL-mapillary-semantic-640x480.onnx";
        }
        File model = new File(new File(getTargetContext().getFilesDir(), "models"), modelName);
        boolean productionModel = "mask2former-swinL-mapillary-semantic-640x480.onnx"
                .equals(modelName);
        if (!model.isFile() || (productionModel && model.length() != MODEL_BYTES)) {
            throw new IllegalStateException("target Swin-L model missing or wrong size: " + model);
        }
        Log.i(TAG, "model=" + modelName + " bytes=" + model.length());

        String imagePath = arguments == null ? null : arguments.getString("image_path");
        if ("pidnet-gpu".equals(mode) || "pidnet-nnapi".equals(mode)) {
            runPidNetBenchmark(model, imagePath, mode);
            return;
        }
        if ("pidnet-neuron".equals(mode)) {
            Log.i(TAG, NativeNeuronShim.nativeBenchmark(model.getAbsolutePath(), 2, 5,
                    arguments != null && "true".equalsIgnoreCase(
                            arguments.getString("precision_loss"))));
            return;
        }
        if (mode != null && mode.startsWith("segformer-")) {
            runSegformerBenchmark(environment, model, imagePath, mode);
            return;
        }
        FloatBuffer input = imagePath == null ? deterministicInput() : imageInput(imagePath);
        Log.i(TAG, imagePath == null ? "input=deterministic 640x480" :
                "input=image " + imagePath + " resized=640x480");
        if (!OrtEnvironment.getAvailableProviders().contains(OrtProvider.WEBGPU)) {
            Log.w(TAG, "WebGPU provider is not registered in this ONNX Runtime package/device");
            return;
        }

        if ("profile".equals(mode)) {
            profileWebGpu(environment, model, input);
            return;
        }
        if ("nnapi".equals(mode) || "nnapi-mixed-fp16".equals(mode)) {
            runCpuProvider(environment, model, input,
                    "nnapi-mixed-fp16".equals(mode) ? "NNAPI-MIXED-FP16" : "NNAPI");
            return;
        }
        if ("xnnpack".equals(mode)) {
            runCpuProvider(environment, model, input, "XNNPACK");
            return;
        }

        Map<String, Map<String, String>> variants = new LinkedHashMap<>();
        variants.put("default", Collections.emptyMap());
        variants.put("high-performance", options(
                "powerPreference", "high-performance"));
        variants.put("cache", options(
                "storageBufferCacheMode", "bucket",
                "uniformBufferCacheMode", "bucket",
                "defaultBufferCacheMode", "bucket"));
        variants.put("validation-off", options(
                "validationMode", "disabled"));
        variants.put("nhwc", options(
                "preferredLayout", "NHWC"));
        variants.put("graph-capture", options(
                "enableGraphCapture", "1"));
        variants.put("combined", options(
                "powerPreference", "high-performance",
                "storageBufferCacheMode", "bucket",
                "uniformBufferCacheMode", "bucket",
                "defaultBufferCacheMode", "bucket",
                "validationMode", "disabled"));
        String variantName = mode == null ? "default" : mode;
        Map<String, String> providerOptions = variants.get(variantName);
        if (providerOptions == null) {
            throw new IllegalArgumentException("unknown mode=" + variantName);
        }
        runWebGpuVariant(environment, model, input, "WEBGPU-" + variantName,
                providerOptions, null);
    }

    private void runPidNetBenchmark(File model, String imagePath, String mode) throws Exception {
        if (imagePath == null || imagePath.isEmpty()) {
            throw new IllegalArgumentException(mode + " requires image_path");
        }
        final int width = intArgument("input_size", 1024);
        final int height = width;
        final int classCount = 19;
        verifyNativePidNetPreprocess();
        FloatBuffer input = normalizedImageInput(imagePath, width, height);
        int[] identityClassMap = new int[classCount];
        for (int i = 0; i < classCount; i++) identityClassMap[i] = i;
        boolean precisionLoss = arguments != null
                && "true".equalsIgnoreCase(arguments.getString("precision_loss"));
        AutoCloseable delegate;
        Interpreter.Options interpreterOptions = new Interpreter.Options();
        NnApiDelegate nnapiDelegate = null;
        String backend;
        if ("pidnet-nnapi".equals(mode)) {
            NnApiDelegate.Options delegateOptions = new NnApiDelegate.Options()
                    .setExecutionPreference(
                            NnApiDelegate.Options.EXECUTION_PREFERENCE_SUSTAINED_SPEED)
                    .setUseNnapiCpu(false)
                    .setAllowFp16(precisionLoss)
                    .setCacheDir(getTargetContext().getCodeCacheDir().getAbsolutePath())
                    .setModelToken("pidnet-s-cityscapes-1024");
            String accelerator = arguments == null
                    ? null : arguments.getString("accelerator");
            if (accelerator != null && !accelerator.isEmpty()) {
                delegateOptions.setAcceleratorName(accelerator);
                Log.i(TAG, "requested_nnapi_accelerator=" + accelerator);
            }
            nnapiDelegate = new NnApiDelegate(delegateOptions);
            delegate = nnapiDelegate;
            interpreterOptions.addDelegate(nnapiDelegate);
            backend = "PIDNET-NNAPI-" + (precisionLoss ? "FP16" : "FP32");
        } else {
            GpuDelegate.Options delegateOptions = new GpuDelegate.Options();
            delegateOptions.setPrecisionLossAllowed(precisionLoss);
            GpuDelegate gpuDelegate = new GpuDelegate(delegateOptions);
            delegate = gpuDelegate;
            interpreterOptions.addDelegate(gpuDelegate);
            backend = "PIDNET-GPU-" + (precisionLoss ? "FP16" : "FP32");
        }
        try (Interpreter interpreter = new Interpreter(model, interpreterOptions)) {
            if (width != 1024) {
                interpreter.resizeInput(0, new int[]{1, 3, height, width});
                interpreter.allocateTensors();
            }
            int[] outputShape = interpreter.getOutputTensor(0).shape();
            if (outputShape.length != 4 || outputShape[0] != 1
                    || outputShape[1] != classCount) {
                throw new IllegalStateException("unexpected PIDNet output "
                        + Arrays.toString(outputShape));
            }
            final int outputHeight = outputShape[2];
            final int outputWidth = outputShape[3];
            ByteBuffer outputBytes = ByteBuffer.allocateDirect(
                    classCount * outputWidth * outputHeight * Float.BYTES)
                    .order(ByteOrder.nativeOrder());
            FloatBuffer output = outputBytes.asFloatBuffer();
            Log.i(TAG, "pidnet_shape input=" + width + "x" + height
                    + " output=" + outputWidth + "x" + outputHeight);
            for (int iteration = 0; iteration < 7; iteration++) {
                input.position(0);
                outputBytes.position(0);
                long started = SystemClock.elapsedRealtimeNanos();
                interpreter.run(input, outputBytes);
                long inferred = SystemClock.elapsedRealtimeNanos();
                int plane = outputWidth * outputHeight;
                int[] expectedLabels = new int[plane];
                for (int pixel = 0; pixel < plane; pixel++) {
                    int bestClass = 0;
                    float best = output.get(pixel);
                    for (int classId = 1; classId < classCount; classId++) {
                        float value = output.get(classId * plane + pixel);
                        if (value > best) {
                            best = value;
                            bestClass = classId;
                        }
                    }
                    expectedLabels[pixel] = bestClass;
                }
                int[] labels = new int[plane];
                float[] confidence = new float[plane];
                outputBytes.position(0);
                long nativeStarted = SystemClock.elapsedRealtimeNanos();
                NativeOctomap.nativeDecodePidNet(outputBytes, classCount, plane,
                        identityClassMap, labels, confidence);
                long decoded = SystemClock.elapsedRealtimeNanos();
                if (!Arrays.equals(expectedLabels, labels)) {
                    throw new AssertionError("native PIDNet argmax differs from Java reference");
                }
                int[] histogram = new int[classCount];
                for (int pixel = 0; pixel < plane; pixel++) {
                    histogram[labels[pixel]]++;
                }
                Integer[] order = new Integer[classCount];
                for (int i = 0; i < classCount; i++) order[i] = i;
                Arrays.sort(order, (left, right) -> Integer.compare(
                        histogram[right], histogram[left]));
                StringBuilder top = new StringBuilder();
                for (int i = 0; i < 6 && histogram[order[i]] > 0; i++) {
                    if (top.length() > 0) top.append(',');
                    top.append(order[i]).append(':').append(String.format(Locale.US, "%.1f%%",
                            histogram[order[i]] * 100.0 / plane));
                }
                Log.i(TAG, String.format(Locale.US,
                        "backend=%s-%d infer=%.2fms native_decode=%.2fms "
                                + "label_hash=%016x "
                                + "center=%d top_classes=%s",
                        backend, iteration,
                        (inferred - started) / 1_000_000.0,
                        (decoded - nativeStarted) / 1_000_000.0,
                        hash(IntBuffer.wrap(labels)), labels[plane / 2], top));
            }
            if (nnapiDelegate != null) {
                Log.i(TAG, "nnapi_errno=" + nnapiDelegate.getNnapiErrno()
                        + " has_errors=" + nnapiDelegate.hasErrors());
            }
        } finally {
            delegate.close();
        }
    }

    private static void verifyNativePidNetPreprocess() {
        final int sourceWidth = 640;
        final int sourceHeight = 480;
        final int modelWidth = 1024;
        final int modelHeight = 1024;
        byte[] rgb = new byte[sourceWidth * sourceHeight * 3];
        for (int i = 0; i < rgb.length; i++) rgb[i] = (byte) (i * 37 + 11);
        ByteBuffer nativeBytes = ByteBuffer.allocateDirect(
                3 * modelWidth * modelHeight * Float.BYTES).order(ByteOrder.nativeOrder());
        FloatBuffer nativeFloats = nativeBytes.asFloatBuffer();
        FloatBuffer reference = ByteBuffer.allocateDirect(
                3 * modelWidth * modelHeight * Float.BYTES)
                .order(ByteOrder.nativeOrder()).asFloatBuffer();
        float[] mean = {0.485f, 0.456f, 0.406f};
        float[] std = {0.229f, 0.224f, 0.225f};
        int plane = modelWidth * modelHeight;
        long javaStarted = SystemClock.elapsedRealtimeNanos();
        for (int y = 0; y < modelHeight; y++) {
            int sourceY = Math.min(sourceHeight - 1, y * sourceHeight / modelHeight);
            for (int x = 0; x < modelWidth; x++) {
                int sourceX = Math.min(sourceWidth - 1, x * sourceWidth / modelWidth);
                int source = (sourceY * sourceWidth + sourceX) * 3;
                int pixel = y * modelWidth + x;
                for (int channel = 0; channel < 3; channel++) {
                    reference.put(channel * plane + pixel,
                            ((rgb[source + channel] & 0xff) / 255f - mean[channel])
                                    / std[channel]);
                }
            }
        }
        long javaFinished = SystemClock.elapsedRealtimeNanos();
        long[] nativeTimes = new long[7];
        for (int iteration = 0; iteration < nativeTimes.length; iteration++) {
            nativeBytes.position(0);
            long nativeStarted = SystemClock.elapsedRealtimeNanos();
            NativeOctomap.nativePreparePidNet(rgb, sourceWidth * 3, 0, 0,
                    sourceWidth, sourceHeight, modelWidth, modelHeight, nativeBytes);
            nativeTimes[iteration] = SystemClock.elapsedRealtimeNanos() - nativeStarted;
        }
        float maxError = 0f;
        int bitDifferences = 0;
        for (int i = 0; i < plane * 3; i++) {
            float expected = reference.get(i);
            float actual = nativeFloats.get(i);
            maxError = Math.max(maxError, Math.abs(expected - actual));
            if (Float.floatToIntBits(expected) != Float.floatToIntBits(actual)) {
                bitDifferences++;
            }
        }
        if (maxError > 1e-6f) {
            throw new AssertionError("native PIDNet preprocessing max error=" + maxError);
        }
        long[] sortedNativeTimes = nativeTimes.clone();
        Arrays.sort(sortedNativeTimes);
        Log.i(TAG, String.format(Locale.US,
                "pidnet_preprocess java_cold=%.2fms native_cold=%.2fms "
                        + "native_warm_median=%.2fms native_best=%.2fms "
                        + "bit_differences=%d/%d max_error=%.9g",
                (javaFinished - javaStarted) / 1_000_000.0,
                nativeTimes[0] / 1_000_000.0,
                sortedNativeTimes[sortedNativeTimes.length / 2] / 1_000_000.0,
                sortedNativeTimes[0] / 1_000_000.0,
                bitDifferences, plane * 3, maxError));
    }

    private void runSegformerBenchmark(OrtEnvironment environment, File model,
                                       String imagePath, String mode) throws Exception {
        final int width = intArgument("input_width", 640);
        final int height = intArgument("input_height", 480);
        FloatBuffer input = imagePath == null
                ? normalizedDeterministicInput(width, height)
                : normalizedImageInput(imagePath, width, height);
        Log.i(TAG, imagePath == null
                ? "input=normalized deterministic " + width + "x" + height
                : "input=normalized image " + imagePath + " resized=" + width + "x" + height);
        try (OrtSession.SessionOptions options = providerOptionsForSegformer(mode);
             OrtSession session = environment.createSession(model.getAbsolutePath(), options)) {
            benchmarkSegformer(environment, session, input, width, height, mode + "-warmup");
            benchmarkSegformer(environment, session, input, width, height, mode + "-1");
            benchmarkSegformer(environment, session, input, width, height, mode + "-2");
        }
    }

    private int intArgument(String name, int fallback) {
        if (arguments == null) return fallback;
        String value = arguments.getString(name);
        if (value == null || value.isEmpty()) return fallback;
        return Integer.parseInt(value);
    }

    private static OrtSession.SessionOptions providerOptionsForSegformer(String mode)
            throws Exception {
        if ("segformer-webgpu".equals(mode)) {
            return webGpuOptions(Collections.emptyMap());
        }
        return cpuProviderOptions("segformer-nnapi".equals(mode) ? "NNAPI" : "XNNPACK");
    }

    private static void benchmarkSegformer(OrtEnvironment environment, OrtSession session,
                                           FloatBuffer input, int width, int height,
                                           String backend) throws Exception {
        input.position(0);
        long started = SystemClock.elapsedRealtimeNanos();
        int[] labels;
        try (OnnxTensor tensor = OnnxTensor.createTensor(
                environment, input, new long[]{1, 3, height, width});
             OrtSession.Result outputs = session.run(
                     Collections.singletonMap("pixel_values", tensor))) {
            OnnxTensor output = (OnnxTensor) outputs.get(0);
            long[] shape = output.getInfo().getShape();
            if (shape.length != 4 || shape[0] != 1 || shape[1] != 150
                    || shape[2] <= 0 || shape[3] <= 0) {
                throw new IllegalStateException("unexpected SegFormer output "
                        + Arrays.toString(shape));
            }
            int channels = (int) shape[1];
            int pixels = (int) (shape[2] * shape[3]);
            FloatBuffer logits = output.getFloatBuffer();
            if (logits == null || logits.remaining() < channels * pixels) {
                throw new IllegalStateException("missing SegFormer logits");
            }
            labels = new int[pixels];
            float[] best = new float[pixels];
            Arrays.fill(best, Float.NEGATIVE_INFINITY);
            for (int channel = 0; channel < channels; channel++) {
                int offset = channel * pixels;
                for (int pixel = 0; pixel < pixels; pixel++) {
                    float value = logits.get(offset + pixel);
                    if (value > best[pixel]) {
                        best[pixel] = value;
                        labels[pixel] = channel;
                    }
                }
            }
            long millis = (SystemClock.elapsedRealtimeNanos() - started) / 1_000_000L;
            int[] histogram = new int[channels];
            for (int label : labels) histogram[label]++;
            Integer[] order = new Integer[channels];
            for (int i = 0; i < channels; i++) order[i] = i;
            Arrays.sort(order, (left, right) -> Integer.compare(
                    histogram[right], histogram[left]));
            StringBuilder top = new StringBuilder();
            for (int i = 0; i < 8 && histogram[order[i]] > 0; i++) {
                if (top.length() > 0) top.append(',');
                top.append(order[i]).append(':').append(histogram[order[i]]);
            }
            Log.i(TAG, String.format(Locale.US,
                    "backend=%s elapsed=%dms output=%dx%d label_hash=%016x top_classes=%s",
                    backend, millis, shape[3], shape[2], hash(IntBuffer.wrap(labels)), top));
        }
    }

    private static BenchmarkResult runWebGpuVariant(OrtEnvironment environment, File model,
                                                     FloatBuffer input, String name,
                                                     Map<String, String> providerOptions,
                                                     BenchmarkResult reference) throws Exception {
        try (OrtSession.SessionOptions options = webGpuOptions(providerOptions);
             OrtSession session = environment.createSession(model.getAbsolutePath(), options)) {
            BenchmarkResult first = benchmark(environment, session, input, name + "-warmup");
            BenchmarkResult second = benchmark(environment, session, input, name + "-1");
            BenchmarkResult third = benchmark(environment, session, input, name + "-2");
            compare(first, second, name + "-warmup", name + "-1");
            compare(second, third, name + "-1", name + "-2");
            if (reference != null) {
                compare(reference, third, "WEBGPU-default", name);
            }
            return third;
        }
    }

    private void profileWebGpu(OrtEnvironment environment, File model, FloatBuffer input)
            throws Exception {
        File prefix = new File(getTargetContext().getFilesDir(), "semantic-webgpu-profile");
        try (OrtSession.SessionOptions options = webGpuOptions(Collections.emptyMap())) {
            options.enableProfiling(prefix.getAbsolutePath());
            try (OrtSession session = environment.createSession(model.getAbsolutePath(), options)) {
                benchmark(environment, session, input, "WEBGPU-profile-warmup");
                benchmark(environment, session, input, "WEBGPU-profile");
                Log.i(TAG, "profile_path=" + session.endProfiling());
            }
        }
    }

    private void runCpuProvider(OrtEnvironment environment, File model, FloatBuffer input,
                                String provider) throws Exception {
        try (OrtSession.SessionOptions options = cpuProviderOptions(provider);
             OrtSession session = environment.createSession(model.getAbsolutePath(), options)) {
            BenchmarkResult first = benchmark(environment, session, input, provider + "-warmup");
            BenchmarkResult second = benchmark(environment, session, input, provider + "-1");
            BenchmarkResult third = benchmark(environment, session, input, provider + "-2");
            compare(first, second, provider + "-warmup", provider + "-1");
            compare(second, third, provider + "-1", provider + "-2");
        }
    }

    private static OrtSession.SessionOptions cpuProviderOptions(String provider) throws Exception {
        OrtSession.SessionOptions options = new OrtSession.SessionOptions();
        options.setOptimizationLevel(OrtSession.SessionOptions.OptLevel.ALL_OPT);
        options.setIntraOpNumThreads(Runtime.getRuntime().availableProcessors());
        options.setInterOpNumThreads(1);
        options.addConfigEntry("session.intra_op.allow_spinning", "0");
        options.addConfigEntry("session.inter_op.allow_spinning", "0");
        if (provider.startsWith("NNAPI")) {
            options.addNnapi();
        } else {
            options.addXnnpack(Collections.singletonMap(
                    "intra_op_num_threads", Integer.toString(Runtime.getRuntime().availableProcessors())));
        }
        return options;
    }

    private static OrtSession.SessionOptions webGpuOptions(
            Map<String, String> providerOptions) throws Exception {
        OrtSession.SessionOptions options = new OrtSession.SessionOptions();
        options.setOptimizationLevel(OrtSession.SessionOptions.OptLevel.ALL_OPT);
        options.setIntraOpNumThreads(1);
        options.setInterOpNumThreads(1);
        options.addConfigEntry("session.intra_op.allow_spinning", "0");
        options.addConfigEntry("session.inter_op.allow_spinning", "0");
        options.addWebGPU(providerOptions);
        return options;
    }

    private static Map<String, String> options(String... entries) {
        Map<String, String> options = new LinkedHashMap<>();
        for (int i = 0; i < entries.length; i += 2) {
            options.put(entries[i], entries[i + 1]);
        }
        return options;
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
            OnnxTensor maskTensor = (OnnxTensor) outputs.get(0);
            IntBuffer maskBuffer = maskTensor.getIntBuffer();
            FloatBuffer confidenceBuffer = ((OnnxTensor) outputs.get(1)).getFloatBuffer();
            LongBuffer longMaskBuffer = maskBuffer == null ? maskTensor.getLongBuffer() : null;
            int maskLength = maskBuffer != null ? maskBuffer.remaining() : longMaskBuffer.remaining();
            mask = new int[maskLength];
            confidence = new float[confidenceBuffer.remaining()];
            if (maskBuffer != null) {
                maskBuffer.get(mask);
            } else {
                for (int i = 0; i < mask.length; i++) {
                    mask[i] = (int) longMaskBuffer.get();
                }
            }
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

    private static FloatBuffer normalizedDeterministicInput(int width, int height) {
        FloatBuffer input = ByteBuffer.allocateDirect(3 * width * height * Float.BYTES)
                .order(ByteOrder.nativeOrder()).asFloatBuffer();
        int plane = width * height;
        float[] mean = {0.485f, 0.456f, 0.406f};
        float[] std = {0.229f, 0.224f, 0.225f};
        for (int y = 0; y < height; y++) {
            for (int x = 0; x < width; x++) {
                int pixel = y * width + x;
                float r = ((x * 17 + y * 3) & 0xff) / 255f;
                float g = ((x * 5 + y * 11) & 0xff) / 255f;
                float b = ((x * 7 + y * 13) & 0xff) / 255f;
                input.put(pixel, (r - mean[0]) / std[0]);
                input.put(plane + pixel, (g - mean[1]) / std[1]);
                input.put(2 * plane + pixel, (b - mean[2]) / std[2]);
            }
        }
        input.position(0);
        return input;
    }

    private FloatBuffer normalizedImageInput(String path, int width, int height) {
        Bitmap decoded = BitmapFactory.decodeFile(path);
        if (decoded == null) {
            throw new IllegalArgumentException("cannot decode image: " + path);
        }
        int[] crop = SemanticSegmenter.modelAspectCrop(
                decoded.getWidth(), decoded.getHeight(), width, height);
        Bitmap cropped = Bitmap.createBitmap(decoded, crop[0], crop[1], crop[2], crop[3]);
        Bitmap bitmap = Bitmap.createScaledBitmap(cropped, width, height, true);
        int[] pixels = new int[width * height];
        bitmap.getPixels(pixels, 0, width, 0, 0, width, height);
        FloatBuffer input = ByteBuffer.allocateDirect(3 * width * height * Float.BYTES)
                .order(ByteOrder.nativeOrder()).asFloatBuffer();
        int plane = width * height;
        float[] mean = {0.485f, 0.456f, 0.406f};
        float[] std = {0.229f, 0.224f, 0.225f};
        for (int pixel = 0; pixel < pixels.length; pixel++) {
            int argb = pixels[pixel];
            float r = ((argb >> 16) & 0xff) / 255f;
            float g = ((argb >> 8) & 0xff) / 255f;
            float b = (argb & 0xff) / 255f;
            input.put(pixel, (r - mean[0]) / std[0]);
            input.put(plane + pixel, (g - mean[1]) / std[1]);
            input.put(2 * plane + pixel, (b - mean[2]) / std[2]);
        }
        input.position(0);
        if (bitmap != cropped) bitmap.recycle();
        if (cropped != decoded) cropped.recycle();
        decoded.recycle();
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
