package com.elabrador.mobilenavigation

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Color
import android.location.Location
import android.location.LocationManager
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.os.Process
import android.os.SystemClock
import android.util.Log
import androidx.core.content.ContextCompat
import com.intel.realsense.librealsense.Align
import com.intel.realsense.librealsense.CameraInfo
import com.intel.realsense.librealsense.Config
import com.intel.realsense.librealsense.DepthFrame
import com.intel.realsense.librealsense.Device
import com.intel.realsense.librealsense.DeviceList
import com.intel.realsense.librealsense.DeviceListener
import com.intel.realsense.librealsense.Extension
import com.intel.realsense.librealsense.Extrinsic
import com.intel.realsense.librealsense.Frame
import com.intel.realsense.librealsense.FrameCallback
import com.intel.realsense.librealsense.FrameMetadata
import com.intel.realsense.librealsense.FrameSet
import com.intel.realsense.librealsense.Intrinsic
import com.intel.realsense.librealsense.MotionFrame
import com.intel.realsense.librealsense.Option
import com.intel.realsense.librealsense.Pipeline
import com.intel.realsense.librealsense.PipelineProfile
import com.intel.realsense.librealsense.RsContext
import com.intel.realsense.librealsense.Sensor
import com.intel.realsense.librealsense.StreamFormat
import com.intel.realsense.librealsense.StreamProfile
import com.intel.realsense.librealsense.StreamType
import com.intel.realsense.librealsense.TimestampDomain
import com.intel.realsense.librealsense.VideoFrame
import com.intel.realsense.librealsense.VideoStreamProfile
import java.io.File
import java.io.FileOutputStream
import java.nio.charset.StandardCharsets
import java.util.ArrayDeque
import java.util.Arrays
import java.util.Locale
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong
import java.util.concurrent.atomic.AtomicReference
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sin

/**
 * Severity/color classification that replaces the original app's `R.color.nav_*` resource
 * lookups, since `:navoutdoor` ships no `res/` directory of its own (see deviation notes in the
 * final report). Callback consumers (e.g. `NavigateFragment`) decide the actual color.
 */
enum class GuidanceLevel { SAFE, WARNING, DANGER, MUTED }

/** Mirrors [LocalPlanner.PathResult]'s fields for cross-package delivery via [OutdoorNavController.Listener]. */
data class LocalPlanSnapshot(
    val worldPath: List<FloatArray>,
    val planned: Boolean,
    val success: Boolean,
    val steeringDegrees: Float,
    val blocked: Boolean,
    val startCost: Int,
    val targetCost: Int,
    val obstacleCount: Int,
    val visualizationGrid: Array<IntArray>?,
    val waitingReason: String?,
    val cameraSeconds: Double? = null,
    val captureElapsedMillis: Long = -1L,
    val frameGeneration: Long = -1L
)

/** Mirrors [AmapRouteClient.PlaceSuggestion]'s fields for cross-package delivery. */
data class PlaceSuggestion(
    val name: String,
    val address: String,
    val latitude: Double,
    val longitude: Double,
    val distanceMeters: Int
)

data class VisionHintSettings(
    val enabled: Boolean,
    val key: String,
    val endpoint: String,
    val model: String
)

/**
 * Plain-Kotlin, non-Activity port of `mobile-navigation`'s `MainActivity`: RealSense depth/VINS
 * streaming, VINS-Mono visual-inertial fusion, semantic segmentation, A* local planning, AMap
 * routing/navigation guidance and dynamic heading calibration. All UI is expressed through
 * [Listener] callbacks instead of direct view mutation; the host (e.g. `NavigateFragment`) is
 * responsible for rendering and for the actual `requestPermissions` calls.
 *
 * See the accompanying report for a full list of intentional deviations from `MainActivity`.
 */
class OutdoorNavController(
    private val context: Context,
    private val amapWebKey: String,
    private val listener: Listener
) {

    interface Listener {
        fun onCameraStatus(text: String)
        fun onGuidanceChanged(text: String, level: GuidanceLevel)
        fun onDistances(left: String, center: String, right: String)
        fun onSemanticOverlay(text: String, level: GuidanceLevel)
        fun onLocalPlan(plan: LocalPlanSnapshot)
        /** A* single-run timing/refresh diagnostics, kept separate from [onSemanticOverlay] so the
         * two don't overwrite each other with different line counts on the same view. */
        fun onLocalPlanMetrics(text: String, level: GuidanceLevel)
        fun onHeading(headingDegrees: Float, text: String)
        fun onLocation(location: Location, text: String)
        fun onLocationStatus(text: String)
        fun onSuggestions(suggestions: List<PlaceSuggestion>)
        fun onRouteStatus(text: String)
        fun onNavigationStatus(text: String, visible: Boolean)
        fun onFrameStatus(text: String)
        fun onCalibrationStatus(text: String, calibrated: Boolean, recalibrationRequired: Boolean)
        fun onNavigationStarted()
        fun onNavigationEnded()
        fun onArrived()
        /** Justified addition (not in the directive's literal list): the depth preview bitmap MainActivity rendered into `depthPreview`. */
        fun onDepthPreview(bitmap: Bitmap?)
        /** Reuses the existing RGB8 stream and is available before VINS initialization. */
        fun onColorPreview(bitmap: Bitmap?)
        /** Justified addition: the VINS status panel MainActivity rendered into `vinsStatusPanel`. */
        fun onVinsStatus(text: String, level: GuidanceLevel)
        fun onVisionHints(primary: String, diagnostic: String, enabled: Boolean)
    }

    companion object {
        private const val TAG = "MobileNavigation"
        private const val VALID_MIN_METERS = 0.25f
        private const val VALID_MAX_METERS = 6.0f
        private const val OBSTACLE_METERS = 1.2f
        private const val STOP_METERS = 0.65f
        private const val SAMPLE_STEP_PIXELS = 12
        private const val PREVIEW_DOWNSAMPLE = 2
        private const val VIDEO_WIDTH = 640
        private const val VIDEO_HEIGHT = 480
        private const val VIDEO_FPS = 30
        private const val NAVIGATION_FRAME_INTERVAL = 3
        private const val PREVIEW_FRAME_INTERVAL = 6
        private const val PREVIEW_MAX_METERS = 4.0f
        private const val COLOR_WIDTH = VIDEO_WIDTH
        private const val COLOR_HEIGHT = VIDEO_HEIGHT
        private val COLOR_FRAME_INTERVAL = BuildConfig.SEMANTIC_FRAME_INTERVAL
        private const val COLOR_AUTO_EXPOSURE_LIMIT_US = 16_000f
        private const val COLOR_METADATA_LOG_INTERVAL = 30
        // One second of source frames prevents UI/GC pauses from becoming VINS image
        // timestamp discontinuities. The upstream ROS subscribers use deeper queues.
        private const val VIDEO_FRAME_QUEUE_CAPACITY = VIDEO_FPS + 2
        private val DEPTH_PALETTE = createDepthPalette()
        private const val PREFS_NAME = "outdoor_nav_prefs"
        private const val VISION_PREFS_NAME = "qwen_visual_hints"
        private const val VISION_ENABLED = "enabled"
        private const val VISION_KEY = "key"
        private const val VISION_ENDPOINT = "endpoint"
        private const val VISION_MODEL = "model"

        private fun createDepthPalette(): IntArray {
            val palette = IntArray(256)
            for (i in palette.indices) {
                val hue = i * 240f / 255f
                palette[i] = Color.HSVToColor(floatArrayOf(hue, 1f, 1f))
            }
            return palette
        }

        private fun newVinsExecutor(name: String): ExecutorService =
            Executors.newSingleThreadExecutor { runnable ->
                Thread({
                    Process.setThreadPriority(Process.THREAD_PRIORITY_MORE_FAVORABLE)
                    runnable.run()
                }, name)
            }
    }

    private val mainHandler = Handler(Looper.getMainLooper())
    private fun runOnUiThread(action: () -> Unit) {
        mainHandler.post(action)
    }

    private var released = false

    private val streaming = AtomicBoolean(false)
    private val diagnosticRgbWritten = AtomicBoolean(false)
    private val diagnosticRgbFrames = AtomicLong()
    private val previewUpdatePending = AtomicBoolean(false)
    private val colorPreviewPending = AtomicBoolean(false)
    @Volatile private var colorPreviewGeneration = 0L
    private val uiUpdatePending = AtomicBoolean(false)
    private var previewDepthBuffer: ByteArray? = null
    private var previewAlternateDepthBuffer: ByteArray? = null
    private var previewPixelBuffer: IntArray? = null
    private var previewBitmap: Bitmap? = null
    @Volatile private var previewValidPercent = Float.NaN
    @Volatile private var previewUsesNativeDepth = false

    private var rsContext: RsContext? = null
    private var streamingThread: Thread? = null
    private var activityResumed = false
    private var restartStreamingWhenStopped = false

    private var phonePoseTracker: PhonePoseTracker? = null
    private var amapRouteClient: AmapRouteClient? = AmapRouteClient()
    private var semanticSegmenter: SemanticSegmenter? = null

    private val localPlanner = LocalPlanner()
    private val vinsInput = VinsInputBuffer()
    private val vinsTimestampMapper = RealSenseTimestampMapper()
    private val calibrationVinsPoseHistory = VinsPoseHistory()
    private val outdoorFixGate = OutdoorFixGate()
    @Volatile private var pendingCalibrationLocation: Location? = null
    @Volatile private var latestGpsAccuracyMeters = Float.NaN
    @Volatile private var latestGpsFixElapsedNanos = 0L
    @Volatile private var latestGpsRejection = ""
    @Volatile private var gnssDirectionDiagnostic = "GNSS 行进方向：暂无数据（仅诊断）"
    @Volatile private var latestPoseCaptureMillis = -1L
    @Volatile private var vinsMono: VinsMono? = null
    @Volatile private var latestVinsPose: VinsMono.Pose? = null
    @Volatile private var latestLocalPlan: LocalPlanner.PathResult = LocalPlanner.PathResult.waitingForTarget()
    private val routeFollower = RouteFollower()
    private val dynamicHeadingCalibrator = DynamicHeadingCalibrator()
    private var currentRoute: AmapRouteClient.RouteResult? = null
    @Volatile private var navigationActive = false
    @Volatile private var lastLocation: Location? = null
    @Volatile private var currentHeading = Float.NaN
    @Volatile private var latestHeadingNanos = 0L

    private val searchHandler = Handler(Looper.getMainLooper())
    private val localPlanHandler = Handler(Looper.getMainLooper())
    private val vinsExecutor = newVinsExecutor("vins-feature-tracker")
    private val vinsEstimatorExecutor = newVinsExecutor("vins-estimator")
    private val vinsDrainPending = AtomicBoolean(false)

    // Match the source estimator_node FIFO: every feature message must be paired
    // with the IMU interval ending at that image timestamp, in timestamp order.
    private val vinsEstimateQueueLock = Object()
    private val pendingVinsEstimates = ArrayDeque<VinsEstimateWork>()
    private val vinsEstimatePending = AtomicBoolean(false)

    private val localPlanExecutor = Executors.newSingleThreadExecutor()
    private val localPlanPending = AtomicBoolean(false)
    private val localPlanRefreshRequested = AtomicBoolean(false)
    private val pendingLocalPlanMap = AtomicReference<SemanticSegmenter.Result?>()
    private val localPlanSequence = AtomicLong()
    private val localPlanGeneration = AtomicLong()
    @Volatile private var latestRenderedLocalPlanSequence = 0L
    @Volatile private var latestSemanticResultNanos = 0L
    @Volatile private var latestLocalPlanDurationNanos = -1L
    @Volatile private var latestLocalPlanRefreshNanos = -1L
    @Volatile private var latestLocalPlanCompletedNanos = 0L
    @Volatile private var latestLocalPlanInputAgeNanos = -1L
    @Volatile private var hasValidLocalPlanDisplay = false
    @Volatile private var latestPlanEvidence: FrameEvidence? = null
    @Volatile private var planObservation = PlanObservation(
        LocalPlanner.PathResult.waitingForTarget(), null)
    private var cueEvidence: FrameEvidence? = null
    private var lastAuditCue = ""
    private var lastAuditReason = ""
    private var lastAuditNanos = 0L
    private var lastCalibrationPanelMillis = 0L
    private val renderedObservations = ObservationRefreshTracker()

    @Volatile private var vinsInitialized = false
    @Volatile private var vinsResetCount = 0
    private var consecutiveUninitializedPoses = 0
    @Volatile private var latestVinsPoseNanos = 0L

    private val guidanceStabilizer = GuidanceStabilizer()
    private val guidanceTextComposer = GuidanceTextComposer()
    // Advance cue state once per immutable planning result, not per UI redraw.
    private var lastCuePlan: LocalPlanner.PathResult? = null
    private var lastPlanCue = ""
    private val navigationCueTracker = NavigationCue.Tracker()
    private var destinationGeneration = 0L
    private var pendingDestinationSearch: Runnable? = null
    private var selectedDestination: AmapRouteClient.PlaceSuggestion? = null
    /** 定位到达前发起的附近地点搜索请求的关键字：定位权限刚授予时手机定位尚未就位，
     * 记下关键字待 [onLocation] 首次收到定位后自动补搜一次，避免用户必须重启应用。 */
    private var pendingLocationSearchKeyword: String? = null
    @Volatile private var latestSemanticResult: SemanticSegmenter.Result = SemanticSegmenter.Result.waiting()
    private var navigationWakeLock: PowerManager.WakeLock? = null

    private data class PlanObservation(
        val plan: LocalPlanner.PathResult,
        val evidence: FrameEvidence?
    )

    private val visionPreferences =
        context.getSharedPreferences(VISION_PREFS_NAME, Context.MODE_PRIVATE)
    @Volatile private var visionHintSnapshot = VisionHintSnapshot.EMPTY
    private val visionHints = QwenVisionHints(context.applicationContext) { primary, diagnostic, snapshot ->
        visionHintSnapshot = snapshot
        listener.onVisionHints(primary, diagnostic, visionHintSettings().enabled)
        renderDirectionGuidance()
    }
    private val safetyTick = object : Runnable {
        override fun run() {
            if (released) return
            renderDirectionGuidance()
            val now = SystemClock.elapsedRealtime()
            val evidence = latestPlanEvidence
            if (navigationActive && hasValidLocalPlanDisplay && evidence != null && !evidence.fresh(now)) {
                hasValidLocalPlanDisplay = false
                listener.onLocalPlan(LocalPlanner.PathResult.waiting("观测已过期，等待新地图").toSnapshot())
            }
            if (now - lastCalibrationPanelMillis >= 1000L) {
                lastCalibrationPanelMillis = now
                renderDynamicHeadingCalibration()
            }
            if (navigationActive) updateNavigationGuidance()
            mainHandler.postDelayed(this, 100L)
        }
    }

    init {
        NavigationAudit.start(context.applicationContext)
        val settings = visionHintSettings()
        visionHints.configure(settings.key, settings.endpoint, settings.model, settings.enabled)
        dynamicHeadingCalibrator.start()
        mainHandler.postDelayed(safetyTick, 100L)

        val power = context.getSystemService(Context.POWER_SERVICE) as? PowerManager
        if (power != null) {
            navigationWakeLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "$TAG:navigation")
            navigationWakeLock?.setReferenceCounted(false)
        }

        semanticSegmenter = SemanticSegmenter(context.applicationContext, object : SemanticSegmenter.Listener {
            override fun onStatus(status: String) {
                runOnUiThread { listener.onSemanticOverlay(status, GuidanceLevel.MUTED) }
            }

            override fun onResult(result: SemanticSegmenter.Result) {
                latestSemanticResult = result
                latestSemanticResultNanos = SystemClock.elapsedRealtimeNanos()
                if (navigationActive) {
                    requestLocalPlanRefresh(result)
                } else {
                    runOnUiThread { renderSemanticStatus(result) }
                }
            }

            override fun onError(message: String) {
                Log.e(TAG, "semantic segmenter error: $message")
                runOnUiThread {
                    listener.onSemanticOverlay(
                        "${BuildConfig.SEMANTIC_MODEL_NAME} 错误：$message", GuidanceLevel.DANGER)
                }
            }
        })
        semanticSegmenter?.initialize()

        renderDynamicHeadingCalibration()

        phonePoseTracker = PhonePoseTracker(context, object : PhonePoseTracker.Listener {
            override fun onHeading(headingDegrees: Float) {
                currentHeading = headingDegrees
                latestHeadingNanos = SystemClock.elapsedRealtimeNanos()
                listener.onHeading(
                    headingDegrees,
                    String.format(Locale.CHINA, "朝向\n%.0f° %s", headingDegrees, cardinalDirection(headingDegrees)))

                updateNavigationGuidance()
            }

            override fun onLocation(location: Location) {
                if (LocationManager.GPS_PROVIDER != location.provider) return
                latestGpsAccuracyMeters =
                    if (location.hasAccuracy()) location.accuracy else Float.NaN
                latestGpsFixElapsedNanos = location.elapsedRealtimeNanos
                gnssDirectionDiagnostic = describeGnssDirection(location)
                val accepted = outdoorFixGate.accept(
                    location.latitude,
                    location.longitude,
                    latestGpsAccuracyMeters,
                    location.elapsedRealtimeNanos,
                    SystemClock.elapsedRealtimeNanos())
                latestGpsRejection = if (accepted) "" else outdoorFixGate.rejection
                NavigationAudit.log(
                    "GPS_AUDIT accepted=$accepted fix_ns=${location.elapsedRealtimeNanos}" +
                        " lat=${location.latitude} lon=${location.longitude}" +
                        " accuracy=${location.accuracy} reason=${outdoorFixGate.rejection}")
                NavigationAudit.log(
                    "GNSS_DIRECTION fix_ns=${location.elapsedRealtimeNanos} $gnssDirectionDiagnostic")
                if (!accepted) {
                    listener.onLocationStatus(outdoorFixGate.rejection)
                    renderDynamicHeadingCalibration()
                    return
                }
                lastLocation = Location(location)
                listener.onLocation(
                    location,
                    String.format(
                        Locale.CHINA,
                        "位置\n%.6f, %.6f\n精度 %.0f m",
                        location.latitude,
                        location.longitude,
                        if (location.hasAccuracy()) location.accuracy else 0f))
                updateDynamicHeadingCalibration(location)
                updateNavigationGuidance()
                pendingLocationSearchKeyword?.let { keyword ->
                    pendingLocationSearchKeyword = null
                    searchDestinationSuggestions(keyword)
                }
            }

            override fun onLocationStatus(status: String) {
                listener.onLocationStatus(status)
            }
        })

        RsContext.init(context.applicationContext)
        rsContext = RsContext()
        rsContext?.setDevicesChangedCallback(object : DeviceListener {
            override fun onDeviceAttach() {
                runOnUiThread { listener.onCameraStatus("深度相机已连接") }
                startStreaming()
            }

            override fun onDeviceDetach() {
                colorPreviewGeneration++
                stopStreaming()
                runOnUiThread {
                    listener.onDepthPreview(null)
                    listener.onColorPreview(null)
                    listener.onCameraStatus("深度相机已断开")
                    listener.onGuidanceChanged("", GuidanceLevel.MUTED)
                    renderVinsStatus(vinsInput.status())
                }
            }
        })
    }

    fun visionHintSettings(): VisionHintSettings = VisionHintSettings(
        enabled = visionPreferences.getBoolean(VISION_ENABLED, true),
        key = visionPreferences.getString(VISION_KEY, BuildConfig.QWEN_KEY).orEmpty(),
        endpoint = visionPreferences.getString(VISION_ENDPOINT, BuildConfig.QWEN_ENDPOINT).orEmpty(),
        model = visionPreferences.getString(VISION_MODEL, BuildConfig.QWEN_MODEL)
            .orEmpty().ifBlank { "qwen3-vl-flash" }
    )

    fun updateVisionHintSettings(settings: VisionHintSettings) {
        val endpoint = settings.endpoint.trim()
        if (endpoint.isNotEmpty()) QwenVisionHints.normalizeEndpoint(endpoint)
        val normalized = settings.copy(
            key = settings.key.trim(),
            endpoint = endpoint,
            model = settings.model.trim().ifBlank { "qwen3-vl-flash" })
        visionPreferences.edit()
            .putBoolean(VISION_ENABLED, normalized.enabled)
            .putString(VISION_KEY, normalized.key)
            .putString(VISION_ENDPOINT, normalized.endpoint)
            .putString(VISION_MODEL, normalized.model)
            .apply()
        visionHints.configure(
            normalized.key, normalized.endpoint, normalized.model, normalized.enabled)
        listener.onVisionHints(
            if (normalized.enabled) "左侧：无\n左前方：无\n正前方：无\n右前方：无\n右侧：无" else "",
            if (normalized.enabled) "等待彩色画面" else "已关闭",
            normalized.enabled)
        renderDirectionGuidance()
    }

    // ---------------------------------------------------------------------
    // Permissions (actual requestPermissions calls stay with the host)
    // ---------------------------------------------------------------------

    fun hasCameraPermission(): Boolean =
        ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) ==
            PackageManager.PERMISSION_GRANTED

    fun hasLocationPermission(): Boolean =
        ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_FINE_LOCATION) ==
            PackageManager.PERMISSION_GRANTED

    fun onCameraPermissionGranted() {
        startStreaming()
    }

    fun onLocationPermissionGranted() {
        phonePoseTracker?.start()
    }

    private fun cardinalDirection(headingDegrees: Float): String {
        val directions = arrayOf("北", "东北", "东", "东南", "南", "西南", "西", "西北")
        val index = Math.round(headingDegrees / 45f) % directions.size
        return directions[if (index < 0) index + directions.size else index]
    }

    // ---------------------------------------------------------------------
    // Lifecycle
    // ---------------------------------------------------------------------

    @Synchronized
    fun onResume() {
        activityResumed = true
        visionHints.setForeground(true)
        rsContext?.let { rs ->
            try {
                // DeviceList implements only AutoCloseable (not java.io.Closeable), so
                // Kotlin's Closeable-bound `use {}` does not apply here.
                val devices = rs.queryDevices()
                try {
                    if (devices.deviceCount > 0) {
                        startStreaming()
                    }
                } finally {
                    devices.close()
                }
            } catch (error: Exception) {
                listener.onCameraStatus("相机检测失败: ${error.message}")
            }
        }
        phonePoseTracker?.start()
    }

    fun onPause() {
        visionHints.setForeground(false)
        colorPreviewGeneration++
        listener.onColorPreview(null)
        synchronized(this) {
            activityResumed = false
        }
        // Keep the navigation pipeline alive while the screen is off or another
        // app is briefly foregrounded. It is explicitly stopped in onDestroy or
        // when the user ends navigation.
        if (!navigationActive) {
            stopStreaming()
            phonePoseTracker?.stop()
        }
        listener.onDepthPreview(null)
        if (!navigationActive) localPlanHandler.removeCallbacksAndMessages(null)
    }

    fun onDestroy() {
        visionHints.close()
        mainHandler.removeCallbacksAndMessages(null)
        colorPreviewGeneration++
        listener.onColorPreview(null)
        released = true
        releaseNavigationWakeLock()
        stopStreaming()
        rsContext?.close()
        rsContext = null
        amapRouteClient?.close()
        amapRouteClient = null
        semanticSegmenter?.close()
        semanticSegmenter = null
        searchHandler.removeCallbacksAndMessages(null)
        localPlanHandler.removeCallbacksAndMessages(null)
        vinsExecutor.shutdownNow()
        vinsEstimatorExecutor.shutdownNow()
        localPlanExecutor.shutdownNow()
        phonePoseTracker = null
    }

    // ---------------------------------------------------------------------
    // Destination search / route planning / navigation control
    // ---------------------------------------------------------------------

    fun searchDestination(query: String) {
        val keyword = query.trim()
        destinationGeneration++
        selectedDestination = null
        pendingLocationSearchKeyword = null
        scheduleDestinationSearch(keyword)
    }

    private fun scheduleDestinationSearch(keyword: String) {
        pendingDestinationSearch?.let { searchHandler.removeCallbacks(it) }
        if (keyword.length < 2) {
            listener.onSuggestions(emptyList())
            return
        }
        val runnable = Runnable { searchDestinationSuggestions(keyword) }
        pendingDestinationSearch = runnable
        searchHandler.postDelayed(runnable, 500L)
    }

    private fun searchDestinationSuggestions(keyword: String) {
        val key = amapWebKey.trim()
        if (key.isEmpty()) {
            listener.onRouteStatus("输入高德 Key 后显示附近地点")
            return
        }
        val location = lastLocation
        if (location == null) {
            listener.onRouteStatus("等待手机定位后显示附近地点")
            pendingLocationSearchKeyword = keyword
            return
        }

        val searchGeneration = destinationGeneration
        amapRouteClient?.searchNearby(key, location, keyword, object : AmapRouteClient.SearchCallback {
            override fun onSuccess(suggestions: MutableList<AmapRouteClient.PlaceSuggestion>) {
                runOnUiThread {
                    if (searchGeneration != destinationGeneration || released) return@runOnUiThread
                    listener.onSuggestions(suggestions.map {
                        PlaceSuggestion(it.name, it.address, it.latitude, it.longitude, it.distanceMeters)
                    })
                }
            }

            override fun onError(message: String) {
                runOnUiThread {
                    if (searchGeneration == destinationGeneration && !released) {
                        listener.onRouteStatus(message)
                    }
                }
            }
        })
    }

    /** Mirrors MainActivity's selectDestinationSuggestion: records the choice and starts routing. */
    fun selectSuggestion(suggestion: PlaceSuggestion) {
        pendingDestinationSearch?.let { searchHandler.removeCallbacks(it) }
        selectedDestination = AmapRouteClient.PlaceSuggestion(
            suggestion.name, suggestion.address, suggestion.latitude,
            suggestion.longitude, suggestion.distanceMeters)
        planRoute(suggestion.name)
    }

    /**
     * Mirrors MainActivity's planWalkingRoute. [destinationTextIfNoSuggestionSelected] is used
     * only when [selectSuggestion] was not called first (free-text destination entry).
     */
    fun planRoute(destinationTextIfNoSuggestionSelected: String) {
        val key = amapWebKey.trim()
        val destination = destinationTextIfNoSuggestionSelected.trim()
        if (key.isEmpty()) {
            listener.onRouteStatus("请输入高德 Web 服务 Key")
            return
        }
        if (destination.isEmpty()) {
            listener.onRouteStatus("请输入目的地名称或完整地址")
            return
        }
        val location = lastLocation
        if (location == null || !gpsFresh()) {
            listener.onRouteStatus("尚未获得新鲜手机定位，请到室外等待定位")
            return
        }

        stopNavigationAndClearRoute()
        val requestGeneration = destinationGeneration
        listener.onRouteStatus("正在查询目的地和步行路线…")
        val callback = object : AmapRouteClient.Callback {
            override fun onSuccess(result: AmapRouteClient.RouteResult) {
                runOnUiThread {
                    if (requestGeneration != destinationGeneration || released) return@runOnUiThread
                    currentRoute = result
                    routeFollower.setRoute(result)
                    resetLocalPlanning()
                    listener.onRouteStatus(String.format(
                        Locale.CHINA,
                        "%s\n步行 %.2f km，约 %d 分钟\n路线已就绪",
                        result.destinationName,
                        result.distanceMeters / 1000f,
                        max(1, Math.round(result.durationSeconds / 60f))))
                    // A place suggestion selection completes route planning and starts navigation.
                    toggleNavigation()
                }
            }

            override fun onError(message: String) {
                runOnUiThread {
                    if (requestGeneration != destinationGeneration || released) return@runOnUiThread
                    listener.onRouteStatus("$message；可重新选择地点重试")
                }
            }
        }
        val suggestion = selectedDestination
        if (suggestion != null) {
            amapRouteClient?.planWalkingRoute(key, location, suggestion, callback)
        } else {
            amapRouteClient?.planWalkingRoute(key, location, destination, callback)
        }
    }

    private fun toggleNavigation() {
        if (navigationActive) {
            endNavigation()
            return
        }
        if (currentRoute == null || !routeFollower.hasRoute()) {
            listener.onNavigationStatus("请先规划一条步行路线", true)
            return
        }
        if (lastLocation == null) {
            listener.onNavigationStatus("等待手机定位后才能开始导航", true)
            return
        }
        navigationActive = true
        guidanceStabilizer.reset()
        guidanceTextComposer.reset()
        val wakeLock = navigationWakeLock
        if (wakeLock != null && !wakeLock.isHeld) {
            wakeLock.acquire()
        }
        listener.onGuidanceChanged("", GuidanceLevel.MUTED)
        requestLocalPlanRefresh()
        listener.onNavigationStarted()
        updateNavigationGuidance()
    }

    /** 用户手动点击"结束导航"：清空路线状态，并通知上层推进到计划的下一阶段 */
    fun endNavigation() {
        stopNavigationAndClearRoute()
        listener.onGuidanceChanged("", GuidanceLevel.MUTED)
        listener.onNavigationEnded()
    }

    /** 有新的导航阶段/计划下发时调用：清空上一次路线与导航状态，不触发 onNavigationEnded 语义
     * （该回调专用于"用户点击结束导航"并据此推进计划，与"重新下发指令需要重置状态"语义不同）。 */
    fun resetForRestart() {
        stopNavigationAndClearRoute()
    }

    /**
     * 仅清空导航/路线内部状态，不回调 [Listener.onNavigationEnded]——该回调会被上层解读为
     * "用户点击了结束导航" 并据此推进多阶段计划，而这里也被 [planRoute] 在规划新路线前用来
     * 重置上一次的路线状态，两者语义不同，不能共用同一个通知。
     */
    private fun stopNavigationAndClearRoute() {
        destinationGeneration++
        guidanceStabilizer.reset()
        guidanceTextComposer.reset()
        navigationActive = false
        releaseNavigationWakeLock()
        listener.onGuidanceChanged("", GuidanceLevel.MUTED)
        resetLocalPlanning()
        currentRoute = null
        routeFollower.clear()
    }

    private fun releaseNavigationWakeLock() {
        val wakeLock = navigationWakeLock
        if (wakeLock != null && wakeLock.isHeld) {
            wakeLock.release()
        }
    }

    private fun gpsFresh(): Boolean {
        val fix = lastLocation ?: return false
        val age = SystemClock.elapsedRealtimeNanos() - fix.elapsedRealtimeNanos
        return age >= 0L && age < TimeUnit.SECONDS.toNanos(5)
    }

    private fun updateNavigationGuidance() {
        val location = lastLocation
        if (!navigationActive || location == null || !routeFollower.hasRoute()) return
        if (!gpsFresh()) {
            listener.onNavigationStatus("等待新鲜 GPS 定位（超过 5 秒）", true)
            return
        }
        val guidance = routeFollower.update(
            location.latitude,
            location.longitude,
            if (location.hasAccuracy()) location.accuracy else Float.NaN,
            Float.NaN,
            location.elapsedRealtimeNanos)
        if (guidance == null) {
            listener.onNavigationStatus(routeFollower.waitingReason(), true)
            return
        }

        val pose = latestVinsPose
        val cameraRelativeTarget =
            dynamicHeadingCalibrator.relativeTargetDegrees(guidance.targetBearingDegrees, pose)
        val cameraTarget =
            if (dynamicHeadingCalibrator.isReady() && cameraRelativeTarget.isFinite()) {
                String.format(Locale.CHINA, "D455 局部目标 %+.0f°", cameraRelativeTarget)
            } else {
                dynamicHeadingCalibrator.status()
            }
        val deviation = if (guidance.offRoute) {
            String.format(Locale.CHINA, "\n偏离路线约 %d 米", guidance.crossTrackMeters)
        } else ""
        val semanticWarning = if (latestSemanticResult.isNotWalkable) {
            String.format(
                Locale.CHINA, "\n%s：%s（%.0f%%），不可通行",
                BuildConfig.SEMANTIC_MODEL_NAME,
                latestSemanticResult.label,
                latestSemanticResult.areaRatio * 100f)
        } else ""
        listener.onNavigationStatus(
            String.format(
                Locale.CHINA,
                "剩余 %s · 距下一步 %s\n%s\n目标方位 %.0f° · %s%s%s",
                formatNavigationDistance(guidance.remainingMeters),
                formatNavigationDistance(guidance.distanceToInstructionMeters),
                guidance.instruction,
                guidance.targetBearingDegrees,
                cameraTarget,
                deviation,
                semanticWarning),
            true)

        if (guidance.arrived) {
            Log.i(
                TAG,
                "Arrived at destination: remainingMeters=${guidance.remainingMeters}," +
                    " gpsAccuracyMeters=${location.accuracy}")
            navigationActive = false
            releaseNavigationWakeLock()
            guidanceStabilizer.reset()
            guidanceTextComposer.reset()
            resetLocalPlanning()
            listener.onArrived()
            listener.onGuidanceChanged("", GuidanceLevel.MUTED)
        }
    }

    /** Returns the fresh phone compass heading corrected from magnetic to true north. */


    private fun formatNavigationDistance(meters: Int): String {
        if (meters < 1000) {
            return String.format(Locale.CHINA, "%d 米", meters)
        }
        return String.format(Locale.CHINA, "%.1f 公里", meters / 1000f)
    }

    // ---------------------------------------------------------------------
    // Heading calibration
    // ---------------------------------------------------------------------

    fun calibrateHeading() {
        dynamicHeadingCalibrator.start()
        pendingCalibrationLocation = null
        renderDynamicHeadingCalibration()
        resetLocalPlanning()
        requestLocalPlanRefresh()
    }

    /**
     * Snapshots the aligned phone compass against the settled VINS world frame once at entry.
     * A mid-session VINS restart permanently switches this controller session to manual mode.
     */




    private fun updateDynamicHeadingCalibration(location: Location) {
        if (LocationManager.GPS_PROVIDER != location.provider) return
        pendingCalibrationLocation = Location(location)
        tryPendingCalibration()
    }

    private fun tryPendingCalibration() {
        val location = pendingCalibrationLocation ?: return
        if (dynamicHeadingCalibrator.isReady()) return
        val ageMillis =
            (SystemClock.elapsedRealtimeNanos() - location.elapsedRealtimeNanos) / 1_000_000L
        if (ageMillis > 5_000L) {
            dynamicHeadingCalibrator.waitForFreshGps()
            renderDynamicHeadingCalibration()
            return
        }
        val pose = calibrationVinsPoseHistory.atOrNearest(location.time / 1000.0, 0.10)
        if (pose == null) {
            searchHandler.postDelayed({
                if (pendingCalibrationLocation === location) tryPendingCalibration()
            }, 20L)
            dynamicHeadingCalibrator.waitForTimeAlignedVinsPose()
            renderDynamicHeadingCalibration()
            return
        }
        pendingCalibrationLocation = null
        val fitStarted = SystemClock.elapsedRealtimeNanos()
        NavigationAudit.log(
            "CALIBRATION_PAIR gps_s=${location.time / 1000.0} vins_s=${pose.timestamp}" +
                " delta_ms=${Math.abs(location.time - pose.timestamp * 1000)}" +
                " lat=${location.latitude} lon=${location.longitude}" +
                " accuracy=${location.accuracy} vins_x=${pose.x} vins_y=${pose.y}" +
                " vins_z=${pose.z} camera_yaw=${pose.egoRightAxisYawRadians()}")
        dynamicHeadingCalibrator.updateTimed(
            location.latitude,
            location.longitude,
            if (location.hasAccuracy()) location.accuracy else Float.POSITIVE_INFINITY,
            pose.x,
            pose.y,
            pose.initialized,
            location.elapsedRealtimeNanos)
        NavigationAudit.log(
            "CALIBRATION_FIT points=${dynamicHeadingCalibrator.sampleCount()}" +
                " compute_ms=${(SystemClock.elapsedRealtimeNanos() - fitStarted) / 1e6}" +
                " fix_age_ms=$ageMillis total_points=${dynamicHeadingCalibrator.totalSampleCount()}" +
                " ready=${dynamicHeadingCalibrator.isReady()}" +
                " reason=${dynamicHeadingCalibrator.status()}" +
                " quality=${dynamicHeadingCalibrator.qualityDetails().replace('\n', ' ')}")
        if (dynamicHeadingCalibrator.isReady()) {
            dynamicHeadingCalibrator.save(
                context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE))
        }
        renderDynamicHeadingCalibration()
    }

    private fun describeGnssDirection(location: Location): String {
        val bearing = if (location.hasBearing() && location.bearing.isFinite()) {
            String.format(Locale.CHINA, "%.1f°", location.bearing)
        } else "未提供"
        var bearingAccuracy = "未提供"
        var speedAccuracy = "未提供"
        if (android.os.Build.VERSION.SDK_INT >= 26) {
            if (location.hasBearingAccuracy() && location.bearingAccuracyDegrees.isFinite()) {
                bearingAccuracy =
                    String.format(Locale.CHINA, "±%.1f°", location.bearingAccuracyDegrees)
            }
            if (location.hasSpeedAccuracy() && location.speedAccuracyMetersPerSecond.isFinite()) {
                speedAccuracy =
                    String.format(Locale.CHINA, "±%.2fm/s", location.speedAccuracyMetersPerSecond)
            }
        }
        val speed = if (location.hasSpeed() && location.speed.isFinite()) {
            String.format(Locale.CHINA, "%.2fm/s", location.speed)
        } else "未提供"
        return "GNSS 行进方向：$bearing · 方向精度 $bearingAccuracy" +
            "\n速度 $speed · 速度精度 $speedAccuracy（仅记录，不参与标定）"
    }

    private fun renderDynamicHeadingCalibration() {
        val ready = dynamicHeadingCalibrator.isReady()
        val gpsAgeMillis = if (latestGpsFixElapsedNanos > 0L) {
            (SystemClock.elapsedRealtimeNanos() - latestGpsFixElapsedNanos) / 1_000_000L
        } else Long.MAX_VALUE
        val text = CalibrationStatusFormatter.format(
            latestGpsAccuracyMeters,
            gpsAgeMillis,
            dynamicHeadingCalibrator,
            latestGpsRejection) + "\n" + gnssDirectionDiagnostic
        val accepted =
            CalibrationStatusFormatter.accuracyAccepted(latestGpsAccuracyMeters) &&
                CalibrationStatusFormatter.isFresh(gpsAgeMillis) &&
                latestGpsRejection.isEmpty()
        listener.onCalibrationStatus(text, ready, !ready && accepted)
    }

    // ---------------------------------------------------------------------
    // RealSense streaming
    // ---------------------------------------------------------------------

    @Synchronized
    private fun startStreaming() {
        if (!hasCameraPermission()) {
            return
        }
        val existing = streamingThread
        if (existing != null && existing.isAlive) {
            restartStreamingWhenStopped = activityResumed
            return
        }
        restartStreamingWhenStopped = false
        streaming.set(true)
        val thread = Thread({ streamDepth() }, "realsense-depth")
        streamingThread = thread
        thread.start()
    }

    @Synchronized
    private fun stopStreaming() {
        restartStreamingWhenStopped = false
        streaming.set(false)
        streamingThread?.interrupt()
    }

    private fun streamDepth() {
        val pipeline = Pipeline()
        val videoFrames = ArrayBlockingQueue<FrameSet>(VIDEO_FRAME_QUEUE_CAPACITY)
        val droppedVideoFrames = AtomicLong()
        vinsTimestampMapper.reset()
        // RealSense SDK classes (Config, Align, PipelineProfile, ...) and this file's own
        // VerifiedDepthAligner/SingleFrameWorker implement only java.lang.AutoCloseable, not
        // java.io.Closeable, so Kotlin's Closeable-bound `use {}` does not apply to them.
        // Every try-with-resources block from MainActivity is ported as an explicit
        // try/finally nest instead, closing in the same reverse-declaration order Java would.
        try {
            val config = Config()
            try {
                // The source tracker consumes every camera frame and only publishes
                // features at 10 Hz. At 15 FPS, fast turns doubled the optical-flow
                // displacement and collapsed inter-frame feature overlap.
                config.enableStream(StreamType.DEPTH, -1, VIDEO_WIDTH, VIDEO_HEIGHT, StreamFormat.Z16, VIDEO_FPS)
                config.enableStream(StreamType.COLOR, -1, VIDEO_WIDTH, VIDEO_HEIGHT, StreamFormat.RGB8, VIDEO_FPS)
                config.enableStream(StreamType.GYRO, -1, 0, 0, StreamFormat.MOTION_XYZ32F, 200)
                config.enableStream(StreamType.ACCEL, -1, 0, 0, StreamFormat.MOTION_XYZ32F, 100)

                val callback = FrameCallback { incoming ->
                    try {
                        if (incoming == null || !streaming.get()) return@FrameCallback
                        if (incoming.`is`(Extension.MOTION_FRAME)) {
                            collectVinsImu(incoming)
                        } else if (incoming.`is`(Extension.COMPOSITE_FRAME)) {
                            val retained = incoming.`as`<FrameSet>(Extension.COMPOSITE_FRAME).clone()
                            if (!videoFrames.offer(retained)) {
                                retained.close()
                                val dropped = droppedVideoFrames.incrementAndGet()
                                if (dropped == 1L || dropped % 15L == 0L) {
                                    Log.w(TAG, "VINS_VIDEO_DROP count=$dropped queue=${videoFrames.size}")
                                }
                            }
                        }
                    } catch (error: Exception) {
                        Log.e(TAG, "RealSense callback failed", error)
                    }
                }

                val profile: PipelineProfile = pipeline.start(config, callback)
                try {
                    val align = Align(StreamType.COLOR)
                    try {
                        val verifiedAlign = VerifiedDepthAligner()
                        try {
                            val depthWorker = SingleFrameWorker()
                            try {
                                val colorIntrinsic = initializeVins(profile)
                                runOnUiThread { listener.onCameraStatus("深度相机运行中") }
                                var frameCount = 0L
                                val semanticCaptureCount = AtomicLong()
                                var consecutiveVideoTimeouts = 0

                                while (streaming.get() && !Thread.currentThread().isInterrupted) {
                                    val queued = videoFrames.poll(1000, TimeUnit.MILLISECONDS)
                                    if (queued == null) {
                                        consecutiveVideoTimeouts++
                                        if (consecutiveVideoTimeouts >= 5) {
                                            throw IllegalStateException("连续 5 秒未收到深度视频帧，请重插相机")
                                        }
                                        continue
                                    }
                                    consecutiveVideoTimeouts = 0
                                    val frames = queued
                                    val colorFrame: Frame? = frames.first(StreamType.COLOR)
                                    try {
                                        frameCount++
                                        var rgb: ByteArray? = null
                                        val intrinsic: Intrinsic? = colorIntrinsic
                                        var imageTime = Double.NaN
                                        var colorWidth = 0
                                        var colorHeight = 0
                                        var colorStride = 0
                                        if (colorFrame != null) {
                                            val color = colorFrame.`as`<VideoFrame>(Extension.VIDEO_FRAME)
                                            logColorFrameMetadata(colorFrame, frameCount)
                                            colorWidth = color.width
                                            colorHeight = color.height
                                            colorStride = color.stride
                                            val frameRgb = ByteArray(color.dataSize)
                                            color.getData(frameRgb)
                                            updateColorPreview(frameRgb, colorWidth, colorHeight, colorStride, frameCount)
                                            rgb = frameRgb
                                            if (BuildConfig.DEBUG) {
                                                writeDiagnosticRgbOnce(frameRgb, colorWidth, colorHeight, colorStride)
                                            }
                                            imageTime = vinsTimestampMapper.toSystemTimeMilliseconds(
                                                color.timestamp, color.timestampDomain, System.currentTimeMillis().toDouble())
                                            val imageAge = (System.currentTimeMillis() - imageTime).toLong()
                                            if (imageAge >= -100L && imageAge < 1000L) {
                                                visionHints.offer(
                                                    frameRgb, colorWidth, colorHeight, colorStride,
                                                    SystemClock.elapsedRealtime() - max(0L, imageAge))
                                            }
                                            vinsInput.addImage(imageTime, frameRgb, colorWidth, colorHeight, colorStride, intrinsic)
                                            requestVinsProcessing()
                                        }

                                        // VINS remains independent at 30 Hz. Navigation rendering stays at
                                        // 10 Hz, while an idle semantic worker may consume an additional
                                        // time-aligned RGB/depth frame instead of being capped at 10 Hz.
                                        val navigationFrame = frameCount % NAVIGATION_FRAME_INTERVAL == 0L
                                        val segmenter = semanticSegmenter
                                        val semanticFrame = rgb != null && intrinsic != null &&
                                            imageTime.isFinite() && vinsInitialized &&
                                            frameCount % COLOR_FRAME_INTERVAL == 0L &&
                                            segmenter != null && segmenter.canAcceptFrame()
                                        if ((navigationFrame || semanticFrame) && depthWorker.tryAcquire()) {
                                            val retained = frames.clone()
                                            val frameRgb = rgb
                                            val frameWidth = colorWidth
                                            val frameHeight = colorHeight
                                            val frameStride = colorStride
                                            val frameIntrinsic = intrinsic
                                            val frameTime = imageTime
                                            val frameNumber = frameCount
                                            try {
                                                depthWorker.execute {
                                                    val owned = retained
                                                    val ownedColor: Frame? = owned.first(StreamType.COLOR)
                                                    try {
                                                        processNavigationFrame(
                                                            owned, ownedColor, frameRgb, frameWidth, frameHeight,
                                                            frameStride, frameIntrinsic, frameTime, frameNumber,
                                                            navigationFrame, semanticFrame, align, verifiedAlign,
                                                            semanticCaptureCount)
                                                    } catch (error: Exception) {
                                                        Log.e(TAG, "Depth stage failed", error)
                                                    } finally {
                                                        ownedColor?.close()
                                                        owned.close()
                                                    }
                                                }
                                            } catch (rejected: RuntimeException) {
                                                retained.close()
                                                throw rejected
                                            }
                                        }
                                    } finally {
                                        colorFrame?.close()
                                        frames.close()
                                    }
                                }
                            } finally {
                                depthWorker.close()
                            }
                        } finally {
                            verifiedAlign.close()
                        }
                    } finally {
                        align.close()
                    }
                } finally {
                    profile.close()
                }
            } finally {
                config.close()
            }
        } catch (error: Exception) {
            Log.e(TAG, "RealSense streaming stopped", error)
            if (streaming.get()) {
                runOnUiThread {
                    listener.onCameraStatus("深度相机启动失败")
                    listener.onFrameStatus(error.message ?: error.javaClass.simpleName)
                }
            }
        } finally {
            try {
                pipeline.stop()
            } catch (ignored: Exception) {
                // Pipeline may already be stopped after a USB disconnect.
            }
            var queued = videoFrames.poll()
            while (queued != null) {
                queued.close()
                queued = videoFrames.poll()
            }
            vinsInput.clear()
            closeVins()
            var restart: Boolean
            synchronized(this) {
                streaming.set(false)
                if (Thread.currentThread() === streamingThread) {
                    streamingThread = null
                }
                restart = restartStreamingWhenStopped && activityResumed
                restartStreamingWhenStopped = false
            }
            if (restart) runOnUiThread { startStreaming() }
        }
    }

    /** Uses the existing RGB stream before VINS is ready and keeps at most one UI frame queued. */
    private fun updateColorPreview(rgb: ByteArray, width: Int, height: Int, stride: Int, frame: Long) {
        if (!activityResumed || frame % PREVIEW_FRAME_INTERVAL != 0L ||
            !colorPreviewPending.compareAndSet(false, true)) return
        val generation = colorPreviewGeneration
        runOnUiThread {
            try {
                if (!activityResumed || released || generation != colorPreviewGeneration) return@runOnUiThread
                val step = 2
                val pixels = RgbPreviewPixels.convert(rgb, width, height, stride, step)
                val bitmap = Bitmap.createBitmap(
                    pixels,
                    (width + step - 1) / step,
                    (height + step - 1) / step,
                    Bitmap.Config.ARGB_8888)
                listener.onColorPreview(bitmap)
            } finally {
                colorPreviewPending.set(false)
            }
        }
    }

    private fun processNavigationFrame(
        frames: FrameSet,
        colorFrame: Frame?,
        rgb: ByteArray?,
        colorWidth: Int,
        colorHeight: Int,
        colorStride: Int,
        intrinsic: Intrinsic?,
        imageTime: Double,
        frameCount: Long,
        navigationFrame: Boolean,
        semanticFrame: Boolean,
        align: Align,
        verifiedAlign: VerifiedDepthAligner,
        semanticCaptureCount: AtomicLong
    ) {
        val alignStartedNanos = SystemClock.elapsedRealtimeNanos()
        val rawDepthFrame: Frame? = frames.first(StreamType.DEPTH)
        try {
            // MainActivity does not null-check rawDepthFrame here either (line 1000-1001 of the
            // original) — ported as-is rather than "fixed" to stay a faithful 1:1 port.
            val rawDepth = rawDepthFrame!!.`as`<DepthFrame>(Extension.DEPTH_FRAME)
            if (colorFrame == null) return
            val sameClock = rawDepthFrame.timestampDomain == colorFrame.timestampDomain
            val skewMillis = Math.abs(rawDepthFrame.timestamp - colorFrame.timestamp)
            if (!FramePairTiming.valid(sameClock, skewMillis)) {
                NavigationAudit.log(
                    "CAPTURE_REJECT rgb=${colorFrame.number} depth=${rawDepthFrame.number}" +
                        " skew_ms=$skewMillis")
                return
            }
            if (semanticFrame) {
                NavigationAudit.log(
                    "CAPTURE_AUDIT frame=${imageTime / 1000.0} rgb=${colorFrame.number}" +
                        " depth=${rawDepthFrame.number} same_clock=$sameClock skew_ms=$skewMillis")
            }
            val alignedDepth = verifiedAlign.process(frames, rawDepth, colorFrame, align)
            val alignedNanos = SystemClock.elapsedRealtimeNanos()
            var semanticCopiedNanos = alignedNanos
            if (semanticFrame) {
                val semanticDepth = alignedDepth.data
                semanticCopiedNanos = SystemClock.elapsedRealtimeNanos()
                semanticSegmenter?.submitRgb(
                    rgb, colorWidth, colorHeight, colorStride,
                    semanticDepth, alignedDepth.width, alignedDepth.height,
                    alignedDepth.stride, alignedDepth.units, intrinsic,
                    imageTime / 1000.0)
                if (semanticCaptureCount.incrementAndGet() % 10L == 0L) {
                    Log.i(TAG, String.format(
                        Locale.US, "SEMANTIC_CAPTURE align=%.1fms depth_copy=%.1fms frame=%d",
                        (alignedNanos - alignStartedNanos) / 1_000_000.0,
                        (semanticCopiedNanos - alignedNanos) / 1_000_000.0,
                        frameCount))
                }
            }
            if (navigationFrame) {
                val distances = analyzeDepth(alignedDepth)
                val semantic = latestSemanticResult
                updateDepthPreview(alignedDepth, rawDepth, frameCount)
                val shownFrameCount = frameCount
                // The camera thread is faster than the Android UI once the
                // local cost grid is visible. Keep only one UI callback so
                // stale frames cannot accumulate until the Java heap fails.
                if (uiUpdatePending.compareAndSet(false, true)) {
                    runOnUiThread {
                        try {
                            updateUi(distances, shownFrameCount, semantic)
                        } finally {
                            uiUpdatePending.set(false)
                        }
                    }
                }
            }
        } finally {
            rawDepthFrame?.close()
        }
    }

    private fun writeDiagnosticRgbOnce(rgb: ByteArray, width: Int, height: Int, stride: Int) {
        if (diagnosticRgbFrames.incrementAndGet() < 150L) return
        if (!diagnosticRgbWritten.compareAndSet(false, true)) return
        val output = File(context.filesDir, "diagnostic_rgb.ppm")
        try {
            FileOutputStream(output).use { stream ->
                stream.write(String.format(Locale.US, "P6\n%d %d\n255\n", width, height)
                    .toByteArray(StandardCharsets.US_ASCII))
                val rowBytes = width * 3
                for (y in 0 until height) {
                    stream.write(rgb, y * stride, rowBytes)
                }
            }
            Log.i(TAG, "DIAGNOSTIC_RGB path=${output.absolutePath} size=${width}x$height stride=$stride")
        } catch (error: Exception) {
            diagnosticRgbWritten.set(false)
            Log.w(TAG, "Unable to write diagnostic RGB frame", error)
        }
    }

    private fun initializeVins(pipelineProfile: PipelineProfile): Intrinsic {
        var colorProfile: StreamProfile? = null
        var gyroProfile: StreamProfile? = null
        val intrinsic: Intrinsic
        val cameraToImu: Extrinsic
        val device = pipelineProfile.device
        try {
            logCameraInfo(device, CameraInfo.NAME)
            logCameraInfo(device, CameraInfo.FIRMWARE_VERSION)
            logCameraInfo(device, CameraInfo.RECOMMENDED_FIRMWARE_VERSION)
            logCameraInfo(device, CameraInfo.USB_TYPE_DESCRIPTOR)
            logCameraInfo(device, CameraInfo.IMU_TYPE)
            for (sensor in device.querySensors()) {
                var colorSensor = false
                for (activeProfile in sensor.activeStreams) {
                    if (activeProfile.type == StreamType.COLOR) {
                        colorSensor = true
                        break
                    }
                }
                if (colorSensor) configureColorExposure(sensor)
                logSensorOption(sensor, Option.ENABLE_AUTO_EXPOSURE)
                logSensorOption(sensor, Option.EXPOSURE)
                logSensorOption(sensor, Option.GAIN)
                logSensorOption(sensor, Option.AUTO_EXPOSURE_PRIORITY)
                logSensorOption(sensor, Option.AUTO_EXPOSURE_LIMIT)
                logSensorOption(sensor, Option.OPTION_AUTO_EXPOSURE_LIMIT_TOGGLE)
                logSensorOption(sensor, Option.ENABLE_AUTO_WHITE_BALANCE)
                logSensorOption(sensor, Option.WHITE_BALANCE)
                for (profile in sensor.activeStreams) {
                    if (profile.type == StreamType.COLOR) colorProfile = profile
                    if (profile.type == StreamType.GYRO) gyroProfile = profile
                }
            }
            val resolvedColor = colorProfile
            val resolvedGyro = gyroProfile
            if (resolvedColor == null || resolvedGyro == null) {
                throw IllegalStateException("D455F color/gyro profiles unavailable for VINS")
            }
            val videoProfile = resolvedColor.`as`<VideoStreamProfile>(Extension.VIDEO_PROFILE)
            intrinsic = videoProfile.intrinsic
            cameraToImu = resolvedColor.getExtrinsicTo(resolvedGyro)
        } finally {
            device.close()
        }
        closeVins()
        val vins = VinsMono(intrinsic, cameraToImu)
        vinsMono = vins
        vinsInitialized = false
        latestVinsPoseNanos = 0L
        runOnUiThread { renderVinsStatus(vinsInput.status()) }
        return intrinsic
    }

    private fun logCameraInfo(device: Device, info: CameraInfo) {
        if (device.supportsInfo(info)) {
            Log.i(TAG, "D455F ${info.name}=${device.getInfo(info)}")
        }
    }

    private fun logSensorOption(sensor: Sensor, option: Option) {
        try {
            if (sensor.supports(option)) {
                Log.i(TAG, String.format(
                    Locale.US, "D455F option %s value=%.3f default=%.3f range=[%.3f,%.3f]",
                    option.name, sensor.getValue(option), sensor.getDefault(option),
                    sensor.getMinRange(option), sensor.getMaxRange(option)))
            }
        } catch (error: Exception) {
            Log.w(TAG, "Unable to query D455F option ${option.name}", error)
        }
    }

    private fun configureColorExposure(sensor: Sensor) {
        try {
            // Preserve RealSense auto exposure/gain, but prevent long indoor
            // exposures from smearing motion beyond the source 21x21 LK window.
            if (sensor.supports(Option.ENABLE_AUTO_EXPOSURE)) {
                sensor.setValue(Option.ENABLE_AUTO_EXPOSURE, 1f)
            }
            if (sensor.supports(Option.AUTO_EXPOSURE_PRIORITY)) {
                sensor.setValue(Option.AUTO_EXPOSURE_PRIORITY, 0f)
            }
            if (sensor.supports(Option.OPTION_AUTO_EXPOSURE_LIMIT_TOGGLE)) {
                sensor.setValue(Option.OPTION_AUTO_EXPOSURE_LIMIT_TOGGLE, 1f)
            }
            if (sensor.supports(Option.AUTO_EXPOSURE_LIMIT)) {
                val limit = max(sensor.getMinRange(Option.AUTO_EXPOSURE_LIMIT),
                    min(COLOR_AUTO_EXPOSURE_LIMIT_US, sensor.getMaxRange(Option.AUTO_EXPOSURE_LIMIT)))
                sensor.setValue(Option.AUTO_EXPOSURE_LIMIT, limit)
                Log.i(TAG, String.format(Locale.US, "D455F color auto-exposure limited to %.0f us", limit))
            } else {
                Log.w(TAG, "D455F color sensor does not support AUTO_EXPOSURE_LIMIT")
            }
        } catch (error: Exception) {
            Log.w(TAG, "Unable to configure D455F color exposure limit", error)
        }
    }

    private fun logColorFrameMetadata(colorFrame: Frame, frameCount: Long) {
        if (frameCount % COLOR_METADATA_LOG_INTERVAL != 0L) return
        try {
            val exposure = if (colorFrame.supportsMetadata(FrameMetadata.ACTUAL_EXPOSURE))
                colorFrame.getMetadata(FrameMetadata.ACTUAL_EXPOSURE) else -1L
            val gain = if (colorFrame.supportsMetadata(FrameMetadata.GAIN_LEVEL))
                colorFrame.getMetadata(FrameMetadata.GAIN_LEVEL) else -1L
            val fps = if (colorFrame.supportsMetadata(FrameMetadata.ACTUAL_FPS))
                colorFrame.getMetadata(FrameMetadata.ACTUAL_FPS) else -1L
            Log.i(TAG, "D455F_COLOR_FRAME exposure_us=$exposure gain=$gain actual_fps=$fps")
        } catch (error: Exception) {
            Log.w(TAG, "Unable to read D455F color frame metadata", error)
        }
    }

    // ---------------------------------------------------------------------
    // VINS
    // ---------------------------------------------------------------------

    private class VinsEstimateWork(val vins: VinsMono, val tracked: VinsMono.TrackedFrame)

    private fun processVinsMeasurements() {
        val local = vinsMono ?: return
        var image = vinsInput.pollReadyImage(local.currentTimeOffsetSeconds())
        while (image != null) {
            val tracked = local.track(image)
            if (local.consumeTrackerRestart()) {
                handleVinsRestart(local, true)
                break
            }
            if (tracked != null) {
                synchronized(vinsEstimateQueueLock) {
                    pendingVinsEstimates.addLast(VinsEstimateWork(local, tracked))
                }
                requestVinsEstimate()
            }
            image = vinsInput.pollReadyImage(local.currentTimeOffsetSeconds())
        }
    }

    private fun requestVinsEstimate() {
        if (vinsEstimatorExecutor.isShutdown || !vinsEstimatePending.compareAndSet(false, true)) return
        vinsEstimatorExecutor.execute {
            try {
                while (true) {
                    val work: VinsEstimateWork? = synchronized(vinsEstimateQueueLock) {
                        pendingVinsEstimates.pollFirst()
                    }
                    if (work == null) break
                    processVinsEstimate(work.vins, work.tracked)
                }
            } finally {
                vinsEstimatePending.set(false)
                synchronized(vinsEstimateQueueLock) {
                    if (pendingVinsEstimates.isNotEmpty()) requestVinsEstimate()
                }
            }
        }
    }

    private fun processVinsEstimate(local: VinsMono, tracked: VinsMono.TrackedFrame) {
        if (vinsMono !== local) return
        val pose = local.process(vinsInput, tracked)
        if (pose != null && vinsMono === local) {
            val wasInitialized = vinsInitialized
            if (pose.initialized) {
                consecutiveUninitializedPoses = 0
                vinsInitialized = true
            } else if (wasInitialized) {
                // The estimator can publish a transient non-initialized flag while
                // its sliding-window optimization catches up. Do not destroy the
                // VINS/map state on a single bad output; require a short run of
                // consecutive losses, as the source node does for a real restart.
                consecutiveUninitializedPoses++
            }
            if (wasInitialized && !pose.initialized && consecutiveUninitializedPoses >= 5) {
                consecutiveUninitializedPoses = 0
                vinsInitialized = false
                handleVinsRestart(local, false)
                return
            }
            if (!wasInitialized) vinsInitialized = pose.initialized
            latestVinsPose = pose
            latestVinsPoseNanos = SystemClock.elapsedRealtimeNanos()
            latestPoseCaptureMillis = FrameEvidence(
                pose.timestamp, 0L, System.currentTimeMillis(), SystemClock.elapsedRealtime()
            ).captureElapsedMillis
            calibrationVinsPoseHistory.add(pose)
            semanticSegmenter?.updateVinsPose(pose)
        }
    }

    private fun handleVinsRestart(local: VinsMono, clearInput: Boolean) {
        if (vinsMono !== local) return
        Log.w(TAG, "VINS_RESTART source=" +
            (if (clearInput) "tracker_timestamp_gap" else "estimator_pose_lost") +
            " clearInput=$clearInput")
        if (clearInput) vinsInput.clear()
        vinsResetCount++
        consecutiveUninitializedPoses = 0
        vinsInitialized = false
        latestVinsPose = null
        latestVinsPoseNanos = 0L
        resetVinsDependents()
    }

    private fun resetVinsDependents() {
        pendingCalibrationLocation = null
        latestPlanEvidence = null
        calibrationVinsPoseHistory.clear()
        dynamicHeadingCalibrator.resetForVinsRestart()
        latestSemanticResult = SemanticSegmenter.Result.waiting()
        latestSemanticResultNanos = 0L
        semanticSegmenter?.resetVinsState()
        runOnUiThread {
            resetLocalPlanning()
            renderDynamicHeadingCalibration()
            renderVinsStatus(vinsInput.status())
        }
    }

    private fun requestVinsProcessing() {
        if (vinsExecutor.isShutdown || !vinsDrainPending.compareAndSet(false, true)) return
        vinsExecutor.execute {
            try {
                processVinsMeasurements()
            } finally {
                vinsDrainPending.set(false)
                val local = vinsMono
                if (local != null && vinsInput.hasReadyImage(local.currentTimeOffsetSeconds())) {
                    requestVinsProcessing()
                }
            }
        }
    }

    private fun closeVins() {
        val local = vinsMono
        vinsMono = null
        latestVinsPose = null
        vinsInitialized = false
        consecutiveUninitializedPoses = 0
        latestVinsPoseNanos = 0L
        synchronized(vinsEstimateQueueLock) {
            pendingVinsEstimates.clear()
        }
        resetVinsDependents()
        local?.close()
    }

    private fun collectVinsImu(frame: Frame) {
        val profile = frame.profile
        try {
            val motion = frame.`as`<MotionFrame>(Extension.MOTION_FRAME)
            val value = motion.motionDataArray
            val domain: TimestampDomain = frame.timestampDomain
            val timestamp = vinsTimestampMapper.toSystemTimeMilliseconds(
                frame.timestamp, domain, System.currentTimeMillis().toDouble())
            if (profile.type == StreamType.GYRO) {
                vinsInput.addGyroscope(timestamp, value[0], value[1], value[2])
            } else if (profile.type == StreamType.ACCEL) {
                vinsInput.addAccelerometer(timestamp, value[0], value[1], value[2])
            }
        } finally {
            profile.close()
        }
    }

    // ---------------------------------------------------------------------
    // Depth preview / distance analysis
    // ---------------------------------------------------------------------

    private fun updateDepthPreview(alignedDepth: DepthImage, nativeDepth: DepthFrame?, frameCount: Long) {
        if (frameCount % PREVIEW_FRAME_INTERVAL != 0L || !previewUpdatePending.compareAndSet(false, true)) {
            return
        }

        try {
            val sourceWidth = alignedDepth.width
            val sourceHeight = alignedDepth.height
            val stride = alignedDepth.stride
            val previewWidth = sourceWidth / PREVIEW_DOWNSAMPLE
            val previewHeight = sourceHeight / PREVIEW_DOWNSAMPLE
            var units = alignedDepth.units
            val pixelCount = previewWidth * previewHeight
            if (previewDepthBuffer == null || previewDepthBuffer!!.size != alignedDepth.dataSize) {
                previewDepthBuffer = ByteArray(alignedDepth.dataSize)
            }
            if (previewPixelBuffer == null || previewPixelBuffer!!.size != pixelCount) {
                previewPixelBuffer = IntArray(pixelCount)
            }
            if (previewBitmap == null || previewBitmap!!.width != previewWidth || previewBitmap!!.height != previewHeight) {
                previewBitmap?.recycle()
                previewBitmap = Bitmap.createBitmap(previewWidth, previewHeight, Bitmap.Config.ARGB_8888)
            }
            var selectedDepth = previewDepthBuffer!!
            val pixels = previewPixelBuffer!!
            alignedDepth.getData(previewDepthBuffer)
            var selectedValid = DepthFrameStats.countValidSamples(
                previewDepthBuffer, sourceWidth, sourceHeight, stride, PREVIEW_DOWNSAMPLE)
            previewUsesNativeDepth = false

            if (nativeDepth != null && nativeDepth.width == sourceWidth &&
                nativeDepth.height == sourceHeight && nativeDepth.stride == stride) {
                if (previewAlternateDepthBuffer == null || previewAlternateDepthBuffer!!.size != nativeDepth.dataSize) {
                    previewAlternateDepthBuffer = ByteArray(nativeDepth.dataSize)
                }
                nativeDepth.getData(previewAlternateDepthBuffer)
                val nativeValid = DepthFrameStats.countValidSamples(
                    previewAlternateDepthBuffer, sourceWidth, sourceHeight, stride, PREVIEW_DOWNSAMPLE)
                if (nativeValid > selectedValid) {
                    selectedDepth = previewAlternateDepthBuffer!!
                    selectedValid = nativeValid
                    units = nativeDepth.units
                    previewUsesNativeDepth = true
                }
            }
            previewValidPercent = if (pixelCount == 0) Float.NaN else selectedValid * 100f / pixelCount

            for (y in 0 until previewHeight) {
                val sourceRow = y * PREVIEW_DOWNSAMPLE * stride
                val outputRow = y * previewWidth
                for (x in 0 until previewWidth) {
                    val sourceIndex = sourceRow + x * PREVIEW_DOWNSAMPLE * 2
                    val rawValue = (selectedDepth[sourceIndex].toInt() and 0xff) or
                        ((selectedDepth[sourceIndex + 1].toInt() and 0xff) shl 8)
                    if (rawValue == 0) {
                        pixels[outputRow + x] = Color.BLACK
                        continue
                    }
                    val meters = rawValue * units
                    var paletteIndex = Math.round(
                        (meters - VALID_MIN_METERS) * 255f / (PREVIEW_MAX_METERS - VALID_MIN_METERS))
                    paletteIndex = max(0, min(255, paletteIndex))
                    pixels[outputRow + x] = DEPTH_PALETTE[paletteIndex]
                }
            }

            previewBitmap!!.setPixels(pixels, 0, previewWidth, 0, 0, previewWidth, previewHeight)
            val bitmap = previewBitmap
            runOnUiThread {
                listener.onDepthPreview(bitmap)
                previewUpdatePending.set(false)
            }
        } catch (error: RuntimeException) {
            previewUpdatePending.set(false)
        }
    }

    private fun analyzeDepth(depth: DepthImage): SectorDistances {
        val width = depth.width
        val height = depth.height
        val yStart = height * 35 / 100
        val yEnd = height * 82 / 100

        val left = sectorDistance(depth, width * 5 / 100, width * 33 / 100, yStart, yEnd)
        val center = sectorDistance(depth, width * 36 / 100, width * 64 / 100, yStart, yEnd)
        val right = sectorDistance(depth, width * 67 / 100, width * 95 / 100, yStart, yEnd)

        return SectorDistances(left, center, right)
    }

    private fun sectorDistance(depth: DepthImage, xStart: Int, xEnd: Int, yStart: Int, yEnd: Int): Float {
        val capacity = max(1,
            ((xEnd - xStart) / SAMPLE_STEP_PIXELS + 1) * ((yEnd - yStart) / SAMPLE_STEP_PIXELS + 1))
        var values = FloatArray(capacity)
        var count = 0

        var y = yStart
        while (y < yEnd) {
            var x = xStart
            while (x < xEnd) {
                val distance = depth.getDistance(x, y)
                if (distance >= VALID_MIN_METERS && distance <= VALID_MAX_METERS) {
                    if (count == values.size) {
                        values = values.copyOf(values.size * 2)
                    }
                    values[count++] = distance
                }
                x += SAMPLE_STEP_PIXELS
            }
            y += SAMPLE_STEP_PIXELS
        }

        if (count < 8) {
            return Float.NaN
        }

        Arrays.sort(values, 0, count)
        val percentileIndex = min(count - 1, Math.round((count - 1) * 0.20f))
        return values[percentileIndex]
    }

    private fun updateUi(distances: SectorDistances, frameCount: Long, semantic: SemanticSegmenter.Result) {
        listener.onDistances(
            formatDistance("左侧", distances.left),
            formatDistance("前方", distances.center),
            formatDistance("右侧", distances.right))

        val frameStatus = StringBuilder(String.format(Locale.CHINA, "深度帧 %,d", frameCount))
        if (previewValidPercent.isFinite()) {
            frameStatus.append(String.format(
                Locale.CHINA, " · 深度有效 %.1f%%（%s）", previewValidPercent,
                if (previewUsesNativeDepth) "原始" else "彩色对齐"))
        }
        val vinsStatus = vinsInput.status()
        frameStatus.append(String.format(
            Locale.CHINA,
            " · VINS输入 图像 %,d / 陀螺仪 %,d / 加速度 %,d / 合并IMU %,d / 配对 %,d%s",
            vinsStatus.images, vinsStatus.gyroscope, vinsStatus.accelerometer,
            vinsStatus.unifiedImu, vinsStatus.pairedImages,
            if (vinsStatus.ready()) "（已就绪）" else "（等待数据）"))
        listener.onFrameStatus(frameStatus.toString())
        renderVinsStatus(vinsStatus)
        renderSemanticStatus(semantic)
        renderDirectionGuidance()
    }

    private fun renderDirectionGuidance() {
        var guidance: String
        var reason = ""
        val now = SystemClock.elapsedRealtime()
        val published = planObservation
        val cuePlan = published.plan
        val evidence = published.evidence
        val observationFresh = evidence != null && evidence.fresh(now)
        val poseFresh = latestVinsPose?.initialized == true &&
            now - latestPoseCaptureMillis >= 0L && now - latestPoseCaptureMillis < 1500L
        if (!navigationActive) {
            guidance = ""
        } else if (
            cuePlan.planned && observationFresh && poseFresh && gpsFresh() &&
            dynamicHeadingCalibrator.isReady()
        ) {
            if (cuePlan !== lastCuePlan) {
                lastPlanCue = navigationCueTracker.update(
                    true, cuePlan.success, cuePlan.blocked, cuePlan.steeringDegrees)
                lastCuePlan = cuePlan
            }
            guidance = lastPlanCue
            cueEvidence = evidence
            if (guidance == "停止") {
                reason = if (cuePlan.blocked) "纯跟踪前方受阻" else "A* 未找到安全路径"
            }
        } else {
            reason = when {
                !poseFresh -> "等待新鲜 VINS 位姿"
                !observationFresh -> "等待新鲜语义地图"
                !gpsFresh() -> "等待新鲜 GPS"
                !dynamicHeadingCalibrator.isReady() -> "等待地理方向对齐"
                else -> cuePlan.waitingReason.orEmpty()
            }
            guidance = if (cueEvidence?.fresh(now) == false) "停止" else ""
        }

        guidance = guidanceStabilizer.updateValidated(
            guidance, navigationActive, now, cueEvidence)
        if (!navigationActive) cueEvidence = null
        val auditNow = SystemClock.elapsedRealtimeNanos()
        if (navigationActive &&
            (guidance != lastAuditCue || reason != lastAuditReason ||
                auditNow - lastAuditNanos > TimeUnit.SECONDS.toNanos(1))
        ) {
            lastAuditCue = guidance
            lastAuditReason = reason
            lastAuditNanos = auditNow
            NavigationAudit.log(
                "CUE_AUDIT cue=$guidance reason=$reason frame=" +
                    (evidence?.cameraSeconds ?: "none") +
                    " frame_age_ms=" + (evidence?.ageMillis(now) ?: -1L) +
                    " raw=$lastPlanCue")
        }
        if (navigationActive && reason.isEmpty() && guidance == "停止" && lastPlanCue != "停止") {
            reason = "安全路径恢复确认中（1.5秒）"
        }
        if (navigationActive && reason.isNotEmpty()) {
            listener.onLocalPlanMetrics(
                (if (guidance == "停止") "安全停止：" else "等待：") + reason,
                if (guidance == "停止") GuidanceLevel.DANGER else GuidanceLevel.WARNING)
        }
        val level = when {
            guidance == "停止" -> GuidanceLevel.DANGER
            guidance == "直走" -> GuidanceLevel.SAFE
            guidance.isEmpty() -> GuidanceLevel.MUTED
            else -> GuidanceLevel.WARNING
        }
        val speechText = guidanceTextComposer.update(guidance, visionHintSnapshot, now)
        listener.onGuidanceChanged(speechText, level)
    }

    private fun computeLocalPlan(semantic: SemanticSegmenter.Result): LocalPlanner.PathResult {
        if (!navigationActive) return LocalPlanner.PathResult.waitingForTarget()
        if (!gpsFresh()) return LocalPlanner.PathResult.waiting("等待新鲜 GPS 定位")
        val evidence = semantic.evidence
        if (evidence == null || !evidence.fresh(SystemClock.elapsedRealtime())) {
            return LocalPlanner.PathResult.waiting("语义观测过期")
        }
        if (!routeFollower.hasRoute()) return LocalPlanner.PathResult.waiting("等待全局路线")
        val costGrid = semantic.localCostGrid
        if (costGrid == null || costGrid.size != MapTransform.WIDTH * MapTransform.HEIGHT) {
            return LocalPlanner.PathResult.waiting("等待语义点云/OctoMap局部代价图")
        }
        val grid = Array(MapTransform.HEIGHT) { IntArray(MapTransform.WIDTH) }
        for (row in 0 until MapTransform.HEIGHT) {
            System.arraycopy(costGrid, row * MapTransform.WIDTH, grid[row], 0, MapTransform.WIDTH)
        }
        val location = lastLocation ?: return LocalPlanner.PathResult.waiting("等待手机定位")
        val guidance = routeFollower.update(
            location.latitude,
            location.longitude,
            if (location.hasAccuracy()) location.accuracy else Float.NaN,
            Float.NaN,
            location.elapsedRealtimeNanos
        ) ?: return LocalPlanner.PathResult.waiting(routeFollower.waitingReason())
        val mapPose = semantic.mapPose
        if (mapPose == null || !mapPose.initialized) {
            return LocalPlanner.PathResult.waiting("等待与地图同步的 VINS 位姿")
        }
        if (!dynamicHeadingCalibrator.isReady()) {
            return LocalPlanner.PathResult.waiting("等待地理方向对齐")
        }
        val relativeTarget =
            dynamicHeadingCalibrator.relativeTargetDegrees(guidance.targetBearingDegrees, mapPose)
        if (!relativeTarget.isFinite()) return LocalPlanner.PathResult.waiting("等待有效 VINS 位姿")
        val radians = Math.toRadians(relativeTarget.toDouble())
        val targetRow = cos(radians).toFloat()
        val targetCol = sin(radians).toFloat()
        NavigationAudit.log(
            "TARGET_AUDIT frame=${evidence.cameraSeconds} pose=${mapPose.timestamp}" +
                " gps_ns=${location.elapsedRealtimeNanos} gps_to_frame_ms=" +
                (evidence.captureElapsedMillis - location.elapsedRealtimeNanos / 1_000_000L) +
                " geo=${guidance.targetBearingDegrees} camera_relative=$relativeTarget" +
                " pose_xy=${mapPose.x},${mapPose.y} map_yaw=${mapPose.egoRightAxisYawRadians()}" +
                " cross_track=${guidance.crossTrackMeters}" +
                " calibration=${dynamicHeadingCalibrator.status()}")
        return localPlanner.plan(
            grid, MapTransform.RESOLUTION, -7.9f, -7.9f,
            0f, 0f, targetCol, targetRow, mapPose)
    }

    private fun requestLocalPlanRefresh() {
        requestLocalPlanRefresh(null)
    }

    private fun requestLocalPlanRefresh(newMap: SemanticSegmenter.Result?) {
        val segmenter = semanticSegmenter
        if (!navigationActive || segmenter == null) {
            return
        }
        if (newMap != null) pendingLocalPlanMap.set(newMap)
        // Keep one pending refresh while A* is running. A newly completed semantic map
        // must replace the consumed request instead of being dropped until another map arrives.
        localPlanRefreshRequested.set(true)
        if (!localPlanPending.compareAndSet(false, true)) return
        localPlanExecutor.execute {
            try {
                while (navigationActive && localPlanRefreshRequested.getAndSet(false)) {
                    refreshLocalPlanOnce(segmenter)
                }
            } catch (error: Exception) {
                Log.e(TAG, "Local map reprojection/A* refresh failed", error)
            } finally {
                localPlanPending.set(false)
                // Close the race where a result arrives after the drain loop observes false
                // but before localPlanPending is cleared.
                if (navigationActive && localPlanRefreshRequested.get()) {
                    requestLocalPlanRefresh()
                }
            }
        }
    }

    private fun refreshLocalPlanOnce(segmenter: SemanticSegmenter) {
        val generation = localPlanGeneration.get()
        val refreshStartedNanos = SystemClock.elapsedRealtimeNanos()
        val requestedMap = pendingLocalPlanMap.getAndSet(null)
        val projected = requestedMap ?: segmenter.reprojectLatestLocalMap()
        if (generation != localPlanGeneration.get()) return
        if (projected == null) {
            val waiting = LocalPlanner.PathResult.waiting(segmenter.localMapWaitingReason())
            val sequence = localPlanSequence.incrementAndGet()
            planObservation = PlanObservation(waiting, latestPlanEvidence)
            latestLocalPlan = waiting
            recordLocalPlanMetrics(refreshStartedNanos, -1L)
            runOnUiThread {
                if (generation != localPlanGeneration.get() ||
                    sequence < latestRenderedLocalPlanSequence
                ) return@runOnUiThread
                latestRenderedLocalPlanSequence = sequence
                renderSemanticStatus(latestSemanticResult)
                renderLocalPlanMetrics()
                if (!hasValidLocalPlanDisplay) listener.onLocalPlan(waiting.toSnapshot())
            }
            return
        }
        val evidence = projected.evidence ?: return
        val planStartedNanos = SystemClock.elapsedRealtimeNanos()
        val plan = computeLocalPlan(projected)
        if (generation != localPlanGeneration.get()) return
        val planDurationNanos = SystemClock.elapsedRealtimeNanos() - planStartedNanos
        if (!evidence.fresh(SystemClock.elapsedRealtime())) {
            NavigationAudit.log(
                "PLAN_EXPIRED frame=${evidence.cameraSeconds}" +
                    " plan_ms=${planDurationNanos / 1e6}")
            return
        }
        val sequence = localPlanSequence.incrementAndGet()
        latestSemanticResult = projected
        planObservation = PlanObservation(plan, evidence)
        latestPlanEvidence = evidence
        latestLocalPlan = plan
        recordLocalPlanMetrics(refreshStartedNanos, planDurationNanos)
        if (sequence % 30L == 0L) {
            Log.i(
                TAG,
                String.format(
                    Locale.US,
                    "Local plan #%d: known=%d path=%d steering=%.1f blocked=%s",
                    sequence,
                    projected.costGridKnownCount,
                    plan.worldPath.size,
                    plan.steeringDegrees,
                    plan.blocked))
        }
        runOnUiThread {
            if (generation != localPlanGeneration.get() ||
                sequence != localPlanSequence.get() ||
                !evidence.fresh(SystemClock.elapsedRealtime())
            ) return@runOnUiThread
            latestRenderedLocalPlanSequence = sequence
            recordRenderedLocalPlanRefresh(evidence)
            renderSemanticStatus(projected)
            renderLocalPlanMetrics()
            listener.onLocalPlan(plan.toSnapshot(evidence))
            hasValidLocalPlanDisplay = true
            updateNavigationGuidance()
            renderDirectionGuidance()
            NavigationAudit.log(
                "PLAN_AUDIT frame=${evidence.cameraSeconds}" +
                    " plan_ms=${planDurationNanos / 1e6}" +
                    " ui_queue_ms=" +
                    ((SystemClock.elapsedRealtimeNanos() - planStartedNanos - planDurationNanos) / 1e6) +
                    " map_to_plan_ms=${(planStartedNanos - refreshStartedNanos) / 1e6}" +
                    " capture_to_display_ms=${evidence.ageMillis(SystemClock.elapsedRealtime())}" +
                    " planned=${plan.planned} success=${plan.success} blocked=${plan.blocked}")
        }
    }

    private fun recordLocalPlanMetrics(refreshStartedNanos: Long, planDurationNanos: Long) {
        latestLocalPlanDurationNanos = planDurationNanos
        val evidence = latestPlanEvidence
        val semanticNanos =
            if (evidence == null) 0L else evidence.captureElapsedMillis * 1_000_000L
        latestLocalPlanInputAgeNanos =
            if (semanticNanos > 0L) max(0L, refreshStartedNanos - semanticNanos) else -1L
    }

    private fun recordRenderedLocalPlanRefresh(evidence: FrameEvidence) {
        if (!renderedObservations.observe(evidence, SystemClock.elapsedRealtime())) return
        val renderedNanos = SystemClock.elapsedRealtimeNanos()
        val previousNanos = latestLocalPlanCompletedNanos
        latestLocalPlanCompletedNanos = renderedNanos
        latestLocalPlanRefreshNanos =
            if (previousNanos > 0L) renderedNanos - previousNanos else -1L
    }

    private fun renderSemanticStatus(semantic: SemanticSegmenter.Result) {
        if (semantic.inferenceMillis <= 0L) {
            return
        }
        if (!semantic.centerCost.isFinite()) {
            val text = if (semantic.semanticPointCount > 0 && semantic.octomapLeafCount > 0) {
                String.format(
                    Locale.CHINA,
                    "%s：中心类别 %s · 语义点云 %,d 点 · OctoMap %,d 叶节点 · 局部代价栅格 %,d/6400 格 · %s · %s · 推理 %.1f 秒",
                    BuildConfig.SEMANTIC_MODEL_NAME, semantic.label, semantic.semanticPointCount,
                    semantic.octomapLeafCount, semantic.costGridKnownCount,
                    if (semantic.groundHeight.isFinite())
                        String.format(Locale.CHINA, "地面修正 %,d 格", semantic.groundClearedCells)
                    else "未检出可靠地面",
                    semantic.backend, semantic.inferenceMillis / 1000f)
            } else {
                String.format(
                    Locale.CHINA,
                    "%s语义分割正常，中心类别 %s · %s · 推理 %.1f 秒；等待语义点云/OctoMap数据",
                    BuildConfig.SEMANTIC_MODEL_NAME, semantic.label, semantic.backend, semantic.inferenceMillis / 1000f)
            }
            listener.onSemanticOverlay(text, GuidanceLevel.MUTED)
            return
        }
        if (semantic.isNotWalkable) {
            val reason = if (semantic.obstacleDistance.isFinite())
                String.format(Locale.CHINA, "深度距离 %.1f 米", semantic.obstacleDistance)
            else "局部代价超过 50"
            val text = String.format(
                Locale.CHINA, "%s：前方%s，类别 %s · 左 %.0f / 前 %.0f / 右 %.0f · %s · 推理 %.1f 秒",
                BuildConfig.SEMANTIC_MODEL_NAME, reason, semantic.label, semantic.leftCost * 100f,
                semantic.centerCost * 100f, semantic.rightCost * 100f, semantic.backend, semantic.inferenceMillis / 1000f)
            listener.onSemanticOverlay(text, GuidanceLevel.WARNING)
        } else {
            val text = String.format(
                Locale.CHINA,
                "%s局部代价：左 %.0f / 前 %.0f / 右 %.0f，前方无不可通行栅格 · %s · 推理 %.1f 秒",
                BuildConfig.SEMANTIC_MODEL_NAME, semantic.leftCost * 100f, semantic.centerCost * 100f,
                semantic.rightCost * 100f, semantic.backend, semantic.inferenceMillis / 1000f)
            listener.onSemanticOverlay(text, GuidanceLevel.MUTED)
        }
    }

    private fun renderVinsStatus(status: VinsInputBuffer.Status) {
        val pose = latestVinsPose
        val text: String
        val level: GuidanceLevel
        if (vinsMono == null) {
            text = "VINS：等待深度相机\n尚未启动图像和 IMU 处理"
            level = GuidanceLevel.MUTED
        } else if (pose != null && pose.initialized) {
            val ageSeconds = if (latestVinsPoseNanos > 0L)
                (SystemClock.elapsedRealtimeNanos() - latestVinsPoseNanos) / 1_000_000_000f
            else 0f
            text = String.format(
                Locale.CHINA, "VINS：初始化成功\n位姿年龄 %.1f 秒 · 已配对 %,d 帧%s",
                max(0f, ageSeconds), status.pairedImages,
                if (status.droppedImages > 0) String.format(Locale.CHINA, " · 输入丢弃 %,d", status.droppedImages) else "")
            level = GuidanceLevel.SAFE
        } else if (status.ready()) {
            text = if (vinsResetCount > 0)
                String.format(Locale.CHINA, "VINS：已重置 %d 次，重新初始化中\n图像和 IMU 已同步，请缓慢平移并转动", vinsResetCount)
            else "VINS：初始化中\n图像和 IMU 已同步，请缓慢平移并转动"
            level = GuidanceLevel.WARNING
        } else {
            text = String.format(Locale.CHINA, "VINS：等待同步输入\n图像 %,d · 合并 IMU %,d", status.images, status.unifiedImu)
            level = GuidanceLevel.MUTED
        }
        listener.onVinsStatus(text, level)
    }

    private fun renderLocalPlanMetrics() {
        if (!navigationActive) {
            listener.onLocalPlanMetrics("A*诊断：等待开始导航", GuidanceLevel.MUTED)
            return
        }
        val duration = if (latestLocalPlanDurationNanos >= 0L)
            String.format(Locale.CHINA, "%.1f ms", latestLocalPlanDurationNanos / 1_000_000f) else "--"
        val refresh = if (latestLocalPlanRefreshNanos >= 0L)
            String.format(Locale.CHINA, "%.0f ms", latestLocalPlanRefreshNanos / 1_000_000f) else "--"
        val inputAge = if (latestLocalPlanInputAgeNanos >= 0L)
            String.format(Locale.CHINA, "%.1f 秒", latestLocalPlanInputAgeNanos / 1_000_000_000f) else "--"
        val text = String.format(
            Locale.CHINA, "A*单次 %s · 刷新间隔 %s\n代价图输入年龄 %s · 更新 #%,d",
            duration, refresh, inputAge, latestRenderedLocalPlanSequence)
        listener.onLocalPlanMetrics(text, if (latestLocalPlanDurationNanos >= 0L) GuidanceLevel.SAFE else GuidanceLevel.WARNING)
    }

    private fun resetLocalPlanning() {
        localPlanGeneration.incrementAndGet()
        localPlanRefreshRequested.set(false)
        pendingLocalPlanMap.set(null)
        localPlanner.clearTargetPath()
        latestLocalPlan = LocalPlanner.PathResult.waitingForTarget()
        planObservation = PlanObservation(latestLocalPlan, null)
        latestPlanEvidence = null
        cueEvidence = null
        latestRenderedLocalPlanSequence = 0L
        latestLocalPlanDurationNanos = -1L
        latestLocalPlanRefreshNanos = -1L
        latestLocalPlanCompletedNanos = 0L
        latestLocalPlanInputAgeNanos = -1L
        hasValidLocalPlanDisplay = false
        renderedObservations.reset()
        navigationCueTracker.reset()
        lastCuePlan = null
        lastPlanCue = ""
        listener.onLocalPlan(latestLocalPlan.toSnapshot())
        renderLocalPlanMetrics()
    }

    private fun formatDistance(label: String, distance: Float): String {
        if (!distance.isFinite()) {
            return "$label\n-- m"
        }
        return String.format(Locale.CHINA, "%s\n%.2f m", label, distance)
    }

    private fun isCloserThan(distance: Float, threshold: Float): Boolean =
        distance.isFinite() && distance < threshold

    private fun clearance(distance: Float): Float =
        if (distance.isFinite()) distance else VALID_MAX_METERS

    private fun LocalPlanner.PathResult.toSnapshot(
        evidence: FrameEvidence? = null
    ): LocalPlanSnapshot = LocalPlanSnapshot(
        worldPath, planned, success, steeringDegrees, blocked,
        startCost, targetCost, obstacleCount, visualizationGrid, waitingReason,
        evidence?.cameraSeconds, evidence?.captureElapsedMillis ?: -1L,
        evidence?.generation ?: -1L)

    private class SectorDistances(val left: Float, val center: Float, val right: Float)
}
