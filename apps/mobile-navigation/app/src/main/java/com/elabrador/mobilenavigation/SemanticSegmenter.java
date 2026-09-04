package com.elabrador.mobilenavigation;

import android.content.Context;
import android.os.SystemClock;
import android.util.Log;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.FloatBuffer;
import java.nio.IntBuffer;
import java.nio.LongBuffer;
import java.util.Collections;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.TimeUnit;

import com.intel.realsense.librealsense.Intrinsic;

import ai.onnxruntime.OnnxTensor;
import ai.onnxruntime.OrtEnvironment;
import ai.onnxruntime.OrtProvider;
import ai.onnxruntime.OrtSession;
import org.tensorflow.lite.Interpreter;
import org.tensorflow.lite.gpu.GpuDelegate;

final class SemanticSegmenter implements AutoCloseable {
    private static final String TAG = "SemanticPipeline";
    private static final long MAP_LIFE_NANOS = TimeUnit.SECONDS.toNanos(60);
    private static final long POSE_LIFE_NANOS = TimeUnit.SECONDS.toNanos(10);
    interface Listener {
        void onStatus(String status);

        void onResult(Result result);

        void onError(String message);
    }

    static final class Result {
        final boolean isNotWalkable;
        final String label;
        final float areaRatio;
        final long inferenceMillis;
        final String backend;
        final float leftCost;
        final float centerCost;
        final float rightCost;
        final float obstacleDistance;
        final int semanticPointCount;
        final int octomapLeafCount;
        final int costGridKnownCount;
        final float groundHeight;
        final int groundClearedCells;
        final int[] localCostGrid;

        Result(boolean isNotWalkable, String label, float areaRatio, long inferenceMillis,
               String backend, float leftCost, float centerCost, float rightCost,
               float obstacleDistance, int semanticPointCount, int octomapLeafCount, int costGridKnownCount,
               float groundHeight, int groundClearedCells, int[] localCostGrid) {
            this.isNotWalkable = isNotWalkable;
            this.label = label;
            this.areaRatio = areaRatio;
            this.inferenceMillis = inferenceMillis;
            this.backend = backend;
            this.leftCost = leftCost;
            this.centerCost = centerCost;
            this.rightCost = rightCost;
            this.obstacleDistance = obstacleDistance;
            this.semanticPointCount = semanticPointCount;
            this.octomapLeafCount = octomapLeafCount;
            this.costGridKnownCount = costGridKnownCount;
            this.groundHeight = groundHeight;
            this.groundClearedCells = groundClearedCells;
            this.localCostGrid = localCostGrid;
        }

        static Result waiting() {
            return new Result(false, "等待 Mask2Former", 0f, 0L, "未加载",
                    Float.NaN, Float.NaN, Float.NaN, Float.NaN, 0, 0, 0,
                    Float.NaN, 0, null);
        }
    }

    private static final String MODEL_ASSET = BuildConfig.SEMANTIC_MODEL_ASSET;
    private static final long MODEL_BYTES = BuildConfig.SEMANTIC_MODEL_BYTES;
    private static final int MODEL_WIDTH = BuildConfig.SEMANTIC_MODEL_WIDTH;
    private static final int MODEL_HEIGHT = BuildConfig.SEMANTIC_MODEL_HEIGHT;
    private static final int OUTPUT_WIDTH = BuildConfig.SEMANTIC_OUTPUT_WIDTH;
    private static final int OUTPUT_HEIGHT = BuildConfig.SEMANTIC_OUTPUT_HEIGHT;
    private static final boolean USE_NEURON = "PIDNET_NEURON".equals(
            BuildConfig.SEMANTIC_MODEL_KIND);
    private static final boolean USE_PIDNET = USE_NEURON || "PIDNET_LITERT".equals(
            BuildConfig.SEMANTIC_MODEL_KIND);
    private static final int PIDNET_CLASS_COUNT = 19;
    private static final int[] PIDNET_TO_MAPILLARY = {
            13, 15, 17, 6, 3, 45, 48, 50, 30, 29,
            27, 19, 20, 55, 61, 54, 58, 57, 52
    };
    // Keep half of the Dimensity 9300+ cores available for VINS, USB and UI work.
    private static final int INFERENCE_THREADS = Math.max(1,
            Math.min(4, Runtime.getRuntime().availableProcessors()));
    private static final String[] LABELS = {
            "鸟", "动物", "路缘", "围栏", "护栏", "隔离设施", "墙体", "自行车道",
            "人行横道", "无障碍坡道", "停车区", "步行区", "轨道", "道路", "辅路", "人行道",
            "桥梁", "建筑物", "隧道", "行人", "骑自行车者", "骑摩托车者", "其他骑行者",
            "斑马线标线", "普通道路标线", "山体", "沙地", "天空", "积雪", "自然地形",
            "植被/草地", "水面", "横幅", "长椅", "自行车架", "广告牌", "排水口", "监控摄像头",
            "消防栓", "设备箱", "邮箱", "井盖", "电话亭", "坑洼", "路灯", "杆体", "标志框",
            "电线杆", "交通灯", "交通标志背面", "交通标志正面", "垃圾桶", "自行车", "船",
            "公交车", "汽车", "房车", "摩托车", "轨道车辆", "其他车辆", "拖车", "卡车",
            "轮式慢行工具", "相机支架", "采集车辆"
    };

    private final Context context;
    private final Listener listener;
    private final ExecutorService executor = Executors.newSingleThreadExecutor(runnable ->
            new Thread(runnable, "semantic-inference"));
    private final ExecutorService mapExecutor = Executors.newSingleThreadExecutor(runnable ->
            new Thread(runnable, "semantic-map"));
    private final AtomicBoolean inferencePending = new AtomicBoolean(false);
    private final AtomicBoolean mapPending = new AtomicBoolean(false);
    private final AtomicLong droppedMapFrames = new AtomicLong();
    private final AtomicLong vinsGeneration = new AtomicLong();
    private final VinsPoseHistory vinsPoseHistory = new VinsPoseHistory();
    private final FloatBuffer inputBuffer = USE_PIDNET ? null : ByteBuffer
            .allocateDirect(3 * MODEL_WIDTH * MODEL_HEIGHT * Float.BYTES)
            .order(ByteOrder.nativeOrder())
            .asFloatBuffer();
    private final ByteBuffer liteRtInputBytes = USE_PIDNET ? ByteBuffer
            .allocateDirect(3 * MODEL_WIDTH * MODEL_HEIGHT * Float.BYTES)
            .order(ByteOrder.nativeOrder()) : null;
    private final ByteBuffer liteRtOutputBytes = USE_PIDNET ? ByteBuffer
            .allocateDirect(PIDNET_CLASS_COUNT * OUTPUT_WIDTH * OUTPUT_HEIGHT * Float.BYTES)
            .order(ByteOrder.nativeOrder()) : null;
    private final Object pidNetBufferLock = new Object();
    private int[] maskBuffer = new int[OUTPUT_WIDTH * OUTPUT_HEIGHT];
    private float[] confidenceBuffer = new float[OUTPUT_WIDTH * OUTPUT_HEIGHT];
    private int[] recycledMaskBuffer = USE_PIDNET
            ? new int[OUTPUT_WIDTH * OUTPUT_HEIGHT] : null;
    private float[] recycledConfidenceBuffer = USE_PIDNET
            ? new float[OUTPUT_WIDTH * OUTPUT_HEIGHT] : null;
    private volatile OrtSession session;
    private volatile Interpreter liteRtInterpreter;
    private volatile GpuDelegate liteRtGpuDelegate;
    private volatile long neuronHandle;
    private volatile String backend = "CPU";
    private volatile boolean closed;
    private volatile long octomapHandle;
    private volatile VinsMono.Pose vinsPose;
    private volatile float[] latestOctomapLeaves;
    private volatile Result latestResult;
    private volatile long vinsPoseStampNanos;
    private volatile long localMapStampNanos;
    private volatile long lastPoseLogNanos;

    void updateVinsPose(VinsMono.Pose pose) {
        if (pose != null && pose.initialized) {
            vinsPose = pose;
            vinsPoseStampNanos = System.nanoTime();
            vinsPoseHistory.add(pose);
            long now = SystemClock.elapsedRealtimeNanos();
            if (now - lastPoseLogNanos >= TimeUnit.SECONDS.toNanos(2)) {
                lastPoseLogNanos = now;
                Log.i(TAG, String.format(Locale.US,
                        "VINS pose t=%.3f xyz=[%.3f %.3f %.3f] ego_yaw=%.2fdeg",
                        pose.timestamp, pose.x, pose.y, pose.z,
                        Math.toDegrees(pose.egoRightAxisYawRadians())));
            }
        }
    }

    /** A restarted VINS instance defines a new world frame; old poses/maps are invalid. */
    void resetVinsState() {
        vinsGeneration.incrementAndGet();
        vinsPoseHistory.clear();
        vinsPose = null;
        vinsPoseStampNanos = 0L;
        localMapStampNanos = 0L;
        latestOctomapLeaves = null;
        latestResult = null;
        if (!closed) {
            try {
                mapExecutor.execute(() -> {
                    if (octomapHandle != 0L) NativeOctomap.nativeClear(octomapHandle);
                    latestOctomapLeaves = null;
                    latestResult = null;
                });
            } catch (RejectedExecutionException ignored) {
                // close() already owns resource cleanup.
            }
        }
    }

    SemanticSegmenter(Context context, Listener listener) {
        this.context = context.getApplicationContext();
        this.listener = listener;
    }

    void initialize() {
        executor.execute(() -> {
            try {
                listener.onStatus("正在准备" + BuildConfig.SEMANTIC_MODEL_DESCRIPTION);
                octomapHandle = NativeOctomap.nativeCreate(0.2f);
                if (octomapHandle == 0L) {
                    throw new IllegalStateException("无法创建原生 OctoMap");
                }
                File model = prepareModelFile();
                if (USE_NEURON) {
                    listener.onStatus("正在加载 " + BuildConfig.SEMANTIC_MODEL_NAME
                            + "（MediaTek Neuron NPU）");
                    neuronHandle = NativeNeuronShim.nativeCreate(model.getAbsolutePath(), true);
                    if (neuronHandle == 0L) {
                        throw new IllegalStateException("MediaTek Neuron NPU 模型创建失败");
                    }
                    backend = "MediaTek-Neuron-FP16";
                } else if (USE_PIDNET) {
                    listener.onStatus("正在加载 " + BuildConfig.SEMANTIC_MODEL_NAME
                            + "（LiteRT GPU）");
                    GpuDelegate.Options delegateOptions = new GpuDelegate.Options();
                    delegateOptions.setPrecisionLossAllowed(true);
                    GpuDelegate gpuDelegate = new GpuDelegate(delegateOptions);
                    Interpreter.Options options = new Interpreter.Options();
                    options.addDelegate(gpuDelegate);
                    liteRtGpuDelegate = gpuDelegate;
                    liteRtInterpreter = new Interpreter(model, options);
                    backend = "LiteRT-GPU-FP16";
                } else {
                    initializeOnnx(model);
                }
                Log.i(TAG, "model ready backend=" + backend
                        + " input=" + MODEL_WIDTH + "x" + MODEL_HEIGHT
                        + " output=" + OUTPUT_WIDTH + "x" + OUTPUT_HEIGHT
                        + " bytes=" + model.length());
                listener.onStatus(BuildConfig.SEMANTIC_MODEL_NAME + " 已加载（"
                        + backend + "），等待彩色帧");
            } catch (Exception error) {
                listener.onError(errorMessage(error));
            }
        });
    }

    void submitRgb(byte[] rgb, int width, int height, int stride,
                   byte[] depth, int depthWidth, int depthHeight, int depthStride,
                   float depthUnits, Intrinsic intrinsic, double frameTimestampSeconds) {
        if (!isModelReady() || closed
                || !inferencePending.compareAndSet(false, true)) {
            return;
        }
        long generation = vinsGeneration.get();
        try {
            executor.execute(() -> {
                try {
                    Result result = runInference(rgb, width, height, stride, depth,
                            depthWidth, depthHeight, depthStride, depthUnits, intrinsic,
                            frameTimestampSeconds, generation);
                    if (result != null && generation == vinsGeneration.get() && !closed) {
                        latestResult = result;
                        listener.onResult(result);
                    }
                } catch (Exception error) {
                    if (generation == vinsGeneration.get() && !closed) {
                        listener.onError(errorMessage(error));
                    }
                } finally {
                    inferencePending.set(false);
                }
            });
        } catch (RejectedExecutionException ignored) {
            inferencePending.set(false);
        }
    }

    boolean canAcceptFrame() {
        return !closed
                && isModelReady()
                && !inferencePending.get();
    }

    private boolean isModelReady() {
        if (USE_NEURON) return neuronHandle != 0L;
        return USE_PIDNET ? liteRtInterpreter != null : session != null;
    }

    private Result runInference(byte[] rgb, int width, int height, int stride,
                                byte[] depth, int depthWidth, int depthHeight, int depthStride,
                                float depthUnits, Intrinsic intrinsic,
                                double frameTimestampSeconds, long generation) throws Exception {
        long started = SystemClock.elapsedRealtimeNanos();
        Log.i(TAG, String.format(Locale.US,
                "inference start input=%dx%d source=%dx%d frame=%.3f",
                MODEL_WIDTH, MODEL_HEIGHT, width, height, frameTimestampSeconds));
        int[] crop = USE_PIDNET
                ? new int[]{0, 0, width, height}
                : modelAspectCrop(width, height, MODEL_WIDTH, MODEL_HEIGHT);
        int xOffset = crop[0];
        int yOffset = crop[1];
        int cropWidth = crop[2];
        int cropHeight = crop[3];
        if (USE_PIDNET) {
            preparePidNetInput(rgb, stride, xOffset, yOffset, cropWidth, cropHeight);
        } else {
            prepareMask2FormerInput(rgb, stride, xOffset, yOffset, cropWidth, cropHeight);
        }
        long prepared = SystemClock.elapsedRealtimeNanos();

        if (USE_PIDNET) {
            return runPidNetInference(depth, depthWidth, depthHeight, depthStride,
                    depthUnits, intrinsic, frameTimestampSeconds, generation, crop,
                    started, prepared);
        }

        OrtEnvironment environment = OrtEnvironment.getEnvironment();
        try (OnnxTensor tensor = OnnxTensor.createTensor(
                environment, inputBuffer, new long[]{1, 3, MODEL_HEIGHT, MODEL_WIDTH});
             OrtSession.Result outputs = session.run(
                      Collections.singletonMap("image", tensor))) {
            long inferred = SystemClock.elapsedRealtimeNanos();
            Log.i(TAG, String.format(Locale.US,
                    "inference returned backend=%s elapsed=%.1fms",
                    backend, (inferred - started) / 1_000_000.0));
            int plane = OUTPUT_WIDTH * OUTPUT_HEIGHT;
            OnnxTensor maskTensor = (OnnxTensor) outputs.get(0);
            IntBuffer maskOutput = maskTensor.getIntBuffer();
            LongBuffer longMaskOutput = maskOutput == null ? maskTensor.getLongBuffer() : null;
            FloatBuffer confidenceOutput = ((OnnxTensor) outputs.get(1)).getFloatBuffer();
            if ((maskOutput == null && longMaskOutput == null)
                    || (maskOutput != null && maskOutput.remaining() < plane)
                    || (longMaskOutput != null && longMaskOutput.remaining() < plane)
                    || confidenceOutput == null || confidenceOutput.remaining() < plane) {
                throw new IllegalStateException("Mask2Former 返回了无效的后处理张量");
            }
            int[] mask = maskBuffer;
            float[] confidence = confidenceBuffer;
            if (maskOutput != null) {
                maskOutput.get(mask);
            } else {
                for (int i = 0; i < plane; i++) {
                    mask[i] = (int) longMaskOutput.get();
                }
            }
            confidenceOutput.get(confidence);
            long decoded = SystemClock.elapsedRealtimeNanos();
            return finishInference(mask, confidence, OUTPUT_WIDTH, OUTPUT_HEIGHT,
                    depth, depthWidth, depthHeight, depthStride, depthUnits, intrinsic,
                    frameTimestampSeconds, generation, crop, started, prepared, inferred,
                    decoded);
        }
    }

    private void initializeOnnx(File model) throws Exception {
        OrtEnvironment environment = OrtEnvironment.getEnvironment();
        listener.onStatus("正在加载 " + BuildConfig.SEMANTIC_MODEL_NAME + "（WebGPU）");
        try {
            if (!OrtEnvironment.getAvailableProviders().contains(OrtProvider.WEBGPU)) {
                throw new IllegalStateException("WebGPU provider unavailable");
            }
            try (OrtSession.SessionOptions options = webGpuOptions()) {
                session = environment.createSession(model.getAbsolutePath(), options);
                backend = "WebGPU";
            }
        } catch (Exception webGpuError) {
            Log.w(TAG, "WebGPU session unavailable; falling back to XNNPACK", webGpuError);
            listener.onStatus("WebGPU 不兼容，正在回退 XNNPACK");
            try (OrtSession.SessionOptions options = xnnpackOptions()) {
                session = environment.createSession(model.getAbsolutePath(), options);
                backend = "XNNPACK-" + INFERENCE_THREADS + "T";
            } catch (Exception xnnpackError) {
                Log.w(TAG, "XNNPACK session unavailable; falling back to CPU", xnnpackError);
                listener.onStatus("XNNPACK 不兼容此模型，正在回退 CPU");
                try (OrtSession.SessionOptions options = cpuOptions()) {
                    session = environment.createSession(model.getAbsolutePath(), options);
                    backend = "CPU";
                }
            }
        }
    }

    private void prepareMask2FormerInput(byte[] rgb, int stride, int xOffset, int yOffset,
                                         int cropWidth, int cropHeight) {
        FloatBuffer input = inputBuffer;
        input.clear();
        int plane = MODEL_WIDTH * MODEL_HEIGHT;
        for (int y = 0; y < MODEL_HEIGHT; y++) {
            int sourceY = yOffset + Math.min(cropHeight - 1,
                    y * cropHeight / MODEL_HEIGHT);
            for (int x = 0; x < MODEL_WIDTH; x++) {
                int sourceX = xOffset + Math.min(cropWidth - 1,
                        x * cropWidth / MODEL_WIDTH);
                int source = sourceY * stride + sourceX * 3;
                int pixel = y * MODEL_WIDTH + x;
                input.put(pixel, rgb[source] & 0xff);
                input.put(plane + pixel, rgb[source + 1] & 0xff);
                input.put(plane * 2 + pixel, rgb[source + 2] & 0xff);
            }
        }
        input.position(0);
    }

    private void preparePidNetInput(byte[] rgb, int stride, int xOffset, int yOffset,
                                    int cropWidth, int cropHeight) {
        liteRtInputBytes.position(0);
        NativeOctomap.nativePreparePidNet(rgb, stride, xOffset, yOffset,
                cropWidth, cropHeight, MODEL_WIDTH, MODEL_HEIGHT, liteRtInputBytes);
        liteRtInputBytes.position(0);
    }

    private Result runPidNetInference(byte[] depth, int depthWidth, int depthHeight,
                                      int depthStride, float depthUnits, Intrinsic intrinsic,
                                      double frameTimestampSeconds, long generation, int[] crop,
                                      long started, long prepared) throws Exception {
        liteRtInputBytes.position(0);
        liteRtOutputBytes.position(0);
        if (USE_NEURON) {
            long handle = neuronHandle;
            if (handle == 0L) throw new IllegalStateException("PIDNet Neuron 尚未初始化");
            NativeNeuronShim.nativeRun(handle, liteRtInputBytes, liteRtOutputBytes);
        } else {
            Interpreter interpreter = liteRtInterpreter;
            if (interpreter == null) throw new IllegalStateException("PIDNet LiteRT 尚未初始化");
            interpreter.run(liteRtInputBytes, liteRtOutputBytes);
        }
        long inferred = SystemClock.elapsedRealtimeNanos();
        int plane = OUTPUT_WIDTH * OUTPUT_HEIGHT;
        liteRtOutputBytes.position(0);
        NativeOctomap.nativeDecodePidNet(liteRtOutputBytes, PIDNET_CLASS_COUNT, plane,
                PIDNET_TO_MAPILLARY, maskBuffer, confidenceBuffer);
        long decoded = SystemClock.elapsedRealtimeNanos();
        Log.i(TAG, String.format(Locale.US,
                "inference returned backend=%s elapsed=%.1fms",
                backend, (inferred - started) / 1_000_000.0));
        enqueuePidNetMap(depth, depthWidth, depthHeight, depthStride, depthUnits, intrinsic,
                frameTimestampSeconds, generation, crop, started, prepared, inferred, decoded);
        return null;
    }

    /**
     * GPU inference and CPU map fusion use separate bounded stages. A second pair of output
     * buffers makes the handed-off mask immutable until mapping completes. If mapping is still
     * busy, the newer segmentation is discarded instead of accumulating stale map updates.
     */
    private void enqueuePidNetMap(byte[] depth, int depthWidth, int depthHeight,
                                  int depthStride, float depthUnits, Intrinsic intrinsic,
                                  double frameTimestampSeconds, long generation, int[] crop,
                                  long started, long prepared, long inferred, long decoded) {
        if (!mapPending.compareAndSet(false, true)) {
            long dropped = droppedMapFrames.incrementAndGet();
            if (dropped == 1L || dropped % 30L == 0L) {
                Log.i(TAG, "semantic map busy; dropped completed inference count=" + dropped);
            }
            return;
        }

        final int[] mapMask;
        final float[] mapConfidence;
        synchronized (pidNetBufferLock) {
            if (recycledMaskBuffer == null || recycledConfidenceBuffer == null) {
                mapPending.set(false);
                throw new IllegalStateException("PIDNet 双缓冲状态无效");
            }
            mapMask = maskBuffer;
            mapConfidence = confidenceBuffer;
            maskBuffer = recycledMaskBuffer;
            confidenceBuffer = recycledConfidenceBuffer;
            recycledMaskBuffer = null;
            recycledConfidenceBuffer = null;
        }

        try {
            mapExecutor.execute(() -> {
                try {
                    Result result = finishInference(
                            mapMask, mapConfidence, OUTPUT_WIDTH, OUTPUT_HEIGHT,
                            depth, depthWidth, depthHeight, depthStride, depthUnits, intrinsic,
                            frameTimestampSeconds, generation, crop, started, prepared, inferred,
                            decoded);
                    if (result != null && generation == vinsGeneration.get() && !closed) {
                        latestResult = result;
                        listener.onResult(result);
                    }
                } catch (Exception error) {
                    if (generation == vinsGeneration.get() && !closed) {
                        listener.onError(errorMessage(error));
                    }
                } finally {
                    synchronized (pidNetBufferLock) {
                        recycledMaskBuffer = mapMask;
                        recycledConfidenceBuffer = mapConfidence;
                    }
                    mapPending.set(false);
                }
            });
        } catch (RejectedExecutionException ignored) {
            synchronized (pidNetBufferLock) {
                recycledMaskBuffer = mapMask;
                recycledConfidenceBuffer = mapConfidence;
            }
            mapPending.set(false);
        }
    }

    private Result finishInference(int[] mask, float[] confidence, int maskWidth,
                                   int maskHeight, byte[] depth, int depthWidth,
                                   int depthHeight, int depthStride, float depthUnits,
                                   Intrinsic intrinsic, double frameTimestampSeconds,
                                   long generation, int[] crop, long started, long prepared,
                                   long inferred, long decoded) throws Exception {
        SemanticPointCloud.Data cloud = SemanticPointCloud.generate(
                mask, confidence, maskWidth, maskHeight, depth,
                depthWidth, depthHeight, depthStride, depthUnits, intrinsic,
                crop[0], crop[1], crop[2], crop[3], MapillaryMetadata.colors(context));
        long cloudBuilt = SystemClock.elapsedRealtimeNanos();
        if (generation != vinsGeneration.get() || closed) return null;
        VinsMono.Pose framePose = vinsPoseHistory.at(frameTimestampSeconds);
        VinsMono.Pose currentPose = vinsPose;
        float[] frameCameraToWorld = framePose == null ? null : framePose.cameraToWorld();
        int[] classColors = MapillaryMetadata.colors(context);
        FrameGroundSemanticFilter.Result frameGround = frameCameraToWorld == null
                ? new FrameGroundSemanticFilter.Result(Float.NaN, 0, 0)
                : FrameGroundSemanticFilter.apply(cloud, frameCameraToWorld,
                SemanticPointCloud.sourceTreeColor(
                        classColors[MapillaryMetadata.SIDEWALK_CLASS_INDEX]));
        int inserted = octomapHandle == 0L || framePose == null ? 0 : NativeOctomap.nativeInsert(
                octomapHandle, cloud.xyz, cloud.semanticRgb, cloud.confidence,
                frameCameraToWorld);
        long mapInserted = SystemClock.elapsedRealtimeNanos();
        if (generation != vinsGeneration.get() || closed) return null;
        int leafCount = octomapHandle == 0L ? 0 : NativeOctomap.nativeLeafCount(octomapHandle);
        int gridKnown = 0;
        float groundHeight = Float.NaN;
        int groundClearedCells = 0;
        int groundSupportCells = 0;
        int groundPositiveCostCells = 0;
        int groundCandidateCells = 0;
        int[] localCostGrid = null;
        if (octomapHandle != 0L && leafCount > 0) {
            float[] leaves = NativeOctomap.nativeExportLeafs(octomapHandle);
            latestOctomapLeaves = leaves;
            VinsMono.Pose projectionPose = framePose != null ? framePose : currentPose;
            float locationX = projectionPose == null ? 0f : (float) projectionPose.x;
            float locationY = projectionPose == null ? 0f : (float) projectionPose.y;
            float locationZ = projectionPose == null ? 0f : (float) projectionPose.z;
            float yaw = projectionPose == null ? 0f : projectionPose.egoRightAxisYawRadians();
            MapTransform.Grid grid = MapTransform.octree2localprmapHeightEgo(
                    context, leaves, locationX, locationY, locationZ, yaw, USE_PIDNET);
            gridKnown = grid.known;
            groundHeight = grid.groundHeight;
            groundClearedCells = grid.groundClearedCells;
            groundSupportCells = grid.groundSupportCells;
            groundPositiveCostCells = grid.groundPositiveCostCells;
            groundCandidateCells = grid.groundCandidateCells;
            localCostGrid = grid.cost;
            localMapStampNanos = System.nanoTime();
        }
        long gridBuilt = SystemClock.elapsedRealtimeNanos();
        double poseDelta = framePose == null || currentPose == null ? Double.NaN
                : Math.sqrt(Math.pow(currentPose.x - framePose.x, 2)
                + Math.pow(currentPose.y - framePose.y, 2)
                + Math.pow(currentPose.z - framePose.z, 2));
        double yawDelta = framePose == null || currentPose == null ? Double.NaN
                : Math.toDegrees(normalizeRadians(currentPose.egoRightAxisYawRadians()
                - framePose.egoRightAxisYawRadians()));
        Log.i(TAG, String.format(Locale.US,
                "stages total=%dms prep=%d model=%d decode=%d cloud=%d octomap=%d grid=%d "
                        + "frame_pose_delta=%.3fm yaw_delta=%.2fdeg points=%d leaves=%d known=%d "
                        + "frame_ground=%.3fm/%d/%d ground=%.3fm support=%d positive=%d candidates=%d cleared=%d",
                millis(gridBuilt - started), millis(prepared - started),
                millis(inferred - prepared), millis(decoded - inferred),
                millis(cloudBuilt - decoded), millis(mapInserted - cloudBuilt),
                millis(gridBuilt - mapInserted), poseDelta, yawDelta,
                inserted, leafCount, gridKnown, frameGround.groundHeight,
                frameGround.supportCells, frameGround.correctedPoints, groundHeight,
                groundSupportCells, groundPositiveCostCells, groundCandidateCells,
                groundClearedCells));
        return summarize(mask, maskWidth, maskHeight, inserted, leafCount, gridKnown,
                localCostGrid, groundHeight, groundClearedCells, millis(gridBuilt - started));
    }

    private static long millis(long nanos) {
        return TimeUnit.NANOSECONDS.toMillis(nanos);
    }

    private static double normalizeRadians(double radians) {
        while (radians > Math.PI) radians -= Math.PI * 2.0;
        while (radians < -Math.PI) radians += Math.PI * 2.0;
        return radians;
    }

    /** Reprojects the latest immutable OctoMap snapshot around the current VINS pose. */
    Result reprojectLatestLocalMap() throws Exception {
        Result base = latestResult;
        float[] leaves = latestOctomapLeaves;
        VinsMono.Pose pose = vinsPose;
        long now = System.nanoTime();
        if (base == null || leaves == null || leaves.length == 0 || pose == null
                || now - localMapStampNanos > MAP_LIFE_NANOS
                || now - vinsPoseStampNanos > POSE_LIFE_NANOS) {
            return null;
        }
        MapTransform.Grid grid = MapTransform.octree2localprmapHeightEgo(
                context, leaves, (float) pose.x, (float) pose.y, (float) pose.z,
                pose.egoRightAxisYawRadians(), USE_PIDNET);
        return new Result(base.isNotWalkable, base.label, base.areaRatio,
                base.inferenceMillis, base.backend, base.leftCost, base.centerCost,
                base.rightCost, base.obstacleDistance, base.semanticPointCount,
                base.octomapLeafCount, grid.known, grid.groundHeight,
                grid.groundClearedCells, grid.cost);
    }

    String localMapWaitingReason() {
        long now = System.nanoTime();
        if (vinsPose == null) return "等待 VINS 位姿";
        if (vinsPoseStampNanos == 0L || now - vinsPoseStampNanos > POSE_LIFE_NANOS) {
            return "VINS 位姿已超过 10 秒未更新";
        }
        if (latestOctomapLeaves == null || latestOctomapLeaves.length == 0) {
            return "等待语义点云/OctoMap局部代价图";
        }
        if (localMapStampNanos == 0L || now - localMapStampNanos > MAP_LIFE_NANOS) {
            return "语义局部地图已超过 60 秒未更新";
        }
        return "等待局部地图刷新";
    }

    private Result summarize(int[] mask, int maskWidth, int maskHeight, int pointCount,
                             int leafCount, int gridKnown, int[] localCostGrid,
                             float groundHeight, int groundClearedCells,
                             long inferenceMillis) {
        // Navigation costs come from the native semantic cloud, OctoMap and
        // the source map_transform implementation passed in localCostGrid.
        int centerPixel = (maskHeight / 2) * maskWidth + maskWidth / 2;
        int classId = mask[centerPixel];
        return new Result(false, LABELS[classId], 0f, inferenceMillis, backend,
                Float.NaN, Float.NaN, Float.NaN, Float.NaN, pointCount, leafCount, gridKnown,
                groundHeight, groundClearedCells, localCostGrid);
    }

    private OrtSession.SessionOptions xnnpackOptions() throws Exception {
        OrtSession.SessionOptions options = new OrtSession.SessionOptions();
        options.setOptimizationLevel(OrtSession.SessionOptions.OptLevel.ALL_OPT);
        options.setIntraOpNumThreads(INFERENCE_THREADS);
        options.setInterOpNumThreads(1);
        options.addConfigEntry("session.intra_op.allow_spinning", "0");
        options.addConfigEntry("session.inter_op.allow_spinning", "0");
        options.addXnnpack(Collections.singletonMap(
                "intra_op_num_threads", Integer.toString(INFERENCE_THREADS)));
        return options;
    }

    private OrtSession.SessionOptions webGpuOptions() throws Exception {
        OrtSession.SessionOptions options = new OrtSession.SessionOptions();
        options.setOptimizationLevel(OrtSession.SessionOptions.OptLevel.ALL_OPT);
        options.setIntraOpNumThreads(1);
        options.setInterOpNumThreads(1);
        options.addConfigEntry("session.intra_op.allow_spinning", "0");
        options.addConfigEntry("session.inter_op.allow_spinning", "0");
        options.addWebGPU(Collections.emptyMap());
        return options;
    }

    private OrtSession.SessionOptions cpuOptions() throws Exception {
        OrtSession.SessionOptions options = new OrtSession.SessionOptions();
        options.setOptimizationLevel(OrtSession.SessionOptions.OptLevel.ALL_OPT);
        options.setIntraOpNumThreads(INFERENCE_THREADS);
        options.setInterOpNumThreads(1);
        options.addConfigEntry("session.intra_op.allow_spinning", "0");
        options.addConfigEntry("session.inter_op.allow_spinning", "0");
        return options;
    }

    private File prepareModelFile() throws Exception {
        File directory = new File(context.getFilesDir(), "models");
        if (!directory.exists() && !directory.mkdirs()) {
            throw new IllegalStateException("无法创建模型目录");
        }
        String assetName = new File(MODEL_ASSET).getName();
        File model = new File(directory, assetName);
        if (model.isFile() && model.length() == MODEL_BYTES) {
            deleteObsoleteModel(directory, assetName);
            return model;
        }

        File temporary = new File(directory, model.getName() + ".tmp");
        try (InputStream input = context.getAssets().open(MODEL_ASSET);
             FileOutputStream output = new FileOutputStream(temporary)) {
            byte[] buffer = new byte[1024 * 1024];
            int count;
            while ((count = input.read(buffer)) >= 0) {
                output.write(buffer, 0, count);
            }
        }
        if (temporary.length() != MODEL_BYTES) {
            throw new IllegalStateException("模型复制不完整：" + temporary.length());
        }
        if (model.exists() && !model.delete()) {
            throw new IllegalStateException("无法替换旧模型");
        }
        if (!temporary.renameTo(model)) {
            throw new IllegalStateException("无法启用已复制模型");
        }
        deleteObsoleteModel(directory, assetName);
        return model;
    }

    private void deleteObsoleteModel(File directory, String currentName) {
        String[] obsoleteNames = {
                "mask2former-R50-mapillary-semantic-320.onnx",
                "mask2former-swinL-semantic.onnx",
                "mask2former-swinL-mapillary-semantic-480.onnx",
                "mask2former-swinL-mapillary-semantic-640x480.onnx",
                "pidnet-s-cityscapes-1024.tflite"
        };
        for (String name : obsoleteNames) {
            if (name.equals(currentName)) continue;
            File obsolete = new File(directory, name);
            if (obsolete.isFile()) {
                // Best-effort cleanup after the source model is known to be valid.
                obsolete.delete();
            }
        }
    }

    static int[] modelAspectCrop(int sourceWidth, int sourceHeight,
                                 int modelWidth, int modelHeight) {
        if (sourceWidth <= 0 || sourceHeight <= 0 || modelWidth <= 0 || modelHeight <= 0) {
            throw new IllegalArgumentException("image dimensions must be positive");
        }
        int cropWidth = sourceWidth;
        int cropHeight = sourceHeight;
        long sourceCross = (long) sourceWidth * modelHeight;
        long modelCross = (long) sourceHeight * modelWidth;
        if (sourceCross > modelCross) {
            cropWidth = Math.max(1, sourceHeight * modelWidth / modelHeight);
        } else if (sourceCross < modelCross) {
            cropHeight = Math.max(1, sourceWidth * modelHeight / modelWidth);
        }
        return new int[]{
                (sourceWidth - cropWidth) / 2,
                (sourceHeight - cropHeight) / 2,
                cropWidth,
                cropHeight
        };
    }

    private String errorMessage(Exception error) {
        String message = error.getMessage();
        return message == null ? error.getClass().getSimpleName() : message;
    }

    @Override
    public synchronized void close() {
        if (closed) return;
        closed = true;
        vinsGeneration.incrementAndGet();
        vinsPoseHistory.clear();
        executor.execute(this::closeInferenceResources);
        executor.shutdown();
        mapExecutor.execute(this::closeMapResources);
        mapExecutor.shutdown();
    }

    private void closeMapResources() {
        long map = octomapHandle;
        octomapHandle = 0L;
        if (map != 0L) {
            NativeOctomap.nativeDestroy(map);
        }
    }

    private void closeInferenceResources() {
        OrtSession current = session;
        session = null;
        if (current != null) {
            try {
                current.close();
            } catch (Exception ignored) {
                // The process is shutting down.
            }
        }
        Interpreter interpreter = liteRtInterpreter;
        liteRtInterpreter = null;
        if (interpreter != null) interpreter.close();
        GpuDelegate gpuDelegate = liteRtGpuDelegate;
        liteRtGpuDelegate = null;
        if (gpuDelegate != null) gpuDelegate.close();
        long currentNeuronHandle = neuronHandle;
        neuronHandle = 0L;
        if (currentNeuronHandle != 0L) NativeNeuronShim.nativeDestroy(currentNeuronHandle);
    }
}
