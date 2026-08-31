package com.elabrador.mobilenavigation;

import android.Manifest;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.hardware.GeomagneticField;
import android.location.Location;
import android.location.LocationManager;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.Process;
import android.os.SystemClock;
import android.text.Editable;
import android.text.TextWatcher;
import android.util.Log;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;

import androidx.appcompat.app.AppCompatActivity;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;

import com.intel.realsense.librealsense.DepthFrame;
import com.intel.realsense.librealsense.Align;
import com.intel.realsense.librealsense.Config;
import com.intel.realsense.librealsense.CameraInfo;
import com.intel.realsense.librealsense.DeviceListener;
import com.intel.realsense.librealsense.Device;
import com.intel.realsense.librealsense.DeviceList;
import com.intel.realsense.librealsense.Extension;
import com.intel.realsense.librealsense.Extrinsic;
import com.intel.realsense.librealsense.Frame;
import com.intel.realsense.librealsense.FrameCallback;
import com.intel.realsense.librealsense.FrameMetadata;
import com.intel.realsense.librealsense.FrameSet;
import com.intel.realsense.librealsense.Pipeline;
import com.intel.realsense.librealsense.PipelineProfile;
import com.intel.realsense.librealsense.RsContext;
import com.intel.realsense.librealsense.StreamType;
import com.intel.realsense.librealsense.StreamFormat;
import com.intel.realsense.librealsense.VideoFrame;
import com.intel.realsense.librealsense.VideoStreamProfile;
import com.intel.realsense.librealsense.Intrinsic;
import com.intel.realsense.librealsense.MotionFrame;
import com.intel.realsense.librealsense.Option;
import com.intel.realsense.librealsense.StreamProfile;
import com.intel.realsense.librealsense.Sensor;
import com.intel.realsense.librealsense.TimestampDomain;

import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayDeque;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;

public class MainActivity extends AppCompatActivity {
    private static final String TAG = "MobileNavigation";
    private static final int CAMERA_PERMISSION_REQUEST = 10;
    private static final int LOCATION_PERMISSION_REQUEST = 11;
    private static final float VALID_MIN_METERS = 0.25f;
    private static final float VALID_MAX_METERS = 6.0f;
    private static final float OBSTACLE_METERS = 1.2f;
    private static final float STOP_METERS = 0.65f;
    private static final int SAMPLE_STEP_PIXELS = 12;
    private static final int PREVIEW_DOWNSAMPLE = 2;
    private static final int VIDEO_WIDTH = 640;
    private static final int VIDEO_HEIGHT = 480;
    private static final int VIDEO_FPS = 30;
    private static final int NAVIGATION_FRAME_INTERVAL = 3;
    private static final int PREVIEW_FRAME_INTERVAL = 6;
    private static final float PREVIEW_MAX_METERS = 4.0f;
    private static final int COLOR_WIDTH = VIDEO_WIDTH;
    private static final int COLOR_HEIGHT = VIDEO_HEIGHT;
    private static final int COLOR_FRAME_INTERVAL = VIDEO_FPS;
    private static final float COLOR_AUTO_EXPOSURE_LIMIT_US = 16_000f;
    private static final int COLOR_METADATA_LOG_INTERVAL = 30;
    // One second of source frames prevents UI/GC pauses from becoming VINS image
    // timestamp discontinuities. The upstream ROS subscribers use deeper queues.
    private static final int VIDEO_FRAME_QUEUE_CAPACITY = VIDEO_FPS + 2;
    private static final int[] DEPTH_PALETTE = createDepthPalette();

    private final AtomicBoolean streaming = new AtomicBoolean(false);
    private final AtomicBoolean diagnosticRgbWritten = new AtomicBoolean(false);
    private final AtomicLong diagnosticRgbFrames = new AtomicLong();
    private final AtomicBoolean previewUpdatePending = new AtomicBoolean(false);
    private final AtomicBoolean uiUpdatePending = new AtomicBoolean(false);
    private byte[] previewDepthBuffer;
    private byte[] previewAlternateDepthBuffer;
    private int[] previewPixelBuffer;
    private Bitmap previewBitmap;
    private volatile float previewValidPercent = Float.NaN;
    private volatile boolean previewUsesNativeDepth;
    private RsContext rsContext;
    private Thread streamingThread;
    private boolean activityResumed;
    private boolean restartStreamingWhenStopped;
    private PhonePoseTracker phonePoseTracker;
    private AmapRouteClient amapRouteClient;
    private SemanticSegmenter semanticSegmenter;
    private final LocalPlanner localPlanner = new LocalPlanner();
    private final VinsInputBuffer vinsInput = new VinsInputBuffer();
    private final RealSenseTimestampMapper vinsTimestampMapper =
            new RealSenseTimestampMapper();
    private final VinsPoseHistory calibrationVinsPoseHistory = new VinsPoseHistory();
    private volatile VinsMono vinsMono;
    private volatile VinsMono.Pose latestVinsPose;
    private volatile LocalPlanner.PathResult latestLocalPlan =
            LocalPlanner.PathResult.waitingForTarget();
    private final RouteFollower routeFollower = new RouteFollower();
    private final DynamicHeadingCalibrator dynamicHeadingCalibrator =
            new DynamicHeadingCalibrator();
    private AmapRouteClient.RouteResult currentRoute;
    private volatile boolean navigationActive;
    private volatile Location lastLocation;
    private volatile float currentHeading = Float.NaN;
    private volatile long latestHeadingNanos;
    private final Handler searchHandler = new Handler(Looper.getMainLooper());
    private final Handler localPlanHandler = new Handler(Looper.getMainLooper());
    private final ExecutorService vinsExecutor = newVinsExecutor("vins-feature-tracker");
    private final ExecutorService vinsEstimatorExecutor = newVinsExecutor("vins-estimator");
    private final AtomicBoolean vinsDrainPending = new AtomicBoolean(false);
    // Match the source estimator_node FIFO: every feature message must be paired
    // with the IMU interval ending at that image timestamp, in timestamp order.
    private final Object vinsEstimateQueueLock = new Object();
    private final ArrayDeque<VinsEstimateWork> pendingVinsEstimates = new ArrayDeque<>();
    private final AtomicBoolean vinsEstimatePending = new AtomicBoolean(false);
    private final ExecutorService localPlanExecutor = Executors.newSingleThreadExecutor();
    private final AtomicBoolean localPlanPending = new AtomicBoolean(false);
    private final AtomicLong localPlanSequence = new AtomicLong();
    private final AtomicLong localPlanGeneration = new AtomicLong();
    private volatile long latestRenderedLocalPlanSequence;
    private volatile long latestSemanticResultNanos;
    private volatile long latestLocalPlanDurationNanos = -1L;
    private volatile long latestLocalPlanRefreshNanos = -1L;
    private volatile long latestLocalPlanCompletedNanos;
    private volatile long latestLocalPlanInputAgeNanos = -1L;
    private volatile boolean vinsInitialized;
    private volatile int vinsResetCount;
    private int consecutiveUninitializedPoses;
    private volatile long latestVinsPoseNanos;
    private Runnable pendingDestinationSearch;
    private AmapRouteClient.PlaceSuggestion selectedDestination;
    private boolean applyingSuggestion;
    private volatile SemanticSegmenter.Result latestSemanticResult =
            SemanticSegmenter.Result.waiting();

    private TextView cameraStatusText;
    private TextView vinsStatusPanel;
    private ImageView depthPreview;
    private TextView guidanceText;
    private TextView leftDistanceText;
    private TextView centerDistanceText;
    private TextView rightDistanceText;
    private TextView frameStatusText;
    private TextView semanticStatusText;
    private TextView headingText;
    private TextView locationText;
    private TextView locationStatusText;
    private TextView calibrationStatusText;
    private EditText amapKeyInput;
    private EditText destinationInput;
    private Button planRouteButton;
    private Button calibrateHeadingButton;
    private Button calibrateAlignedButton;
    private Button toggleNavigationButton;
    private TextView routeStatusText;
    private TextView navigationStatusText;
    private TextView suggestionStatusText;
    private LinearLayout destinationSuggestions;
    private LocalPlanView localPlanView;
    private TextView localPlanMetricsText;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        cameraStatusText = findViewById(R.id.cameraStatusText);
        vinsStatusPanel = findViewById(R.id.vinsStatusPanel);
        depthPreview = findViewById(R.id.depthPreview);
        guidanceText = findViewById(R.id.guidanceText);
        leftDistanceText = findViewById(R.id.leftDistanceText);
        centerDistanceText = findViewById(R.id.centerDistanceText);
        rightDistanceText = findViewById(R.id.rightDistanceText);
        frameStatusText = findViewById(R.id.frameStatusText);
        semanticStatusText = findViewById(R.id.semanticStatusText);
        headingText = findViewById(R.id.headingText);
        locationText = findViewById(R.id.locationText);
        locationStatusText = findViewById(R.id.locationStatusText);
        calibrationStatusText = findViewById(R.id.calibrationStatusText);
        amapKeyInput = findViewById(R.id.amapKeyInput);
        destinationInput = findViewById(R.id.destinationInput);
        planRouteButton = findViewById(R.id.planRouteButton);
        calibrateHeadingButton = findViewById(R.id.calibrateHeadingButton);
        calibrateAlignedButton = findViewById(R.id.calibrateAlignedButton);
        toggleNavigationButton = findViewById(R.id.toggleNavigationButton);
        routeStatusText = findViewById(R.id.routeStatusText);
        navigationStatusText = findViewById(R.id.navigationStatusText);
        suggestionStatusText = findViewById(R.id.suggestionStatusText);
        destinationSuggestions = findViewById(R.id.destinationSuggestions);
        localPlanView = findViewById(R.id.localPlanView);
        localPlanMetricsText = findViewById(R.id.localPlanMetricsText);
        amapRouteClient = new AmapRouteClient();
        semanticSegmenter = new SemanticSegmenter(getApplicationContext(),
                new SemanticSegmenter.Listener() {
                    @Override
                    public void onStatus(String status) {
                        runOnUiThread(() -> semanticStatusText.setText(status));
                    }

                    @Override
                    public void onResult(SemanticSegmenter.Result result) {
                        latestSemanticResult = result;
                        latestSemanticResultNanos = SystemClock.elapsedRealtimeNanos();
                        runOnUiThread(() -> {
                            renderSemanticStatus(result);
                            requestLocalPlanRefresh();
                        });
                    }

                    @Override
                    public void onError(String message) {
                        runOnUiThread(() -> {
                            semanticStatusText.setText("Mask2Former 错误：" + message);
                            semanticStatusText.setTextColor(
                                    ContextCompat.getColor(MainActivity.this, R.color.nav_danger));
                        });
                    }
                });
        semanticSegmenter.initialize();
        amapKeyInput.setText(getPreferences(MODE_PRIVATE).getString("amap_web_key", ""));
        planRouteButton.setOnClickListener(this::planWalkingRoute);
        calibrateHeadingButton.setOnClickListener(this::startDynamicHeadingCalibration);
        calibrateAlignedButton.setOnClickListener(this::calibrateAlignedHeading);
        toggleNavigationButton.setOnClickListener(this::toggleNavigation);
        destinationInput.addTextChangedListener(new TextWatcher() {
            @Override
            public void beforeTextChanged(CharSequence text, int start, int count, int after) {
            }

            @Override
            public void onTextChanged(CharSequence text, int start, int before, int count) {
                if (!applyingSuggestion) {
                    selectedDestination = null;
                    scheduleDestinationSearch(text.toString().trim());
                }
            }

            @Override
            public void afterTextChanged(Editable text) {
            }
        });

        phonePoseTracker = new PhonePoseTracker(this, new PhonePoseTracker.Listener() {
            @Override
            public void onHeading(float headingDegrees) {
                currentHeading = headingDegrees;
                latestHeadingNanos = SystemClock.elapsedRealtimeNanos();
                headingText.setText(String.format(
                        Locale.CHINA, "朝向\n%.0f° %s", headingDegrees, cardinalDirection(headingDegrees)));
                updateNavigationGuidance();
            }

            @Override
            public void onLocation(Location location) {
                lastLocation = location;
                locationText.setText(String.format(
                        Locale.CHINA,
                        "位置\n%.6f, %.6f\n精度 %.0f m",
                        location.getLatitude(),
                        location.getLongitude(),
                        location.hasAccuracy() ? location.getAccuracy() : 0f));
                updateDynamicHeadingCalibration(location);
                updateNavigationGuidance();
            }

            @Override
            public void onLocationStatus(String status) {
                locationStatusText.setText(status);
            }
        });

        RsContext.init(getApplicationContext());
        rsContext = new RsContext();
        rsContext.setDevicesChangedCallback(new DeviceListener() {
            @Override
            public void onDeviceAttach() {
                runOnUiThread(() -> cameraStatusText.setText("深度相机已连接"));
                startStreaming();
            }

            @Override
            public void onDeviceDetach() {
                stopStreaming();
                runOnUiThread(() -> {
                    if (depthPreview != null) {
                        depthPreview.setImageBitmap(null);
                    }
                    cameraStatusText.setText("深度相机已断开");
                    guidanceText.setText("--");
                    renderVinsStatus(vinsInput.status());
                });
            }
        });

        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
                == PackageManager.PERMISSION_GRANTED) {
            requestLocationPermissionIfNeeded();
        } else {
            requestCameraPermissionIfNeeded();
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        synchronized (this) {
            activityResumed = true;
        }
        if (rsContext != null) {
            try (DeviceList devices = rsContext.queryDevices()) {
                if (devices.getDeviceCount() > 0) {
                    startStreaming();
                }
            } catch (Exception error) {
                cameraStatusText.setText("相机检测失败: " + error.getMessage());
            }
        }
        if (phonePoseTracker != null) {
            phonePoseTracker.start();
        }
    }

    @Override
    protected void onPause() {
        synchronized (this) {
            activityResumed = false;
        }
        stopStreaming();
        if (phonePoseTracker != null) {
            phonePoseTracker.stop();
        }
        if (depthPreview != null) {
            depthPreview.setImageBitmap(null);
        }
        localPlanHandler.removeCallbacksAndMessages(null);
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        stopStreaming();
        if (rsContext != null) {
            rsContext.close();
            rsContext = null;
        }
        if (amapRouteClient != null) {
            amapRouteClient.close();
            amapRouteClient = null;
        }
        if (semanticSegmenter != null) {
            semanticSegmenter.close();
            semanticSegmenter = null;
        }
        searchHandler.removeCallbacksAndMessages(null);
        localPlanHandler.removeCallbacksAndMessages(null);
        vinsExecutor.shutdownNow();
        vinsEstimatorExecutor.shutdownNow();
        localPlanExecutor.shutdownNow();
        depthPreview = null;
        phonePoseTracker = null;
        super.onDestroy();
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == CAMERA_PERMISSION_REQUEST
                && grantResults.length > 0
                && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
            startStreaming();
            requestLocationPermissionIfNeeded();
        } else if (requestCode == LOCATION_PERMISSION_REQUEST
                && grantResults.length > 0
                && phonePoseTracker != null) {
            phonePoseTracker.start();
        }
    }

    private void requestCameraPermissionIfNeeded() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
                != PackageManager.PERMISSION_GRANTED) {
            ActivityCompat.requestPermissions(
                    this,
                    new String[]{Manifest.permission.CAMERA},
                    CAMERA_PERMISSION_REQUEST
            );
        }
    }

    private void requestLocationPermissionIfNeeded() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION)
                != PackageManager.PERMISSION_GRANTED) {
            ActivityCompat.requestPermissions(
                    this,
                    new String[]{
                            Manifest.permission.ACCESS_FINE_LOCATION,
                            Manifest.permission.ACCESS_COARSE_LOCATION
                    },
                    LOCATION_PERMISSION_REQUEST
            );
        }
    }

    private String cardinalDirection(float headingDegrees) {
        String[] directions = {"北", "东北", "东", "东南", "南", "西南", "西", "西北"};
        int index = Math.round(headingDegrees / 45f) % directions.length;
        return directions[index];
    }

    private void planWalkingRoute(View ignored) {
        String key = amapKeyInput.getText().toString().trim();
        String destination = destinationInput.getText().toString().trim();
        if (key.isEmpty()) {
            routeStatusText.setText("请输入高德 Web 服务 Key");
            return;
        }
        if (destination.isEmpty()) {
            routeStatusText.setText("请输入目的地名称或完整地址");
            return;
        }
        if (lastLocation == null) {
            routeStatusText.setText("尚未获得手机定位，请到室外等待定位");
            return;
        }

        getPreferences(MODE_PRIVATE).edit().putString("amap_web_key", key).apply();
        stopNavigationAndClearRoute();
        planRouteButton.setEnabled(false);
        routeStatusText.setText("正在查询目的地和步行路线…");
        AmapRouteClient.Callback callback = new AmapRouteClient.Callback() {
            @Override
            public void onSuccess(AmapRouteClient.RouteResult result) {
                runOnUiThread(() -> {
                    planRouteButton.setEnabled(true);
                    currentRoute = result;
                    routeFollower.setRoute(result);
                    resetLocalPlanning();
                    float relativeTurn = Float.isFinite(currentHeading)
                            ? shortestTurn(currentHeading, result.initialBearingDegrees)
                            : Float.NaN;
                    String turnText = Float.isFinite(relativeTurn)
                            ? String.format(Locale.CHINA, "\n相对转向 %+.0f°", relativeTurn)
                            : "";
                    routeStatusText.setText(String.format(
                            Locale.CHINA,
                            "%s\n步行 %.2f km，约 %d 分钟\n初始方位 %.0f°%s\n%s",
                            result.destinationName,
                            result.distanceMeters / 1000f,
                            Math.max(1, Math.round(result.durationSeconds / 60f)),
                            result.initialBearingDegrees,
                            turnText,
                            result.firstInstruction));
                    toggleNavigationButton.setText("开始导航");
                    toggleNavigationButton.setEnabled(true);
                    toggleNavigationButton.setVisibility(View.VISIBLE);
                    navigationStatusText.setText("路线已就绪，点击“开始导航”后实时跟随位置");
                    navigationStatusText.setVisibility(View.VISIBLE);
                });
            }

            @Override
            public void onError(String message) {
                runOnUiThread(() -> {
                    planRouteButton.setEnabled(true);
                    routeStatusText.setText(message);
                });
            }
        };
        if (selectedDestination != null) {
            amapRouteClient.planWalkingRoute(key, lastLocation, selectedDestination, callback);
        } else {
            amapRouteClient.planWalkingRoute(key, lastLocation, destination, callback);
        }
    }

    private void toggleNavigation(View ignored) {
        if (navigationActive) {
            navigationActive = false;
            resetLocalPlanning();
            toggleNavigationButton.setText("继续导航");
            navigationStatusText.setText("导航已暂停，路线仍然保留");
            return;
        }
        if (currentRoute == null || !routeFollower.hasRoute()) {
            navigationStatusText.setText("请先规划一条步行路线");
            navigationStatusText.setVisibility(View.VISIBLE);
            return;
        }
        if (lastLocation == null) {
            navigationStatusText.setText("等待手机定位后才能开始导航");
            navigationStatusText.setVisibility(View.VISIBLE);
            return;
        }
        navigationActive = true;
        requestLocalPlanRefresh();
        toggleNavigationButton.setText("结束导航");
        updateNavigationGuidance();
    }

    private void stopNavigationAndClearRoute() {
        navigationActive = false;
        resetLocalPlanning();
        currentRoute = null;
        routeFollower.clear();
        toggleNavigationButton.setVisibility(View.GONE);
        navigationStatusText.setVisibility(View.GONE);
    }

    private void updateNavigationGuidance() {
        if (!navigationActive || lastLocation == null || !routeFollower.hasRoute()) {
            return;
        }
        RouteFollower.Guidance guidance = routeFollower.update(
                lastLocation.getLatitude(),
                lastLocation.getLongitude(),
                lastLocation.hasAccuracy() ? lastLocation.getAccuracy() : 0f,
                Float.NaN);
        if (guidance == null) {
            return;
        }

        VinsMono.Pose pose = latestVinsPose;
        float cameraRelativeTarget = dynamicHeadingCalibrator.relativeTargetDegrees(
                guidance.targetBearingDegrees, pose);
        String cameraTarget = Float.isFinite(cameraRelativeTarget)
                ? String.format(Locale.CHINA, "D455 局部目标 %+.0f°", cameraRelativeTarget)
                : dynamicHeadingCalibrator.status();
        String deviation = guidance.offRoute
                ? String.format(Locale.CHINA, "\n偏离路线约 %d 米", guidance.crossTrackMeters)
                : "";
        String semanticWarning = latestSemanticResult.isNotWalkable
                ? String.format(Locale.CHINA, "\nMask2Former：%s（%.0f%%），不可通行",
                latestSemanticResult.label,
                latestSemanticResult.areaRatio * 100f)
                : "";
        navigationStatusText.setText(String.format(
                Locale.CHINA,
                "%s\n剩余 %s · 距下一步 %s\n目标方位 %.0f° · %s\n%s%s%s",
                guidance.action,
                formatNavigationDistance(guidance.remainingMeters),
                formatNavigationDistance(guidance.distanceToInstructionMeters),
                guidance.targetBearingDegrees,
                cameraTarget,
                guidance.instruction,
                deviation,
                semanticWarning));
        navigationStatusText.setVisibility(View.VISIBLE);

        if (guidance.arrived) {
            navigationActive = false;
            resetLocalPlanning();
            toggleNavigationButton.setText("导航完成");
            toggleNavigationButton.setEnabled(false);
        }
    }

    private String formatNavigationDistance(int meters) {
        if (meters < 1000) {
            return String.format(Locale.CHINA, "%d 米", meters);
        }
        return String.format(Locale.CHINA, "%.1f 公里", meters / 1000f);
    }

    private void startDynamicHeadingCalibration(View ignored) {
        dynamicHeadingCalibrator.start();
        if (lastLocation != null) updateDynamicHeadingCalibration(lastLocation);
        renderDynamicHeadingCalibration();
        resetLocalPlanning();
        requestLocalPlanRefresh();
    }

    private void calibrateAlignedHeading(View ignored) {
        float trueHeading = currentHeading;
        long nowNanos = SystemClock.elapsedRealtimeNanos();
        if (latestHeadingNanos == 0L
                || nowNanos - latestHeadingNanos > TimeUnit.SECONDS.toNanos(2)) {
            trueHeading = Float.NaN;
        }
        VinsMono.Pose pose = latestVinsPose;
        if (latestVinsPoseNanos == 0L
                || nowNanos - latestVinsPoseNanos > TimeUnit.SECONDS.toNanos(2)) {
            pose = null;
        }
        Location location = lastLocation;
        if (Float.isFinite(trueHeading) && location != null) {
            GeomagneticField magneticField = new GeomagneticField(
                    (float) location.getLatitude(), (float) location.getLongitude(),
                    location.hasAltitude() ? (float) location.getAltitude() : 0f,
                    System.currentTimeMillis());
            trueHeading = (float) DynamicHeadingCalibrator.normalizeDegrees(
                    trueHeading + magneticField.getDeclination());
        }
        dynamicHeadingCalibrator.calibrateAligned(trueHeading, pose);
        renderDynamicHeadingCalibration();
        resetLocalPlanning();
        requestLocalPlanRefresh();
    }

    private void updateDynamicHeadingCalibration(Location location) {
        if (!LocationManager.GPS_PROVIDER.equals(location.getProvider())) return;
        long ageMillis = Math.max(0L, System.currentTimeMillis() - location.getTime());
        if (ageMillis > 5_000L) {
            dynamicHeadingCalibrator.waitForFreshGps();
            renderDynamicHeadingCalibration();
            return;
        }
        VinsMono.Pose pose = calibrationVinsPoseHistory.atOrNearest(
                location.getTime() / 1000.0, 0.75);
        if (pose == null) {
            dynamicHeadingCalibrator.waitForTimeAlignedVinsPose();
            renderDynamicHeadingCalibration();
            return;
        }
        dynamicHeadingCalibrator.update(
                location.getLatitude(), location.getLongitude(),
                location.hasAccuracy() ? location.getAccuracy() : Float.POSITIVE_INFINITY,
                pose.x, pose.y, pose.initialized);
        renderDynamicHeadingCalibration();
    }

    private void renderDynamicHeadingCalibration() {
        if (calibrationStatusText == null || calibrateHeadingButton == null
                || calibrateAlignedButton == null) return;
        boolean ready = dynamicHeadingCalibrator.isReady();
        calibrationStatusText.setText(dynamicHeadingCalibrator.status());
        calibrationStatusText.setTextColor(ContextCompat.getColor(this,
                ready ? R.color.nav_safe : R.color.nav_muted));
        calibrateHeadingButton.setText(ready ? "重新动态方向标定" : "开始动态方向标定");
    }

    private void scheduleDestinationSearch(String keyword) {
        if (pendingDestinationSearch != null) {
            searchHandler.removeCallbacks(pendingDestinationSearch);
        }
        destinationSuggestions.removeAllViews();
        if (keyword.length() < 2) {
            suggestionStatusText.setVisibility(View.GONE);
            destinationSuggestions.setVisibility(View.GONE);
            return;
        }

        pendingDestinationSearch = () -> searchDestinationSuggestions(keyword);
        searchHandler.postDelayed(pendingDestinationSearch, 500L);
    }

    private void searchDestinationSuggestions(String keyword) {
        String key = amapKeyInput.getText().toString().trim();
        if (key.isEmpty()) {
            showSuggestionStatus("输入高德 Key 后显示附近地点");
            return;
        }
        if (lastLocation == null) {
            showSuggestionStatus("等待手机定位后显示附近地点");
            return;
        }

        showSuggestionStatus("正在搜索附近地点…");
        amapRouteClient.searchNearby(key, lastLocation, keyword, new AmapRouteClient.SearchCallback() {
            @Override
            public void onSuccess(List<AmapRouteClient.PlaceSuggestion> suggestions) {
                runOnUiThread(() -> {
                    if (!destinationInput.getText().toString().trim().equals(keyword)) {
                        return;
                    }
                    showDestinationSuggestions(suggestions);
                });
            }

            @Override
            public void onError(String message) {
                runOnUiThread(() -> {
                    if (destinationInput.getText().toString().trim().equals(keyword)) {
                        showSuggestionStatus(message);
                    }
                });
            }
        });
    }

    private void showDestinationSuggestions(List<AmapRouteClient.PlaceSuggestion> suggestions) {
        destinationSuggestions.removeAllViews();
        if (suggestions.isEmpty()) {
            destinationSuggestions.setVisibility(View.GONE);
            showSuggestionStatus("附近 5 公里内没有找到匹配地点");
            return;
        }

        suggestionStatusText.setVisibility(View.GONE);
        destinationSuggestions.setVisibility(View.VISIBLE);
        for (AmapRouteClient.PlaceSuggestion suggestion : suggestions) {
            TextView row = new TextView(this);
            row.setText(String.format(
                    Locale.CHINA,
                    "%s\n%s  ·  %s",
                    suggestion.name,
                    suggestion.address.isEmpty() ? "地址信息暂缺" : suggestion.address,
                    formatSuggestionDistance(suggestion.distanceMeters)));
            row.setTextColor(ContextCompat.getColor(this, R.color.nav_text));
            row.setTextSize(16f);
            int horizontal = dp(14);
            int vertical = dp(12);
            row.setPadding(horizontal, vertical, horizontal, vertical);
            row.setBackgroundColor(ContextCompat.getColor(this, R.color.nav_surface));
            row.setOnClickListener(view -> selectDestinationSuggestion(suggestion));
            LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT);
            params.bottomMargin = dp(2);
            destinationSuggestions.addView(row, params);
        }
    }

    private void selectDestinationSuggestion(AmapRouteClient.PlaceSuggestion suggestion) {
        selectedDestination = suggestion;
        applyingSuggestion = true;
        destinationInput.setText(suggestion.name);
        destinationInput.setSelection(destinationInput.length());
        applyingSuggestion = false;
        destinationSuggestions.setVisibility(View.GONE);
        showSuggestionStatus(String.format(
                Locale.CHINA,
                "已选择：%s，%s",
                suggestion.name,
                formatSuggestionDistance(suggestion.distanceMeters)));
    }

    private void showSuggestionStatus(String message) {
        suggestionStatusText.setText(message);
        suggestionStatusText.setVisibility(View.VISIBLE);
    }

    private String formatSuggestionDistance(int distanceMeters) {
        if (distanceMeters < 1000) {
            return String.format(Locale.CHINA, "距离 %d 米", distanceMeters);
        }
        return String.format(Locale.CHINA, "距离 %.1f 公里", distanceMeters / 1000f);
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private float shortestTurn(float currentDegrees, float targetDegrees) {
        return ((targetDegrees - currentDegrees + 540f) % 360f) - 180f;
    }

    private synchronized void startStreaming() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
                != PackageManager.PERMISSION_GRANTED) {
            return;
        }
        if (streamingThread != null && streamingThread.isAlive()) {
            restartStreamingWhenStopped = activityResumed;
            return;
        }

        restartStreamingWhenStopped = false;
        streaming.set(true);
        streamingThread = new Thread(this::streamDepth, "realsense-depth");
        streamingThread.start();
    }

    private synchronized void stopStreaming() {
        restartStreamingWhenStopped = false;
        streaming.set(false);
        if (streamingThread != null) {
            streamingThread.interrupt();
        }
    }

    private void streamDepth() {
        Pipeline pipeline = new Pipeline();
        ArrayBlockingQueue<FrameSet> videoFrames =
                new ArrayBlockingQueue<>(VIDEO_FRAME_QUEUE_CAPACITY);
        AtomicLong droppedVideoFrames = new AtomicLong();
        vinsTimestampMapper.reset();
        try {
            try (Config config = new Config()) {
                // The source tracker consumes every camera frame and only publishes
                // features at 10 Hz. At 15 FPS, fast turns doubled the optical-flow
                // displacement and collapsed inter-frame feature overlap.
                config.enableStream(StreamType.DEPTH, -1, VIDEO_WIDTH, VIDEO_HEIGHT,
                        StreamFormat.Z16, VIDEO_FPS);
                config.enableStream(StreamType.COLOR, -1, VIDEO_WIDTH, VIDEO_HEIGHT,
                        StreamFormat.RGB8, VIDEO_FPS);
                config.enableStream(StreamType.GYRO, -1, 0, 0,
                        StreamFormat.MOTION_XYZ32F, 200);
                config.enableStream(StreamType.ACCEL, -1, 0, 0,
                        StreamFormat.MOTION_XYZ32F, 100);
                FrameCallback callback = incoming -> {
                    try {
                        if (incoming == null || !streaming.get()) return;
                        if (incoming.is(Extension.MOTION_FRAME)) {
                            collectVinsImu(incoming);
                        } else if (incoming.is(Extension.COMPOSITE_FRAME)) {
                            FrameSet retained = incoming.<FrameSet>as(Extension.COMPOSITE_FRAME).clone();
                            if (!videoFrames.offer(retained)) {
                                retained.close();
                                long dropped = droppedVideoFrames.incrementAndGet();
                                if (dropped == 1L || dropped % 15L == 0L) {
                                    Log.w(TAG, "VINS_VIDEO_DROP count=" + dropped
                                            + " queue=" + videoFrames.size());
                                }
                            }
                        }
                    } catch (Exception error) {
                        Log.e(TAG, "RealSense callback failed", error);
                    }
                };
                try (PipelineProfile profile = pipeline.start(config, callback);
                     Align align = new Align(StreamType.COLOR)) {
                Intrinsic colorIntrinsic = initializeVins(profile);
                runOnUiThread(() -> cameraStatusText.setText("深度相机运行中"));
                long frameCount = 0;
                int consecutiveVideoTimeouts = 0;

                while (streaming.get() && !Thread.currentThread().isInterrupted()) {
                    FrameSet queued = videoFrames.poll(1000, TimeUnit.MILLISECONDS);
                    if (queued == null) {
                        consecutiveVideoTimeouts++;
                        if (consecutiveVideoTimeouts >= 5) {
                            throw new IllegalStateException("连续 5 秒未收到深度视频帧，请重插相机");
                        }
                        continue;
                    }
                    consecutiveVideoTimeouts = 0;
                    try (FrameSet frames = queued;
                         Frame colorFrame = frames.first(StreamType.COLOR)) {
                        frameCount++;
                        byte[] rgb = null;
                        Intrinsic intrinsic = colorIntrinsic;
                        double imageTime = Double.NaN;
                        int colorWidth = 0;
                        int colorHeight = 0;
                        int colorStride = 0;
                        if (colorFrame != null) {
                            VideoFrame color = colorFrame.as(Extension.VIDEO_FRAME);
                            logColorFrameMetadata(colorFrame, frameCount);
                            colorWidth = color.getWidth();
                            colorHeight = color.getHeight();
                            colorStride = color.getStride();
                            rgb = new byte[color.getDataSize()];
                            color.getData(rgb);
                            if (BuildConfig.DEBUG) {
                                writeDiagnosticRgbOnce(
                                        rgb, colorWidth, colorHeight, colorStride);
                            }
                            imageTime = vinsTimestampMapper.toSystemTimeMilliseconds(
                                    color.getTimestamp(), color.getTimestampDomain(),
                                    System.currentTimeMillis());
                            vinsInput.addImage(imageTime, rgb, colorWidth,
                                    colorHeight, colorStride, intrinsic);
                            requestVinsProcessing();
                        }

                        // The source VINS image path is independent of depth alignment.
                        // Keep feature tracking at 30 Hz and run navigation depth work at
                        // 10 Hz so alignment and rendering cannot delay the next image.
                        if (frameCount % NAVIGATION_FRAME_INTERVAL == 0) {
                            try (Frame rawDepthFrame = frames.first(StreamType.DEPTH);
                                 FrameSet alignedFrames = frames.applyFilter(align);
                                 Frame alignedDepthFrame = alignedFrames.first(StreamType.DEPTH)) {
                                DepthFrame rawDepth = rawDepthFrame.as(Extension.DEPTH_FRAME);
                                DepthFrame alignedDepth = alignedDepthFrame.as(Extension.DEPTH_FRAME);
                                SectorDistances distances = analyzeDepth(alignedDepth);
                                if (rgb != null && intrinsic != null && Double.isFinite(imageTime)
                                        && vinsInitialized
                                        && frameCount % COLOR_FRAME_INTERVAL == 0
                                        && semanticSegmenter != null) {
                                    byte[] semanticDepth = new byte[alignedDepth.getDataSize()];
                                    alignedDepth.getData(semanticDepth);
                                    semanticSegmenter.submitRgb(
                                            rgb, colorWidth, colorHeight, colorStride,
                                            semanticDepth, alignedDepth.getWidth(), alignedDepth.getHeight(),
                                            alignedDepth.getStride(), alignedDepth.getUnits(), intrinsic,
                                            imageTime / 1000.0);
                                }
                                SemanticSegmenter.Result semantic = latestSemanticResult;
                                updateDepthPreview(alignedDepth, rawDepth, frameCount);
                                long shownFrameCount = frameCount;
                                // The camera thread is faster than the Android UI once the
                                // local cost grid is visible. Keep only one UI callback so
                                // stale frames cannot accumulate until the Java heap fails.
                                if (uiUpdatePending.compareAndSet(false, true)) {
                                    runOnUiThread(() -> {
                                        try {
                                            updateUi(distances, shownFrameCount, semantic);
                                        } finally {
                                            uiUpdatePending.set(false);
                                        }
                                    });
                                }
                            }
                        }
                    }
                }
                }
            }
        } catch (Exception error) {
            Log.e(TAG, "RealSense streaming stopped", error);
            if (streaming.get()) {
                runOnUiThread(() -> {
                    cameraStatusText.setText("深度相机启动失败");
                    frameStatusText.setText(error.getMessage() == null
                            ? error.getClass().getSimpleName()
                            : error.getMessage());
                });
            }
        } finally {
            try {
                pipeline.stop();
            } catch (Exception ignored) {
                // Pipeline may already be stopped after a USB disconnect.
            }
            FrameSet queued;
            while ((queued = videoFrames.poll()) != null) queued.close();
            vinsInput.clear();
            closeVins();
            boolean restart;
            synchronized (this) {
                streaming.set(false);
                if (Thread.currentThread() == streamingThread) {
                    streamingThread = null;
                }
                restart = restartStreamingWhenStopped && activityResumed;
                restartStreamingWhenStopped = false;
            }
            if (restart) runOnUiThread(this::startStreaming);
        }
    }

    private void writeDiagnosticRgbOnce(byte[] rgb, int width, int height, int stride) {
        if (diagnosticRgbFrames.incrementAndGet() < 150L) return;
        if (!diagnosticRgbWritten.compareAndSet(false, true)) return;
        File output = new File(getFilesDir(), "diagnostic_rgb.ppm");
        try (FileOutputStream stream = new FileOutputStream(output)) {
            stream.write(String.format(Locale.US, "P6\n%d %d\n255\n", width, height)
                    .getBytes(StandardCharsets.US_ASCII));
            int rowBytes = width * 3;
            for (int y = 0; y < height; y++) {
                stream.write(rgb, y * stride, rowBytes);
            }
            Log.i(TAG, "DIAGNOSTIC_RGB path=" + output.getAbsolutePath()
                    + " size=" + width + "x" + height + " stride=" + stride);
        } catch (Exception error) {
            diagnosticRgbWritten.set(false);
            Log.w(TAG, "Unable to write diagnostic RGB frame", error);
        }
    }

    private Intrinsic initializeVins(PipelineProfile pipelineProfile) throws Exception {
        StreamProfile colorProfile = null;
        StreamProfile gyroProfile = null;
        Intrinsic intrinsic;
        Extrinsic cameraToImu;
        try (Device device = pipelineProfile.getDevice()) {
            logCameraInfo(device, CameraInfo.NAME);
            logCameraInfo(device, CameraInfo.FIRMWARE_VERSION);
            logCameraInfo(device, CameraInfo.RECOMMENDED_FIRMWARE_VERSION);
            logCameraInfo(device, CameraInfo.USB_TYPE_DESCRIPTOR);
            logCameraInfo(device, CameraInfo.IMU_TYPE);
            for (Sensor sensor : device.querySensors()) {
                boolean colorSensor = false;
                for (StreamProfile activeProfile : sensor.getActiveStreams()) {
                    if (activeProfile.getType() == StreamType.COLOR) {
                        colorSensor = true;
                        break;
                    }
                }
                if (colorSensor) configureColorExposure(sensor);
                logSensorOption(sensor, Option.ENABLE_AUTO_EXPOSURE);
                logSensorOption(sensor, Option.EXPOSURE);
                logSensorOption(sensor, Option.GAIN);
                logSensorOption(sensor, Option.AUTO_EXPOSURE_PRIORITY);
                logSensorOption(sensor, Option.AUTO_EXPOSURE_LIMIT);
                logSensorOption(sensor, Option.OPTION_AUTO_EXPOSURE_LIMIT_TOGGLE);
                logSensorOption(sensor, Option.ENABLE_AUTO_WHITE_BALANCE);
                logSensorOption(sensor, Option.WHITE_BALANCE);
                for (StreamProfile profile : sensor.getActiveStreams()) {
                    if (profile.getType() == StreamType.COLOR) colorProfile = profile;
                    if (profile.getType() == StreamType.GYRO) gyroProfile = profile;
                }
            }
            if (colorProfile == null || gyroProfile == null) {
                throw new IllegalStateException("D455F color/gyro profiles unavailable for VINS");
            }
            VideoStreamProfile videoProfile = colorProfile.as(Extension.VIDEO_PROFILE);
            intrinsic = videoProfile.getIntrinsic();
            cameraToImu = colorProfile.getExtrinsicTo(gyroProfile);
        }
        closeVins();
        vinsMono = new VinsMono(intrinsic, cameraToImu);
        vinsInitialized = false;
        latestVinsPoseNanos = 0L;
        runOnUiThread(() -> renderVinsStatus(vinsInput.status()));
        return intrinsic;
    }

    private static void logCameraInfo(Device device, CameraInfo info) {
        if (device.supportsInfo(info)) {
            Log.i(TAG, "D455F " + info.name() + "=" + device.getInfo(info));
        }
    }

    private static void logSensorOption(Sensor sensor, Option option) {
        try {
            if (sensor.supports(option)) {
                Log.i(TAG, String.format(Locale.US,
                        "D455F option %s value=%.3f default=%.3f range=[%.3f,%.3f]",
                        option.name(), sensor.getValue(option), sensor.getDefault(option),
                        sensor.getMinRange(option), sensor.getMaxRange(option)));
            }
        } catch (Exception error) {
            Log.w(TAG, "Unable to query D455F option " + option.name(), error);
        }
    }

    private static void configureColorExposure(Sensor sensor) {
        try {
            // Preserve RealSense auto exposure/gain, but prevent long indoor
            // exposures from smearing motion beyond the source 21x21 LK window.
            if (sensor.supports(Option.ENABLE_AUTO_EXPOSURE)) {
                sensor.setValue(Option.ENABLE_AUTO_EXPOSURE, 1f);
            }
            if (sensor.supports(Option.AUTO_EXPOSURE_PRIORITY)) {
                sensor.setValue(Option.AUTO_EXPOSURE_PRIORITY, 0f);
            }
            if (sensor.supports(Option.OPTION_AUTO_EXPOSURE_LIMIT_TOGGLE)) {
                sensor.setValue(Option.OPTION_AUTO_EXPOSURE_LIMIT_TOGGLE, 1f);
            }
            if (sensor.supports(Option.AUTO_EXPOSURE_LIMIT)) {
                float limit = Math.max(sensor.getMinRange(Option.AUTO_EXPOSURE_LIMIT),
                        Math.min(COLOR_AUTO_EXPOSURE_LIMIT_US,
                                sensor.getMaxRange(Option.AUTO_EXPOSURE_LIMIT)));
                sensor.setValue(Option.AUTO_EXPOSURE_LIMIT, limit);
                Log.i(TAG, String.format(Locale.US,
                        "D455F color auto-exposure limited to %.0f us", limit));
            } else {
                Log.w(TAG, "D455F color sensor does not support AUTO_EXPOSURE_LIMIT");
            }
        } catch (Exception error) {
            Log.w(TAG, "Unable to configure D455F color exposure limit", error);
        }
    }

    private static void logColorFrameMetadata(Frame colorFrame, long frameCount) {
        if (frameCount % COLOR_METADATA_LOG_INTERVAL != 0) return;
        try {
            long exposure = colorFrame.supportsMetadata(FrameMetadata.ACTUAL_EXPOSURE)
                    ? colorFrame.getMetadata(FrameMetadata.ACTUAL_EXPOSURE) : -1L;
            long gain = colorFrame.supportsMetadata(FrameMetadata.GAIN_LEVEL)
                    ? colorFrame.getMetadata(FrameMetadata.GAIN_LEVEL) : -1L;
            long fps = colorFrame.supportsMetadata(FrameMetadata.ACTUAL_FPS)
                    ? colorFrame.getMetadata(FrameMetadata.ACTUAL_FPS) : -1L;
            Log.i(TAG, "D455F_COLOR_FRAME exposure_us=" + exposure
                    + " gain=" + gain + " actual_fps=" + fps);
        } catch (Exception error) {
            Log.w(TAG, "Unable to read D455F color frame metadata", error);
        }
    }

    private static ExecutorService newVinsExecutor(String name) {
        return Executors.newSingleThreadExecutor(runnable -> new Thread(() -> {
            Process.setThreadPriority(Process.THREAD_PRIORITY_MORE_FAVORABLE);
            runnable.run();
        }, name));
    }

    private void processVinsMeasurements() {
        VinsMono local = vinsMono;
        if (local == null) return;
        VinsInputBuffer.ImageSample image;
        while ((image = vinsInput.pollReadyImage(local.currentTimeOffsetSeconds())) != null) {
            VinsMono.TrackedFrame tracked = local.track(image);
            if (local.consumeTrackerRestart()) {
                handleVinsRestart(local, true);
                break;
            }
            if (tracked == null) continue;
            synchronized (vinsEstimateQueueLock) {
                pendingVinsEstimates.addLast(new VinsEstimateWork(local, tracked));
            }
            requestVinsEstimate();
        }
    }

    private void requestVinsEstimate() {
        if (vinsEstimatorExecutor.isShutdown()
                || !vinsEstimatePending.compareAndSet(false, true)) return;
        vinsEstimatorExecutor.execute(() -> {
            try {
                while (true) {
                    VinsEstimateWork work;
                    synchronized (vinsEstimateQueueLock) {
                        work = pendingVinsEstimates.pollFirst();
                    }
                    if (work == null) break;
                    processVinsEstimate(work.vins, work.tracked);
                }
            } finally {
                vinsEstimatePending.set(false);
                synchronized (vinsEstimateQueueLock) {
                    if (!pendingVinsEstimates.isEmpty()) requestVinsEstimate();
                }
            }
        });
    }

    private void processVinsEstimate(VinsMono local, VinsMono.TrackedFrame tracked) {
        if (vinsMono != local) return;
        VinsMono.Pose pose = local.process(vinsInput, tracked);
        if (pose != null && vinsMono == local) {
            boolean wasInitialized = vinsInitialized;
            if (pose.initialized) {
                consecutiveUninitializedPoses = 0;
                vinsInitialized = true;
            } else if (wasInitialized) {
                // The estimator can publish a transient non-initialized flag while
                // its sliding-window optimization catches up.  Do not destroy the
                // VINS/map state on a single bad output; require a short run of
                // consecutive losses, as the source node does for a real restart.
                consecutiveUninitializedPoses++;
            }
            if (wasInitialized && !pose.initialized && consecutiveUninitializedPoses >= 5) {
                consecutiveUninitializedPoses = 0;
                vinsInitialized = false;
                handleVinsRestart(local, false);
                return;
            }
            if (!wasInitialized) vinsInitialized = pose.initialized;
            latestVinsPose = pose;
            latestVinsPoseNanos = SystemClock.elapsedRealtimeNanos();
            calibrationVinsPoseHistory.add(pose);
            if (semanticSegmenter != null) semanticSegmenter.updateVinsPose(pose);
        }
    }

    private void handleVinsRestart(VinsMono local, boolean clearInput) {
        if (vinsMono != local) return;
        Log.w(TAG, "VINS_RESTART source="
                + (clearInput ? "tracker_timestamp_gap" : "estimator_pose_lost")
                + " clearInput=" + clearInput);
        if (clearInput) vinsInput.clear();
        vinsResetCount++;
        consecutiveUninitializedPoses = 0;
        vinsInitialized = false;
        latestVinsPose = null;
        latestVinsPoseNanos = 0L;
        resetVinsDependents();
    }

    private void resetVinsDependents() {
        calibrationVinsPoseHistory.clear();
        dynamicHeadingCalibrator.resetForVinsRestart();
        latestSemanticResult = SemanticSegmenter.Result.waiting();
        latestSemanticResultNanos = 0L;
        SemanticSegmenter segmenter = semanticSegmenter;
        if (segmenter != null) segmenter.resetVinsState();
        runOnUiThread(() -> {
            resetLocalPlanning();
            renderDynamicHeadingCalibration();
            renderVinsStatus(vinsInput.status());
        });
    }

    private void requestVinsProcessing() {
        if (vinsExecutor.isShutdown() || !vinsDrainPending.compareAndSet(false, true)) return;
        vinsExecutor.execute(() -> {
            try {
                processVinsMeasurements();
            } finally {
                vinsDrainPending.set(false);
                VinsMono local = vinsMono;
                if (local != null
                        && vinsInput.hasReadyImage(local.currentTimeOffsetSeconds())) {
                    requestVinsProcessing();
                }
            }
        });
    }

    private void closeVins() {
        VinsMono local = vinsMono;
        vinsMono = null;
        latestVinsPose = null;
        vinsInitialized = false;
        consecutiveUninitializedPoses = 0;
        latestVinsPoseNanos = 0L;
        synchronized (vinsEstimateQueueLock) {
            pendingVinsEstimates.clear();
        }
        resetVinsDependents();
        if (local != null) local.close();
    }

    private static final class VinsEstimateWork {
        final VinsMono vins;
        final VinsMono.TrackedFrame tracked;

        VinsEstimateWork(VinsMono vins, VinsMono.TrackedFrame tracked) {
            this.vins = vins;
            this.tracked = tracked;
        }
    }

    private void collectVinsImu(Frame frame) {
        try (StreamProfile profile = frame.getProfile()) {
            MotionFrame motion = frame.as(Extension.MOTION_FRAME);
            float[] value = motion.getMotionDataArray();
            TimestampDomain domain = frame.getTimestampDomain();
            double timestamp = vinsTimestampMapper.toSystemTimeMilliseconds(
                    frame.getTimestamp(), domain, System.currentTimeMillis());
            if (profile.getType() == StreamType.GYRO) {
                vinsInput.addGyroscope(timestamp, value[0], value[1], value[2]);
            } else if (profile.getType() == StreamType.ACCEL) {
                vinsInput.addAccelerometer(timestamp, value[0], value[1], value[2]);
            }
        }
    }

    private void updateDepthPreview(DepthFrame alignedDepth, DepthFrame nativeDepth,
                                    long frameCount) {
        if (frameCount % PREVIEW_FRAME_INTERVAL != 0
                || depthPreview == null
                || !previewUpdatePending.compareAndSet(false, true)) {
            return;
        }

        try {
            int sourceWidth = alignedDepth.getWidth();
            int sourceHeight = alignedDepth.getHeight();
            int stride = alignedDepth.getStride();
            int previewWidth = sourceWidth / PREVIEW_DOWNSAMPLE;
            int previewHeight = sourceHeight / PREVIEW_DOWNSAMPLE;
            float units = alignedDepth.getUnits();
            int pixelCount = previewWidth * previewHeight;
            if (previewDepthBuffer == null
                    || previewDepthBuffer.length != alignedDepth.getDataSize()) {
                previewDepthBuffer = new byte[alignedDepth.getDataSize()];
            }
            if (previewPixelBuffer == null || previewPixelBuffer.length != pixelCount) {
                previewPixelBuffer = new int[pixelCount];
            }
            if (previewBitmap == null || previewBitmap.getWidth() != previewWidth
                    || previewBitmap.getHeight() != previewHeight) {
                if (previewBitmap != null) previewBitmap.recycle();
                previewBitmap = Bitmap.createBitmap(
                        previewWidth, previewHeight, Bitmap.Config.ARGB_8888);
            }
            byte[] selectedDepth = previewDepthBuffer;
            int[] pixels = previewPixelBuffer;
            alignedDepth.getData(previewDepthBuffer);
            int selectedValid = DepthFrameStats.countValidSamples(
                    previewDepthBuffer, sourceWidth, sourceHeight, stride,
                    PREVIEW_DOWNSAMPLE);
            previewUsesNativeDepth = false;

            if (nativeDepth != null
                    && nativeDepth.getWidth() == sourceWidth
                    && nativeDepth.getHeight() == sourceHeight
                    && nativeDepth.getStride() == stride) {
                if (previewAlternateDepthBuffer == null
                        || previewAlternateDepthBuffer.length != nativeDepth.getDataSize()) {
                    previewAlternateDepthBuffer = new byte[nativeDepth.getDataSize()];
                }
                nativeDepth.getData(previewAlternateDepthBuffer);
                int nativeValid = DepthFrameStats.countValidSamples(
                        previewAlternateDepthBuffer, sourceWidth, sourceHeight, stride,
                        PREVIEW_DOWNSAMPLE);
                if (nativeValid > selectedValid) {
                    selectedDepth = previewAlternateDepthBuffer;
                    selectedValid = nativeValid;
                    units = nativeDepth.getUnits();
                    previewUsesNativeDepth = true;
                }
            }
            previewValidPercent = pixelCount == 0
                    ? Float.NaN : selectedValid * 100f / pixelCount;

            for (int y = 0; y < previewHeight; y++) {
                int sourceRow = y * PREVIEW_DOWNSAMPLE * stride;
                int outputRow = y * previewWidth;
                for (int x = 0; x < previewWidth; x++) {
                    int sourceIndex = sourceRow + x * PREVIEW_DOWNSAMPLE * 2;
                    int rawValue = (selectedDepth[sourceIndex] & 0xff)
                            | ((selectedDepth[sourceIndex + 1] & 0xff) << 8);
                    if (rawValue == 0) {
                        pixels[outputRow + x] = Color.BLACK;
                        continue;
                    }

                    float meters = rawValue * units;
                    int paletteIndex = Math.round(
                            (meters - VALID_MIN_METERS)
                                    * 255f / (PREVIEW_MAX_METERS - VALID_MIN_METERS));
                    paletteIndex = Math.max(0, Math.min(255, paletteIndex));
                    pixels[outputRow + x] = DEPTH_PALETTE[paletteIndex];
                }
            }

            previewBitmap.setPixels(pixels, 0, previewWidth,
                    0, 0, previewWidth, previewHeight);
            Bitmap bitmap = previewBitmap;
            runOnUiThread(() -> {
                if (depthPreview != null) {
                    depthPreview.setImageBitmap(bitmap);
                }
                previewUpdatePending.set(false);
            });
        } catch (RuntimeException error) {
            previewUpdatePending.set(false);
        }
    }

    private static int[] createDepthPalette() {
        int[] palette = new int[256];
        for (int i = 0; i < palette.length; i++) {
            float hue = i * 240f / 255f;
            palette[i] = Color.HSVToColor(new float[]{hue, 1f, 1f});
        }
        return palette;
    }

    private SectorDistances analyzeDepth(DepthFrame depth) {
        int width = depth.getWidth();
        int height = depth.getHeight();
        int yStart = height * 35 / 100;
        int yEnd = height * 82 / 100;

        float left = sectorDistance(depth, width * 5 / 100, width * 33 / 100, yStart, yEnd);
        float center = sectorDistance(depth, width * 36 / 100, width * 64 / 100, yStart, yEnd);
        float right = sectorDistance(depth, width * 67 / 100, width * 95 / 100, yStart, yEnd);

        return new SectorDistances(left, center, right);
    }

    private float sectorDistance(DepthFrame depth, int xStart, int xEnd, int yStart, int yEnd) {
        int capacity = Math.max(1,
                ((xEnd - xStart) / SAMPLE_STEP_PIXELS + 1)
                        * ((yEnd - yStart) / SAMPLE_STEP_PIXELS + 1));
        float[] values = new float[capacity];
        int count = 0;

        for (int y = yStart; y < yEnd; y += SAMPLE_STEP_PIXELS) {
            for (int x = xStart; x < xEnd; x += SAMPLE_STEP_PIXELS) {
                float distance = depth.getDistance(x, y);
                if (distance >= VALID_MIN_METERS && distance <= VALID_MAX_METERS) {
                    if (count == values.length) {
                        values = Arrays.copyOf(values, values.length * 2);
                    }
                    values[count++] = distance;
                }
            }
        }

        if (count < 8) {
            return Float.NaN;
        }

        Arrays.sort(values, 0, count);
        int percentileIndex = Math.min(count - 1, Math.round((count - 1) * 0.20f));
        return values[percentileIndex];
    }

    private void updateUi(
            SectorDistances distances,
            long frameCount,
            SemanticSegmenter.Result semantic) {
        leftDistanceText.setText(formatDistance("左侧", distances.left));
        centerDistanceText.setText(formatDistance("前方", distances.center));
        rightDistanceText.setText(formatDistance("右侧", distances.right));
        frameStatusText.setText(String.format(Locale.CHINA, "深度帧 %,d", frameCount));
        if (Float.isFinite(previewValidPercent)) {
            frameStatusText.append(String.format(Locale.CHINA,
                    " · 深度有效 %.1f%%（%s）", previewValidPercent,
                    previewUsesNativeDepth ? "原始" : "彩色对齐"));
        }
        VinsInputBuffer.Status vinsStatus = vinsInput.status();
        frameStatusText.append(String.format(Locale.CHINA,
                " · VINS输入 图像 %,d / 陀螺仪 %,d / 加速度 %,d / 合并IMU %,d / 配对 %,d%s",
                vinsStatus.images, vinsStatus.gyroscope, vinsStatus.accelerometer,
                vinsStatus.unifiedImu, vinsStatus.pairedImages,
                vinsStatus.ready() ? "（已就绪）" : "（等待数据）"));
        renderVinsStatus(vinsStatus);
        renderSemanticStatus(semantic);
        renderLocalPlanStatus(semantic);

        String guidance;
        int color;
        if (navigationActive && !latestLocalPlan.planned) {
            guidance = "等待局部 A* 规划";
            color = R.color.nav_warning;
        } else if (navigationActive && !latestLocalPlan.success) {
            guidance = "停止，A*搜索无路径";
            color = R.color.nav_danger;
        } else if (navigationActive && latestLocalPlan.blocked) {
            guidance = "停止，前视路径阻塞";
            color = R.color.nav_danger;
        } else if (navigationActive && latestLocalPlan.success
                && Float.isFinite(latestLocalPlan.steeringDegrees)) {
            float steering = latestLocalPlan.steeringDegrees;
            if (Math.abs(steering) <= 10f) {
                guidance = "可以直行";
                color = R.color.nav_safe;
            } else if (steering > 0f) {
                guidance = "向左绕行";
                color = R.color.nav_warning;
            } else {
                guidance = "向右绕行";
                color = R.color.nav_warning;
            }
        } else if (navigationActive) {
            guidance = "等待局部 A* 规划";
            color = R.color.nav_warning;
        } else if (semantic.isNotWalkable && semantic.leftCost > 0.5f
                && semantic.rightCost > 0.5f) {
            guidance = "前方代价 100，左右均不可通行";
            color = R.color.nav_danger;
        } else if (semantic.isNotWalkable && semantic.leftCost <= 0.5f) {
            guidance = "前方代价高，向左绕行";
            color = R.color.nav_warning;
        } else if (semantic.isNotWalkable && semantic.rightCost <= 0.5f) {
            guidance = "前方代价高，向右绕行";
            color = R.color.nav_warning;
        } else if (isCloserThan(distances.center, STOP_METERS)
                && isCloserThan(distances.left, STOP_METERS)
                && isCloserThan(distances.right, STOP_METERS)) {
            guidance = "停止";
            color = R.color.nav_danger;
        } else if (!isCloserThan(distances.center, OBSTACLE_METERS)) {
            guidance = "可以直行";
            color = R.color.nav_safe;
        } else if (clearance(distances.left) >= clearance(distances.right)) {
            guidance = "向左绕行";
            color = R.color.nav_warning;
        } else {
            guidance = "向右绕行";
            color = R.color.nav_warning;
        }

        guidanceText.setText(guidance);
        guidanceText.setTextColor(ContextCompat.getColor(this, color));
    }

    private LocalPlanner.PathResult computeLocalPlan(SemanticSegmenter.Result semantic) {
        // Source local_planner.py leaves target_direction as None until global navigation supplies it.
        if (!navigationActive) {
            return LocalPlanner.PathResult.waitingForTarget();
        }
        if (lastLocation == null) return LocalPlanner.PathResult.waiting("等待手机定位");
        if (!routeFollower.hasRoute()) return LocalPlanner.PathResult.waiting("等待全局路线");
        if (semantic.localCostGrid == null
                || semantic.localCostGrid.length != MapTransform.WIDTH * MapTransform.HEIGHT) {
            return LocalPlanner.PathResult.waiting("等待语义点云/OctoMap局部代价图");
        }
        int[][] grid = new int[MapTransform.HEIGHT][MapTransform.WIDTH];
        for (int r = 0; r < MapTransform.HEIGHT; r++) {
            System.arraycopy(semantic.localCostGrid, r * MapTransform.WIDTH,
                    grid[r], 0, MapTransform.WIDTH);
        }
        RouteFollower.Guidance guidance = routeFollower.update(
                lastLocation.getLatitude(), lastLocation.getLongitude(),
                lastLocation.hasAccuracy() ? lastLocation.getAccuracy() : 0f,
                Float.NaN);
        if (guidance == null) {
            return LocalPlanner.PathResult.waiting("等待全局路线目标");
        }
        float relativeTarget = dynamicHeadingCalibrator.relativeTargetDegrees(
                guidance.targetBearingDegrees, latestVinsPose);
        if (!Float.isFinite(relativeTarget)) {
            return LocalPlanner.PathResult.waiting(dynamicHeadingCalibrator.status());
        }
        float radians = (float) Math.toRadians(relativeTarget);
        float targetRow = (float) Math.cos(radians);
        float targetCol = (float) Math.sin(radians);
        return localPlanner.plan(grid, MapTransform.RESOLUTION, -7.9f, -7.9f,
                0f, 0f, targetCol, targetRow);
    }

    private void requestLocalPlanRefresh() {
        SemanticSegmenter segmenter = semanticSegmenter;
        if (!navigationActive || segmenter == null
                || !localPlanPending.compareAndSet(false, true)) {
            return;
        }
        long generation = localPlanGeneration.get();
        localPlanExecutor.execute(() -> {
            try {
                long refreshStartedNanos = SystemClock.elapsedRealtimeNanos();
                SemanticSegmenter.Result projected = segmenter.reprojectLatestLocalMap();
                if (generation != localPlanGeneration.get()) return;
                if (projected == null) {
                    LocalPlanner.PathResult waiting = LocalPlanner.PathResult.waiting(
                            segmenter.localMapWaitingReason());
                    long sequence = localPlanSequence.incrementAndGet();
                    latestLocalPlan = waiting;
                    recordLocalPlanMetrics(refreshStartedNanos, -1L);
                    runOnUiThread(() -> {
                        if (generation != localPlanGeneration.get()
                                || sequence < latestRenderedLocalPlanSequence) return;
                        latestRenderedLocalPlanSequence = sequence;
                        renderSemanticStatus(latestSemanticResult);
                        renderLocalPlanStatus(latestSemanticResult);
                        renderLocalPlanMetrics();
                        localPlanView.setPlan(waiting);
                    });
                    return;
                }
                long planStartedNanos = SystemClock.elapsedRealtimeNanos();
                LocalPlanner.PathResult plan = computeLocalPlan(projected);
                if (generation != localPlanGeneration.get()) return;
                long planDurationNanos = SystemClock.elapsedRealtimeNanos() - planStartedNanos;
                long sequence = localPlanSequence.incrementAndGet();
                latestSemanticResult = projected;
                latestLocalPlan = plan;
                recordLocalPlanMetrics(refreshStartedNanos, planDurationNanos);
                Log.i(TAG, String.format(Locale.US,
                        "Local plan #%d: known=%d path=%d steering=%.1f blocked=%s",
                        sequence, projected.costGridKnownCount, plan.worldPath.size(),
                        plan.steeringDegrees, plan.blocked));
                runOnUiThread(() -> {
                    if (generation != localPlanGeneration.get()
                            || sequence < latestRenderedLocalPlanSequence) return;
                    latestRenderedLocalPlanSequence = sequence;
                    renderSemanticStatus(projected);
                    renderLocalPlanStatus(projected);
                    renderLocalPlanMetrics();
                    localPlanView.setPlan(plan);
                    updateNavigationGuidance();
                });
            } catch (Exception error) {
                Log.e(TAG, "Local map reprojection/A* refresh failed", error);
            } finally {
                localPlanPending.set(false);
            }
        });
    }

    private void recordLocalPlanMetrics(long refreshStartedNanos, long planDurationNanos) {
        long completedNanos = SystemClock.elapsedRealtimeNanos();
        long previousNanos = latestLocalPlanCompletedNanos;
        latestLocalPlanCompletedNanos = completedNanos;
        latestLocalPlanDurationNanos = planDurationNanos;
        latestLocalPlanRefreshNanos = previousNanos > 0L
                ? completedNanos - previousNanos : -1L;
        long semanticNanos = latestSemanticResultNanos;
        latestLocalPlanInputAgeNanos = semanticNanos > 0L
                ? Math.max(0L, refreshStartedNanos - semanticNanos) : -1L;
    }

    private void renderSemanticStatus(SemanticSegmenter.Result semantic) {
        if (semantic.inferenceMillis <= 0L) {
            return;
        }
        if (!Float.isFinite(semantic.centerCost)) {
            if (semantic.semanticPointCount > 0 && semantic.octomapLeafCount > 0) {
                semanticStatusText.setText(String.format(
                        Locale.CHINA,
                        "Mask2Former：中心类别 %s · 语义点云 %,d 点 · OctoMap %,d 叶节点 · 局部代价栅格 %,d/6400 格 · %s · %s · 推理 %.1f 秒；%s",
                        semantic.label,
                        semantic.semanticPointCount,
                        semantic.octomapLeafCount,
                        semantic.costGridKnownCount,
                        Float.isFinite(semantic.groundHeight)
                                ? String.format(Locale.CHINA, "地面修正 %,d 格", semantic.groundClearedCells)
                                : "未检出可靠地面",
                        semantic.backend,
                        semantic.inferenceMillis / 1000f,
                        latestLocalPlan.planned ? "A*局部规划运行中" : "A*等待导航目标方向"));
            } else {
                semanticStatusText.setText(String.format(
                        Locale.CHINA,
                        "Mask2Former语义分割正常，中心类别 %s · %s · 推理 %.1f 秒；等待语义点云/OctoMap数据",
                        semantic.label,
                        semantic.backend,
                        semantic.inferenceMillis / 1000f));
            }
            semanticStatusText.setTextColor(ContextCompat.getColor(this, R.color.nav_muted));
            return;
        }
        if (semantic.isNotWalkable) {
            String reason = Float.isFinite(semantic.obstacleDistance)
                    ? String.format(Locale.CHINA, "深度距离 %.1f 米", semantic.obstacleDistance)
                    : "局部代价超过 50";
            semanticStatusText.setText(String.format(
                    Locale.CHINA, "Mask2Former：前方%s，类别 %s · 左 %.0f / 前 %.0f / 右 %.0f · %s · 推理 %.1f 秒",
                    reason, semantic.label, semantic.leftCost * 100f,
                    semantic.centerCost * 100f, semantic.rightCost * 100f,
                    semantic.backend, semantic.inferenceMillis / 1000f));
            semanticStatusText.setTextColor(ContextCompat.getColor(this, R.color.nav_warning));
        } else {
            semanticStatusText.setText(String.format(
                    Locale.CHINA,
                    "Mask2Former局部代价：左 %.0f / 前 %.0f / 右 %.0f，前方无不可通行栅格 · %s · 推理 %.1f 秒",
                    semantic.leftCost * 100f,
                    semantic.centerCost * 100f,
                    semantic.rightCost * 100f,
                    semantic.backend,
                    semantic.inferenceMillis / 1000f));
            semanticStatusText.setTextColor(ContextCompat.getColor(this, R.color.nav_muted));
        }
    }

    /** Runs the ported local_planner grid preprocessing and A* on each semantic map result. */
    private void renderLocalPlanStatus(SemanticSegmenter.Result semantic) {
        if (semantic.localCostGrid == null || semantic.localCostGrid.length != MapTransform.WIDTH * MapTransform.HEIGHT) {
            return;
        }
        LocalPlanner.PathResult result = latestLocalPlan;
        if (!result.planned) {
            semanticStatusText.append("\nA*局部规划: " + result.waitingReason);
            return;
        }
        semanticStatusText.append(String.format(Locale.CHINA,
                "\nA*移植规划: %s，路径点 %d，转角 %s，起点代价 %d，目标代价 %d，障碍格 %d",
                result.success ? (result.blocked ? "前视阻塞" : "已找到路径") : "搜索无路径",
                result.worldPath.size(),
                Float.isFinite(result.steeringDegrees)
                        ? String.format(Locale.CHINA, "%+.1f°", result.steeringDegrees) : "--",
                result.startCost, result.targetCost, result.obstacleCount));
        if (latestRenderedLocalPlanSequence > 0) {
            semanticStatusText.append(String.format(
                    Locale.CHINA, " · 更新 #%,d", latestRenderedLocalPlanSequence));
        }
    }

    private void renderVinsStatus(VinsInputBuffer.Status status) {
        if (vinsStatusPanel == null) return;
        VinsMono.Pose pose = latestVinsPose;
        String text;
        int color;
        if (vinsMono == null) {
            text = "VINS：等待深度相机\n尚未启动图像和 IMU 处理";
            color = R.color.nav_muted;
        } else if (pose != null && pose.initialized) {
            float ageSeconds = latestVinsPoseNanos > 0L
                    ? (SystemClock.elapsedRealtimeNanos() - latestVinsPoseNanos) / 1_000_000_000f
                    : 0f;
            text = String.format(Locale.CHINA,
                    "VINS：初始化成功\n位姿年龄 %.1f 秒 · 已配对 %,d 帧%s",
                    Math.max(0f, ageSeconds), status.pairedImages,
                    status.droppedImages > 0
                            ? String.format(Locale.CHINA, " · 输入丢弃 %,d", status.droppedImages)
                            : "");
            color = R.color.nav_safe;
        } else if (status.ready()) {
            text = vinsResetCount > 0
                    ? String.format(Locale.CHINA,
                    "VINS：已重置 %d 次，重新初始化中\n图像和 IMU 已同步，请缓慢平移并转动",
                    vinsResetCount)
                    : "VINS：初始化中\n图像和 IMU 已同步，请缓慢平移并转动";
            color = R.color.nav_warning;
        } else {
            text = String.format(Locale.CHINA,
                    "VINS：等待同步输入\n图像 %,d · 合并 IMU %,d",
                    status.images, status.unifiedImu);
            color = R.color.nav_muted;
        }
        vinsStatusPanel.setText(text);
        vinsStatusPanel.setTextColor(ContextCompat.getColor(this, color));
    }

    private void renderLocalPlanMetrics() {
        if (localPlanMetricsText == null) return;
        if (!navigationActive) {
            localPlanMetricsText.setText("A*诊断：等待开始导航");
            localPlanMetricsText.setTextColor(
                    ContextCompat.getColor(this, R.color.nav_muted));
            return;
        }
        String duration = latestLocalPlanDurationNanos >= 0L
                ? String.format(Locale.CHINA, "%.1f ms", latestLocalPlanDurationNanos / 1_000_000f)
                : "--";
        String refresh = latestLocalPlanRefreshNanos >= 0L
                ? String.format(Locale.CHINA, "%.0f ms", latestLocalPlanRefreshNanos / 1_000_000f)
                : "--";
        String inputAge = latestLocalPlanInputAgeNanos >= 0L
                ? String.format(Locale.CHINA, "%.1f 秒", latestLocalPlanInputAgeNanos / 1_000_000_000f)
                : "--";
        localPlanMetricsText.setText(String.format(Locale.CHINA,
                "A*单次 %s · 刷新间隔 %s\n代价图输入年龄 %s · 更新 #%,d",
                duration, refresh, inputAge, latestRenderedLocalPlanSequence));
        localPlanMetricsText.setTextColor(ContextCompat.getColor(this,
                latestLocalPlanDurationNanos >= 0L ? R.color.nav_safe : R.color.nav_warning));
    }

    private void resetLocalPlanning() {
        localPlanGeneration.incrementAndGet();
        localPlanner.clearTargetPath();
        latestLocalPlan = LocalPlanner.PathResult.waitingForTarget();
        latestRenderedLocalPlanSequence = 0L;
        latestLocalPlanDurationNanos = -1L;
        latestLocalPlanRefreshNanos = -1L;
        latestLocalPlanCompletedNanos = 0L;
        latestLocalPlanInputAgeNanos = -1L;
        if (localPlanView != null) localPlanView.setPlan(latestLocalPlan);
        renderLocalPlanMetrics();
    }

    private String formatDistance(String label, float distance) {
        if (!Float.isFinite(distance)) {
            return label + "\n-- m";
        }
        return String.format(Locale.CHINA, "%s\n%.2f m", label, distance);
    }

    private boolean isCloserThan(float distance, float threshold) {
        return Float.isFinite(distance) && distance < threshold;
    }

    private float clearance(float distance) {
        return Float.isFinite(distance) ? distance : VALID_MAX_METERS;
    }

    private static final class SectorDistances {
        final float left;
        final float center;
        final float right;

        SectorDistances(float left, float center, float right) {
            this.left = left;
            this.center = center;
            this.right = right;
        }
    }

}
