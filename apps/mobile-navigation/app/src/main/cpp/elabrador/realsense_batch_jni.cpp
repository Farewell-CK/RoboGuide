#include <jni.h>

#include <cmath>
#include <algorithm>
#include <cstdint>
#include <vector>
#include <thread>
#include <memory>
#include <cstring>
#include <condition_variable>
#include <mutex>

// ABI declarations from librealsense2/rsutil.h. The implementation is not copied:
// every point is evaluated by the rs2_deproject_pixel_to_point symbol exported by
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
struct rs2_extrinsics { float rotation[9]; float translation[3]; };
void rs2_transform_point_to_point(float to[3], const rs2_extrinsics*, const float from[3]);
void rs2_project_point_to_pixel(float pixel[2], const rs2_intrinsics*, const float point[3]);
}

namespace {
constexpr int kAlignLanes = 3;
struct CachedAlign {
    rs2_intrinsics depth{}, color{};
    rs2_extrinsics transform{};
    std::vector<float> rays;
    std::vector<uint16_t> lanes[kAlignLanes];
    std::thread workers[kAlignLanes - 1];
    std::mutex mutex;
    std::condition_variable startCondition, doneCondition;
    bool stopping = false;
    uint64_t generation = 0;
    int completed = 0;
    const jbyte* source = nullptr;
    int sourceStride = 0;
    float sourceUnits = 0.0f;
    bool sourceCached = true;

    ~CachedAlign() {
        {
            std::lock_guard<std::mutex> lock(mutex);
            stopping = true;
            ++generation;
        }
        startCondition.notify_all();
        for (auto& worker : workers) if (worker.joinable()) worker.join();
    }
};

void alignLane(CachedAlign* state, int lane, const jbyte* source, int stride,
               float units, bool cached) {
    auto& target = state->lanes[lane];
    std::fill(target.begin(), target.end(), 0);
    for (int y = lane; y < state->depth.height; y += kAlignLanes) {
        for (int x = 0; x < state->depth.width; ++x) {
            int index = y * stride + x * 2;
            uint16_t value = static_cast<uint8_t>(source[index])
                    | (static_cast<uint8_t>(source[index + 1]) << 8);
            if (!value) continue;
            const float meters = units * value;
            int corners[4];
            bool valid = true;
            for (int corner = 0; corner < 2; ++corner) {
                const float* ray = state->rays.data()
                        + (y * state->depth.width + x) * 6 + corner * 3;
                float point[3] = {meters * ray[0], meters * ray[1], meters};
                if (!cached) {
                    float offset = corner ? 0.5f : -0.5f;
                    float originalPixel[2] = {x + offset, y + offset};
                    rs2_deproject_pixel_to_point(point, &state->depth, originalPixel, meters);
                }
                float transformed[3], pixel[2];
                rs2_transform_point_to_point(transformed, &state->transform, point);
                rs2_project_point_to_pixel(pixel, &state->color, transformed);
                if (!std::isfinite(pixel[0]) || !std::isfinite(pixel[1])
                        || std::abs(pixel[0]) > 1000000 || std::abs(pixel[1]) > 1000000) {
                    valid = false;
                    break;
                }
                corners[corner * 2] = static_cast<int>(pixel[0] + 0.5f);
                corners[corner * 2 + 1] = static_cast<int>(pixel[1] + 0.5f);
            }
            if (!valid || corners[0] < 0 || corners[1] < 0
                    || corners[2] >= state->color.width || corners[3] >= state->color.height) {
                continue;
            }
            for (int py = corners[1]; py <= corners[3]; ++py) {
                for (int px = corners[0]; px <= corners[2]; ++px) {
                    auto& cell = target[py * state->color.width + px];
                    cell = cell ? std::min(cell, value) : value;
                }
            }
        }
    }
}

bool readIntrinsics(JNIEnv* env, jfloatArray array, rs2_intrinsics& result) {
    if (!array || env->GetArrayLength(array) != 12) return false;
    float values[12];
    env->GetFloatArrayRegion(array, 0, 12, values);
    for (float value : values) if (!std::isfinite(value)) return false;
    result.width = static_cast<int>(values[0]); result.height = static_cast<int>(values[1]);
    result.ppx = values[2]; result.ppy = values[3];
    result.fx = values[4]; result.fy = values[5];
    result.model = static_cast<rs2_distortion>(static_cast<int>(values[6]));
    std::copy(values + 7, values + 12, result.coeffs);
    return result.width > 0 && result.width <= 4096 && result.height > 0
            && result.height <= 4096 && result.fx > 0 && result.fy > 0
            && result.model >= 0 && result.model < RS2_DISTORTION_COUNT
            && result.model != RS2_DISTORTION_MODIFIED_BROWN_CONRADY;
}
}

extern "C" JNIEXPORT jlong JNICALL
Java_com_elabrador_mobilenavigation_VerifiedDepthAligner_nativeCreate(
        JNIEnv* env, jclass, jfloatArray depth, jfloatArray color,
        jfloatArray rotation, jfloatArray translation) {
    auto state = std::unique_ptr<CachedAlign>(new CachedAlign());
    if (!readIntrinsics(env, depth, state->depth) || !readIntrinsics(env, color, state->color)
            || !rotation || env->GetArrayLength(rotation) != 9
            || !translation || env->GetArrayLength(translation) != 3) {
        return 0;
    }
    env->GetFloatArrayRegion(rotation, 0, 9, state->transform.rotation);
    env->GetFloatArrayRegion(translation, 0, 3, state->transform.translation);
    const int count = state->depth.width * state->depth.height;
    state->rays.resize(count * 6);
    for (int y = 0; y < state->depth.height; ++y) {
        for (int x = 0; x < state->depth.width; ++x) {
            for (int corner = 0; corner < 2; ++corner) {
                const float offset = corner ? 0.5f : -0.5f;
                const float pixel[2] = {x + offset, y + offset};
                // The SDK calculates distortion before multiplying by depth.
                rs2_deproject_pixel_to_point(state->rays.data() + (y * state->depth.width + x) * 6
                        + corner * 3, &state->depth, pixel, 1.0f);
            }
        }
    }
    for (auto& lane : state->lanes) lane.resize(state->color.width * state->color.height);
    for (int lane = 0; lane < kAlignLanes - 1; ++lane) {
        state->workers[lane] = std::thread([statePtr = state.get(), lane] {
            uint64_t observed = 0;
            while (true) {
                const jbyte* source;
                int stride;
                float units;
                bool cached;
                {
                    std::unique_lock<std::mutex> lock(statePtr->mutex);
                    statePtr->startCondition.wait(lock, [&] {
                        return statePtr->stopping || statePtr->generation != observed;
                    });
                    if (statePtr->stopping) return;
                    observed = statePtr->generation;
                    source = statePtr->source;
                    stride = statePtr->sourceStride;
                    units = statePtr->sourceUnits;
                    cached = statePtr->sourceCached;
                }
                alignLane(statePtr, lane, source, stride, units, cached);
                {
                    std::lock_guard<std::mutex> lock(statePtr->mutex);
                    ++statePtr->completed;
                }
                statePtr->doneCondition.notify_one();
            }
        });
    }
    return reinterpret_cast<jlong>(state.release());
}

extern "C" JNIEXPORT void JNICALL
Java_com_elabrador_mobilenavigation_VerifiedDepthAligner_nativeAlign(
        JNIEnv* env, jclass, jlong handle, jbyteArray depth, jint stride, jfloat units,
        jbyteArray output, jboolean cached) {
    auto* state = reinterpret_cast<CachedAlign*>(handle);
    if (!state || !depth || !output || stride < state->depth.width * 2
            || env->GetArrayLength(depth) < static_cast<int64_t>(stride) * state->depth.height
            || env->GetArrayLength(output) != state->color.width * state->color.height * 2
            || !(units > 0) || !std::isfinite(units)) {
        jclass cls = env->FindClass("java/lang/IllegalArgumentException");
        env->ThrowNew(cls, "Invalid cached alignment buffers");
        return;
    }
    jbyte* source = env->GetByteArrayElements(depth, nullptr);
    if (!source) return;
    {
        std::lock_guard<std::mutex> lock(state->mutex);
        state->source = source;
        state->sourceStride = stride;
        state->sourceUnits = units;
        state->sourceCached = cached;
        state->completed = 0;
        ++state->generation;
    }
    state->startCondition.notify_all();
    alignLane(state, kAlignLanes - 1, source, stride, units, cached);
    {
        std::unique_lock<std::mutex> lock(state->mutex);
        state->doneCondition.wait(lock, [&] { return state->completed == kAlignLanes - 1; });
    }
    env->ReleaseByteArrayElements(depth, source, JNI_ABORT);
    auto& merged = state->lanes[0];
    for (size_t i = 0; i < merged.size(); ++i) {
        for (int lane = 1; lane < kAlignLanes; ++lane) {
            uint16_t value = state->lanes[lane][i];
            if (value) merged[i] = merged[i] ? std::min(merged[i], value) : value;
        }
    }
    env->SetByteArrayRegion(output, 0, merged.size() * 2,
                           reinterpret_cast<const jbyte*>(merged.data()));
}

extern "C" JNIEXPORT void JNICALL
Java_com_elabrador_mobilenavigation_VerifiedDepthAligner_nativeDestroy(
        JNIEnv*, jclass, jlong handle) {
    delete reinterpret_cast<CachedAlign*>(handle);
}

namespace {
void throwIllegalArgument(JNIEnv* env, const char* message) {
    jclass cls = env->FindClass("java/lang/IllegalArgumentException");
    if (cls != nullptr) env->ThrowNew(cls, message);
}
}

extern "C" JNIEXPORT void JNICALL
Java_com_elabrador_mobilenavigation_NativeRealSense_nativeDeprojectPixelsCached(
        JNIEnv* env, jclass, jint width, jint height, jfloat ppx, jfloat ppy,
        jfloat fx, jfloat fy, jint distortionModel, jfloatArray coefficients,
        jfloatArray pixels, jfloatArray depths, jfloatArray xyz, jboolean cached) {
    if (coefficients == nullptr || pixels == nullptr || depths == nullptr || xyz == nullptr) {
        throwIllegalArgument(env, "RealSense deprojection arrays must not be null");
        return;
    }
    const jsize coefficientCount = env->GetArrayLength(coefficients);
    const jsize pixelValueCount = env->GetArrayLength(pixels);
    const jsize pointCount = env->GetArrayLength(depths);
    const jsize xyzValueCount = env->GetArrayLength(xyz);
    if (coefficientCount < 5 || pixelValueCount != pointCount * 2
            || xyzValueCount != pointCount * 3 || width <= 0 || height <= 0
            || !(fx > 0.0f) || !(fy > 0.0f)
            || distortionModel < RS2_DISTORTION_NONE
            || distortionModel >= RS2_DISTORTION_COUNT) {
        throwIllegalArgument(env, "Invalid RealSense intrinsics or batch array lengths");
        return;
    }

    rs2_intrinsics intrinsics{};
    intrinsics.width = width;
    intrinsics.height = height;
    intrinsics.ppx = ppx;
    intrinsics.ppy = ppy;
    intrinsics.fx = fx;
    intrinsics.fy = fy;
    intrinsics.model = static_cast<rs2_distortion>(distortionModel);
    env->GetFloatArrayRegion(coefficients, 0, 5, intrinsics.coeffs);
    if (env->ExceptionCheck()) return;

    jfloat* pixelValues = env->GetFloatArrayElements(pixels, nullptr);
    jfloat* depthValues = env->GetFloatArrayElements(depths, nullptr);
    jfloat* xyzValues = env->GetFloatArrayElements(xyz, nullptr);
    if (pixelValues == nullptr || depthValues == nullptr || xyzValues == nullptr) {
        if (pixelValues != nullptr) env->ReleaseFloatArrayElements(pixels, pixelValues, JNI_ABORT);
        if (depthValues != nullptr) env->ReleaseFloatArrayElements(depths, depthValues, JNI_ABORT);
        if (xyzValues != nullptr) env->ReleaseFloatArrayElements(xyz, xyzValues, 0);
        return;
    }

    struct RayCache {
        rs2_intrinsics calibration{};
        std::vector<float> rays;
        std::vector<uint8_t> valid;
    };
    thread_local RayCache cache;
    const bool cacheAllowed = cached && width <= 4096 && height <= 4096;
    if (cacheAllowed && (cache.valid.empty()
            || std::memcmp(&cache.calibration, &intrinsics, sizeof(intrinsics)) != 0)) {
        cache.calibration = intrinsics;
        cache.rays.resize(static_cast<size_t>(width) * height * 3);
        cache.valid.assign(static_cast<size_t>(width) * height, 0);
    }
    for (jsize index = 0; index < pointCount; ++index) {
        float x = pixelValues[index * 2], y = pixelValues[index * 2 + 1];
        if (cacheAllowed && x >= 0 && y >= 0 && x < width && y < height
                && x == std::floor(x) && y == std::floor(y)) {
            size_t pixel = static_cast<size_t>(y) * width + static_cast<size_t>(x);
            float* ray = cache.rays.data() + pixel * 3;
            if (!cache.valid[pixel]) {
                rs2_deproject_pixel_to_point(ray, &intrinsics, pixelValues + index * 2, 1.0f);
                cache.valid[pixel] = 1;
            }
            xyzValues[index * 3] = depthValues[index] * ray[0];
            xyzValues[index * 3 + 1] = depthValues[index] * ray[1];
            xyzValues[index * 3 + 2] = depthValues[index];
        } else {
            rs2_deproject_pixel_to_point(xyzValues + index * 3, &intrinsics,
                    pixelValues + index * 2, depthValues[index]);
        }
    }

    env->ReleaseFloatArrayElements(pixels, pixelValues, JNI_ABORT);
    env->ReleaseFloatArrayElements(depths, depthValues, JNI_ABORT);
    env->ReleaseFloatArrayElements(xyz, xyzValues, 0);
}

extern "C" JNIEXPORT void JNICALL
Java_com_elabrador_mobilenavigation_NativeRealSense_nativeDeprojectPixels(
        JNIEnv* env, jclass cls, jint width, jint height, jfloat ppx, jfloat ppy,
        jfloat fx, jfloat fy, jint distortionModel, jfloatArray coefficients,
        jfloatArray pixels, jfloatArray depths, jfloatArray xyz) {
    Java_com_elabrador_mobilenavigation_NativeRealSense_nativeDeprojectPixelsCached(
            env, cls, width, height, ppx, ppy, fx, fy, distortionModel, coefficients,
            pixels, depths, xyz, false);
}
