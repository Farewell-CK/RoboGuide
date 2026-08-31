#include <jni.h>
#include <android/log.h>

#include <algorithm>
#include <cmath>
#include <mutex>
#include <unordered_set>
#include <vector>

#include <opencv2/core.hpp>
#include "feature_tracker.h"

// ABI declarations from librealsense2/rsutil.h. The implementation remains in
// the exact librealsense2.so bundled with the Android app.
extern "C" {
enum rs2_distortion {
    RS2_DISTORTION_NONE = 0,
    RS2_DISTORTION_MODIFIED_BROWN_CONRADY = 1,
    RS2_DISTORTION_INVERSE_BROWN_CONRADY = 2,
    RS2_DISTORTION_FTHETA = 3,
    RS2_DISTORTION_BROWN_CONRADY = 4,
    RS2_DISTORTION_KANNALA_BRANDT4 = 5,
    RS2_DISTORTION_COUNT = 6
};

struct rs2_intrinsics {
    int width;
    int height;
    float ppx;
    float ppy;
    float fx;
    float fy;
    rs2_distortion model;
    float coeffs[5];
};

void rs2_deproject_pixel_to_point(
        float point[3], const rs2_intrinsics* intrinsics,
        const float pixel[2], float depth);
}

// These globals replace feature_tracker/parameters.cpp. Values are the same
// parameters consumed by the upstream tracker; only ROS parameter transport is removed.
std::string IMAGE_TOPIC;
std::string IMU_TOPIC;
std::vector<std::string> CAM_NAMES;
std::string FISHEYE_MASK;
int MAX_CNT = 150;
int MIN_DIST = 25;
int WINDOW_SIZE = 20;
int FREQ = 10;
double F_THRESHOLD = 1.0;
int SHOW_TRACK = 0;
int STEREO_TRACK = 0;
int EQUALIZE = 0;
int ROW = 480;
int COL = 640;
int FOCAL_LENGTH = 460;
int FISHEYE = 0;
bool PUB_THIS_FRAME = false;

namespace {
class RealSensePinholeCamera final : public camodocal::PinholeCamera {
public:
    RealSensePinholeCamera(const std::string& cameraName, int width, int height,
                          double fx, double fy, double cx, double cy,
                          int distortionModel, const double coefficients[5])
            : camodocal::PinholeCamera(cameraName, width, height,
                    0.0, 0.0, 0.0, 0.0, fx, fy, cx, cy) {
        intrinsics_.width = width;
        intrinsics_.height = height;
        intrinsics_.fx = static_cast<float>(fx);
        intrinsics_.fy = static_cast<float>(fy);
        intrinsics_.ppx = static_cast<float>(cx);
        intrinsics_.ppy = static_cast<float>(cy);
        intrinsics_.model = static_cast<rs2_distortion>(distortionModel);
        for (int index = 0; index < 5; ++index) {
            intrinsics_.coeffs[index] = static_cast<float>(coefficients[index]);
        }
    }

    void liftProjective(const Eigen::Vector2d& pixel, Eigen::Vector3d& ray) const override {
        const float source[2] = {
                static_cast<float>(pixel.x()), static_cast<float>(pixel.y())};
        float point[3];
        rs2_deproject_pixel_to_point(point, &intrinsics_, source, 1.0f);
        ray << static_cast<double>(point[0]),
                static_cast<double>(point[1]), static_cast<double>(point[2]);
    }

    void liftSphere(const Eigen::Vector2d& pixel, Eigen::Vector3d& ray) const override {
        liftProjective(pixel, ray);
        ray.normalize();
    }

private:
    rs2_intrinsics intrinsics_{};
};

struct AndroidFeatureTracker {
    FeatureTracker tracker;
    std::mutex mutex;
    double firstImageTime = 0.0;
    double lastImageTime = 0.0;
    int published = 1;
    bool firstImage = true;
    bool initializedOutput = false;
    bool restartPending = false;
    int diagnosticFrames = 0;
    std::unordered_set<int> previousPublishedIds;
};

AndroidFeatureTracker* fromHandle(jlong handle) {
    return reinterpret_cast<AndroidFeatureTracker*>(handle);
}

void reset(AndroidFeatureTracker& state) {
    camodocal::CameraPtr camera = state.tracker.m_camera;
    state.tracker = FeatureTracker();
    state.tracker.m_camera = camera;
    state.firstImageTime = 0.0;
    state.lastImageTime = 0.0;
    state.published = 1;
    state.firstImage = true;
    state.initializedOutput = false;
    state.restartPending = false;
    state.diagnosticFrames = 0;
    state.previousPublishedIds.clear();
}
}  // namespace

extern "C" JNIEXPORT jlong JNICALL
Java_com_elabrador_mobilenavigation_NativeVinsFeatureTracker_nativeCreate(
        JNIEnv* env, jclass, jint width, jint height, jdouble fx, jdouble fy,
        jdouble cx, jdouble cy, jint distortionModel,
        jdoubleArray distortionCoefficients, jint maxCount, jint minDistance,
        jdouble fundamentalThreshold, jboolean equalize) {
    COL = width;
    ROW = height;
    MAX_CNT = maxCount;
    MIN_DIST = minDistance;
    F_THRESHOLD = fundamentalThreshold;
    EQUALIZE = equalize ? 1 : 0;
    double coefficients[5] = {};
    if (distortionCoefficients) {
        const jsize count = std::min<jsize>(5, env->GetArrayLength(distortionCoefficients));
        env->GetDoubleArrayRegion(distortionCoefficients, 0, count, coefficients);
    }
    __android_log_print(ANDROID_LOG_INFO, "VINS-Mono",
            "color calibration: %dx%d fx=%.6f fy=%.6f cx=%.6f cy=%.6f model=%d coeff=[%.9f,%.9f,%.9f,%.9f,%.9f]",
            width, height, fx, fy, cx, cy, distortionModel,
            coefficients[0], coefficients[1], coefficients[2],
            coefficients[3], coefficients[4]);
    auto* state = new AndroidFeatureTracker();
    state->tracker.m_camera.reset(new RealSensePinholeCamera(
            "D455F-color", width, height, fx, fy, cx, cy,
            distortionModel, coefficients));

    Eigen::Vector3d exactCorner;
    state->tracker.m_camera->liftProjective(Eigen::Vector2d(0.0, 0.0), exactCorner);
    const double pinholeX = -cx / fx;
    const double pinholeY = -cy / fy;
    __android_log_print(ANDROID_LOG_INFO, "VINS-Mono",
            "exact RealSense rays enabled: corner=(%.9f,%.9f) zero_distortion=(%.9f,%.9f) delta=(%.9f,%.9f)",
            exactCorner.x() / exactCorner.z(), exactCorner.y() / exactCorner.z(),
            pinholeX, pinholeY,
            exactCorner.x() / exactCorner.z() - pinholeX,
            exactCorner.y() / exactCorner.z() - pinholeY);
    return reinterpret_cast<jlong>(state);
}

extern "C" JNIEXPORT void JNICALL
Java_com_elabrador_mobilenavigation_NativeVinsFeatureTracker_nativeDestroy(
        JNIEnv*, jclass, jlong handle) {
    delete fromHandle(handle);
}

extern "C" JNIEXPORT void JNICALL
Java_com_elabrador_mobilenavigation_NativeVinsFeatureTracker_nativeReset(
        JNIEnv*, jclass, jlong handle) {
    auto* state = fromHandle(handle);
    if (!state) return;
    std::lock_guard<std::mutex> lock(state->mutex);
    reset(*state);
}

extern "C" JNIEXPORT jboolean JNICALL
Java_com_elabrador_mobilenavigation_NativeVinsFeatureTracker_nativeConsumeRestart(
        JNIEnv*, jclass, jlong handle) {
    auto* state = fromHandle(handle);
    if (!state) return JNI_FALSE;
    std::lock_guard<std::mutex> lock(state->mutex);
    const bool restart = state->restartPending;
    state->restartPending = false;
    return restart ? JNI_TRUE : JNI_FALSE;
}

extern "C" JNIEXPORT jdoubleArray JNICALL
Java_com_elabrador_mobilenavigation_NativeVinsFeatureTracker_nativeTrack(
        JNIEnv* env, jclass, jlong handle, jbyteArray pixels, jint width,
        jint height, jint stride, jdouble timestamp) {
    auto* state = fromHandle(handle);
    if (!state || !pixels || width != COL || height != ROW || stride < width) {
        return env->NewDoubleArray(0);
    }
    std::lock_guard<std::mutex> lock(state->mutex);
    if (state->firstImage) {
        state->firstImage = false;
        state->firstImageTime = state->lastImageTime = timestamp;
        return env->NewDoubleArray(0);
    }
    const double imageDelta = timestamp - state->lastImageTime;
    if (imageDelta > 1.0 || timestamp < state->lastImageTime) {
        // feature_tracker_node.cpp resets only its timing/publication state here.
        // It deliberately preserves trackerData and init_pub across a restart.
        __android_log_print(ANDROID_LOG_WARN, "VINS-Bridge",
                "VINS_TRACKER_RESTART reason=%s delta=%.6f current=%.9f previous=%.9f",
                timestamp < state->lastImageTime ? "backward_timestamp" : "image_gap",
                imageDelta, timestamp, state->lastImageTime);
        state->firstImage = true;
        state->lastImageTime = 0.0;
        state->published = 1;
        state->restartPending = true;
        return env->NewDoubleArray(0);
    }
    state->lastImageTime = timestamp;
    PUB_THIS_FRAME = std::round(static_cast<double>(state->published)
            / (timestamp - state->firstImageTime)) <= FREQ;
    if (PUB_THIS_FRAME && std::abs(static_cast<double>(state->published)
            / (timestamp - state->firstImageTime) - FREQ) < 0.01 * FREQ) {
        state->firstImageTime = timestamp;
        state->published = 0;
    }

    jbyte* data = env->GetByteArrayElements(pixels, nullptr);
    // cv_bridge::toCvCopy() in the source node gives FeatureTracker an owning
    // cv::Mat. A Mat wrapped directly around GetByteArrayElements() is only
    // valid until ReleaseByteArrayElements(), while FeatureTracker keeps that
    // frame as cur_img for the next optical-flow call. Clone here to preserve
    // the source lifetime contract across the JNI boundary.
    cv::Mat imageView(height, width, CV_8UC1, data, stride);
    cv::Mat image = imageView.clone();
    state->tracker.readImage(image, timestamp);
    env->ReleaseByteArrayElements(pixels, data, JNI_ABORT);
    for (unsigned int index = 0;; ++index) {
        if (!state->tracker.updateID(index)) break;
    }
    if (!PUB_THIS_FRAME) return env->NewDoubleArray(0);
    state->published++;
    if (!state->initializedOutput) {
        state->initializedOutput = true;
        return env->NewDoubleArray(0);
    }

    std::vector<double> output;
    output.reserve(state->tracker.ids.size() * 8);
    std::unordered_set<int> publishedIds;
    int overlap = 0;
    for (std::size_t i = 0; i < state->tracker.ids.size(); ++i) {
        if (state->tracker.track_cnt[i] <= 1) continue;
        const int id = state->tracker.ids[i];
        publishedIds.insert(id);
        if (state->previousPublishedIds.count(id) != 0) overlap++;
        output.push_back(id);
        output.push_back(state->tracker.cur_un_pts[i].x);
        output.push_back(state->tracker.cur_un_pts[i].y);
        output.push_back(1.0);
        output.push_back(state->tracker.cur_pts[i].x);
        output.push_back(state->tracker.cur_pts[i].y);
        output.push_back(state->tracker.pts_velocity[i].x);
        output.push_back(state->tracker.pts_velocity[i].y);
    }
    state->diagnosticFrames++;
    if (state->diagnosticFrames == 1 || state->diagnosticFrames % 20 == 0) {
        __android_log_print(ANDROID_LOG_INFO, "VINS-Bridge",
                "tracker published=%zu overlap_previous=%d total_tracked=%zu",
                publishedIds.size(), overlap, state->tracker.ids.size());
    }
    state->previousPublishedIds = std::move(publishedIds);

    jdoubleArray result = env->NewDoubleArray(static_cast<jsize>(output.size()));
    env->SetDoubleArrayRegion(result, 0, static_cast<jsize>(output.size()), output.data());
    return result;
}
