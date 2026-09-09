#include <jni.h>
#include <android/log.h>

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <map>
#include <new>
#include <mutex>
#include <vector>

#include "estimator.h"

double INIT_DEPTH = 5.0;
double MIN_PARALLAX = 10.0 / FOCAL_LENGTH;
double ACC_N = 0.1, ACC_W = 0.0002;
double GYR_N = 0.01, GYR_W = 2.0e-5;
std::vector<Eigen::Matrix3d> RIC;
std::vector<Eigen::Vector3d> TIC;
Eigen::Vector3d G{0.0, 0.0, 9.80665};
double BIAS_ACC_THRESHOLD = 0.1;
double BIAS_GYR_THRESHOLD = 0.1;
double SOLVER_TIME = 0.04;
int NUM_ITERATIONS = 8;
int ESTIMATE_EXTRINSIC = 2;
int ESTIMATE_TD = 1;
// Keep the source realsense_color_config.yaml camera model. The D455F stream
// used by this pipeline is configured as global shutter, so no row-time offset
// may be injected into visual residuals.
int ROLLING_SHUTTER = 0;
std::string EX_CALIB_RESULT_PATH;
std::string VINS_RESULT_PATH;
std::string IMU_TOPIC;
double ROW = 480.0, COL = 640.0;
double TD = 0.0, TR = 0.0;

namespace {
struct AndroidEstimator {
    Estimator estimator;
    std::mutex mutex;
    double currentTime = -1.0;
    double latestImageTime = -1.0;
    int diagnosticFrames = 0;
};

AndroidEstimator* fromHandle(jlong handle) {
    return reinterpret_cast<AndroidEstimator*>(handle);
}

void resetRuntimeState(AndroidEstimator& state) {
    state.currentTime = -1.0;
    state.latestImageTime = -1.0;
    state.diagnosticFrames = 0;
}

void configureExtrinsics(JNIEnv* env, jint extrinsicMode,
                         jdoubleArray rotation, jdoubleArray translation) {
    RIC.clear();
    TIC.clear();
    Eigen::Matrix3d r = Eigen::Matrix3d::Identity();
    Eigen::Vector3d t = Eigen::Vector3d::Zero();
    // parameters.cpp deliberately ignores the configured R/T in mode 2. The
    // online calibration must start from identity rotation and zero translation.
    if (extrinsicMode != 2 && rotation && env->GetArrayLength(rotation) == 9) {
        jdouble values[9];
        env->GetDoubleArrayRegion(rotation, 0, 9, values);
        for (int row = 0; row < 3; ++row)
            for (int col = 0; col < 3; ++col)
                r(row, col) = values[row * 3 + col];
    }
    if (extrinsicMode != 2 && translation && env->GetArrayLength(translation) == 3) {
        jdouble values[3];
        env->GetDoubleArrayRegion(translation, 0, 3, values);
        t = Eigen::Vector3d(values[0], values[1], values[2]);
    }
    RIC.push_back(r);
    TIC.push_back(t);
}
}  // namespace

extern "C" JNIEXPORT jlong JNICALL
Java_com_elabrador_mobilenavigation_NativeVinsEstimator_nativeCreate(
        JNIEnv* env, jclass, jint width, jint height, jdouble focalLength,
        jdouble accNoise, jdouble gyroNoise, jdouble accRandomWalk,
        jdouble gyroRandomWalk, jdouble gravity, jdouble solverTime,
        jint iterations, jdouble keyframeParallax, jboolean estimateTimeOffset,
        jdouble timeOffset, jint extrinsicMode, jdoubleArray rotation,
        jdoubleArray translation) {
    ROW = height;
    COL = width;
    ACC_N = accNoise;
    GYR_N = gyroNoise;
    ACC_W = accRandomWalk;
    GYR_W = gyroRandomWalk;
    G = Eigen::Vector3d(0.0, 0.0, gravity);
    SOLVER_TIME = solverTime;
    NUM_ITERATIONS = iterations;
    MIN_PARALLAX = keyframeParallax / focalLength;
    ESTIMATE_TD = estimateTimeOffset ? 1 : 0;
    TD = timeOffset;
    ESTIMATE_EXTRINSIC = extrinsicMode;
    configureExtrinsics(env, extrinsicMode, rotation, translation);
    __android_log_print(ANDROID_LOG_INFO, "VINS-Bridge",
            "estimator extrinsic mode=%d rolling_shutter=%d tr=%.3f "
            "initial_R=[%.6f %.6f %.6f; %.6f %.6f %.6f; %.6f %.6f %.6f] initial_T=[%.6f %.6f %.6f]",
            extrinsicMode,
            ROLLING_SHUTTER, TR,
            RIC[0](0, 0), RIC[0](0, 1), RIC[0](0, 2),
            RIC[0](1, 0), RIC[0](1, 1), RIC[0](1, 2),
            RIC[0](2, 0), RIC[0](2, 1), RIC[0](2, 2),
            TIC[0].x(), TIC[0].y(), TIC[0].z());
    // The ROS node owns Estimator as a static object, so its storage is zeroed
    // before the constructor calls clearState(). Preserve that exact lifetime
    // semantic for pointer members such as pre_integrations.
    void* storage = std::calloc(1, sizeof(AndroidEstimator));
    if (!storage) return 0;
    auto* state = new(storage) AndroidEstimator();
    state->estimator.setParameter();
    return reinterpret_cast<jlong>(state);
}

extern "C" JNIEXPORT void JNICALL
Java_com_elabrador_mobilenavigation_NativeVinsEstimator_nativeDestroy(
        JNIEnv*, jclass, jlong handle) {
    auto* state = fromHandle(handle);
    if (!state) return;
    state->~AndroidEstimator();
    std::free(state);
}

extern "C" JNIEXPORT void JNICALL
Java_com_elabrador_mobilenavigation_NativeVinsEstimator_nativeReset(
        JNIEnv*, jclass, jlong handle) {
    auto* state = fromHandle(handle);
    if (!state) return;
    std::lock_guard<std::mutex> lock(state->mutex);
    state->estimator.clearState();
    state->estimator.setParameter();
    resetRuntimeState(*state);
}

extern "C" JNIEXPORT jboolean JNICALL
Java_com_elabrador_mobilenavigation_NativeVinsEstimator_nativeProcess(
        JNIEnv* env, jclass, jlong handle, jdouble imageTimestamp,
        jdoubleArray imuArray, jdoubleArray featureArray) {
    auto* state = fromHandle(handle);
    if (!state || !imuArray || !featureArray) return JNI_FALSE;
    const jsize imuLength = env->GetArrayLength(imuArray);
    const jsize featureLength = env->GetArrayLength(featureArray);
    if (imuLength % 7 != 0 || featureLength % 8 != 0) return JNI_FALSE;
    std::vector<double> imu(static_cast<std::size_t>(imuLength));
    std::vector<double> features(static_cast<std::size_t>(featureLength));
    env->GetDoubleArrayRegion(imuArray, 0, imuLength, imu.data());
    env->GetDoubleArrayRegion(featureArray, 0, featureLength, features.data());

    state->diagnosticFrames++;
    double accelNormSum = 0.0;
    double accelNormSquaredSum = 0.0;
    double gyroNormSum = 0.0;
    const int imuSamples = imuLength / 7;
    for (std::size_t i = 0; i < imu.size(); i += 7) {
        const double accelNorm = std::sqrt(imu[i + 1] * imu[i + 1]
                + imu[i + 2] * imu[i + 2] + imu[i + 3] * imu[i + 3]);
        accelNormSum += accelNorm;
        accelNormSquaredSum += accelNorm * accelNorm;
        gyroNormSum += std::sqrt(imu[i + 4] * imu[i + 4]
                + imu[i + 5] * imu[i + 5] + imu[i + 6] * imu[i + 6]);
    }
    const double averageAccelNorm = imuSamples > 0 ? accelNormSum / imuSamples : 0.0;
    const double averageGyroNorm = imuSamples > 0 ? gyroNormSum / imuSamples : 0.0;
    const double accelNormVariance = imuSamples > 0
            ? std::max(0.0, accelNormSquaredSum / imuSamples
                    - averageAccelNorm * averageAccelNorm)
            : 0.0;
    const double accelNormStdDev = std::sqrt(accelNormVariance);
    double featureSpeedSum = 0.0;
    const int featureSamples = featureLength / 8;
    for (std::size_t i = 0; i < features.size(); i += 8) {
        featureSpeedSum += std::sqrt(features[i + 6] * features[i + 6]
                + features[i + 7] * features[i + 7]);
    }
    const double averageFeatureSpeed = featureSamples > 0
            ? featureSpeedSum / featureSamples : 0.0;
    if (state->diagnosticFrames == 1 || state->diagnosticFrames % 20 == 0) {
        const double imuSpan = imuLength >= 14 ? imu[imu.size() - 7] - imu[0] : 0.0;
        double minimumDt = 1.0e9;
        double maximumDt = 0.0;
        for (std::size_t i = 0; i < imu.size(); i += 7) {
            if (i >= 7) {
                const double dt = imu[i] - imu[i - 7];
                minimumDt = std::min(minimumDt, dt);
                maximumDt = std::max(maximumDt, dt);
            }
        }
        if (imuSamples < 2) minimumDt = 0.0;
        __android_log_print(ANDROID_LOG_INFO, "VINS-Bridge",
                "estimator features=%d feature_speed=%.6f imu_samples=%d imu_span=%.6f imu_dt=[%.6f,%.6f] accel_norm=%.6f accel_std=%.6f gyro_norm=%.6f image_time=%.6f",
                featureSamples, averageFeatureSpeed, imuSamples, imuSpan,
                minimumDt, maximumDt,
                averageAccelNorm, accelNormStdDev, averageGyroNorm, imageTimestamp);
    }

    std::lock_guard<std::mutex> lock(state->mutex);
    if (state->latestImageTime >= 0.0 && imageTimestamp <= state->latestImageTime) return JNI_FALSE;
    state->latestImageTime = imageTimestamp;
    double ax = 0, ay = 0, az = 0, gx = 0, gy = 0, gz = 0;
    for (std::size_t i = 0; i < imu.size(); i += 7) {
        const double t = imu[i];
        const double imageTime = imageTimestamp + state->estimator.td;
        if (t <= imageTime) {
            if (state->currentTime < 0.0) state->currentTime = t;
            const double dt = t - state->currentTime;
            if (dt < 0.0) return JNI_FALSE;
            state->currentTime = t;
            ax = imu[i + 1]; ay = imu[i + 2]; az = imu[i + 3];
            gx = imu[i + 4]; gy = imu[i + 5]; gz = imu[i + 6];
            state->estimator.processIMU(dt, Eigen::Vector3d(ax, ay, az),
                                       Eigen::Vector3d(gx, gy, gz));
        } else {
            const double dt1 = imageTime - state->currentTime;
            const double dt2 = t - imageTime;
            if (dt1 < 0.0 || dt2 < 0.0 || dt1 + dt2 <= 0.0) return JNI_FALSE;
            const double w1 = dt2 / (dt1 + dt2);
            const double w2 = dt1 / (dt1 + dt2);
            ax = w1 * ax + w2 * imu[i + 1];
            ay = w1 * ay + w2 * imu[i + 2];
            az = w1 * az + w2 * imu[i + 3];
            gx = w1 * gx + w2 * imu[i + 4];
            gy = w1 * gy + w2 * imu[i + 5];
            gz = w1 * gz + w2 * imu[i + 6];
            state->currentTime = imageTime;
            state->estimator.processIMU(dt1, Eigen::Vector3d(ax, ay, az),
                                        Eigen::Vector3d(gx, gy, gz));
            break;
        }
    }

    std::map<int, std::vector<std::pair<int, Eigen::Matrix<double, 7, 1>>>> image;
    for (std::size_t i = 0; i < features.size(); i += 8) {
        Eigen::Matrix<double, 7, 1> value;
        value << features[i + 1], features[i + 2], features[i + 3],
                 features[i + 4], features[i + 5], features[i + 6], features[i + 7];
        image[static_cast<int>(features[i])].emplace_back(0, value);
    }
    std_msgs::Header header;
    header.stamp.seconds = imageTimestamp;
    header.frame_id = "world";
    state->estimator.processImage(image, header);

    if (state->estimator.solver_flag == Estimator::NON_LINEAR
            && (state->diagnosticFrames % 10) == 0) {
        const int index = WINDOW_SIZE;
        const Eigen::Vector3d& position = state->estimator.Ps[index];
        const Eigen::Vector3d& velocity = state->estimator.Vs[index];
        const Eigen::Vector3d& accelBias = state->estimator.Bas[index];
        const Eigen::Vector3d& gyroBias = state->estimator.Bgs[index];
        __android_log_print(ANDROID_LOG_INFO, "VINS-Bridge",
                "state P=(%.6f,%.6f,%.6f) V=(%.6f,%.6f,%.6f) "
                "Ba=(%.6f,%.6f,%.6f) Bg=(%.6f,%.6f,%.6f) td=%.6f tracks=%d",
                position.x(), position.y(), position.z(),
                velocity.x(), velocity.y(), velocity.z(),
                accelBias.x(), accelBias.y(), accelBias.z(),
                gyroBias.x(), gyroBias.y(), gyroBias.z(),
                state->estimator.td, state->estimator.f_manager.last_track_num);
    }
    return JNI_TRUE;
}

extern "C" JNIEXPORT jdouble JNICALL
Java_com_elabrador_mobilenavigation_NativeVinsEstimator_nativeCurrentTimeOffset(
        JNIEnv*, jclass, jlong handle) {
    auto* state = fromHandle(handle);
    if (!state) return 0.0;
    std::lock_guard<std::mutex> lock(state->mutex);
    return state->estimator.td;
}

extern "C" JNIEXPORT jdoubleArray JNICALL
Java_com_elabrador_mobilenavigation_NativeVinsEstimator_nativeLatestPose(
        JNIEnv* env, jclass, jlong handle) {
    auto* state = fromHandle(handle);
    if (!state) return env->NewDoubleArray(0);
    std::lock_guard<std::mutex> lock(state->mutex);
    const int index = WINDOW_SIZE;
    Eigen::Quaterniond q(state->estimator.Rs[index]);
    const Eigen::Vector3d& p = state->estimator.Ps[index];
    const Eigen::Vector3d& v = state->estimator.Vs[index];
    const Eigen::Matrix3d& ric = state->estimator.ric[0];
    const Eigen::Vector3d& tic = state->estimator.tic[0];
    double output[24] = {p.x(), p.y(), p.z(), q.x(), q.y(), q.z(), q.w(),
                         v.x(), v.y(), v.z(),
                         static_cast<double>(state->estimator.solver_flag),
                         state->latestImageTime,
                         ric(0, 0), ric(0, 1), ric(0, 2),
                         ric(1, 0), ric(1, 1), ric(1, 2),
                         ric(2, 0), ric(2, 1), ric(2, 2),
                         tic.x(), tic.y(), tic.z()};
    jdoubleArray result = env->NewDoubleArray(24);
    env->SetDoubleArrayRegion(result, 0, 24, output);
    return result;
}
