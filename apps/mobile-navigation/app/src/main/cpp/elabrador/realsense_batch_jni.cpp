#include <jni.h>

#include <cmath>

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
}

namespace {
void throwIllegalArgument(JNIEnv* env, const char* message) {
    jclass cls = env->FindClass("java/lang/IllegalArgumentException");
    if (cls != nullptr) env->ThrowNew(cls, message);
}
}

extern "C" JNIEXPORT void JNICALL
Java_com_elabrador_mobilenavigation_NativeRealSense_nativeDeprojectPixels(
        JNIEnv* env, jclass, jint width, jint height, jfloat ppx, jfloat ppy,
        jfloat fx, jfloat fy, jint distortionModel, jfloatArray coefficients,
        jfloatArray pixels, jfloatArray depths, jfloatArray xyz) {
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

    for (jsize index = 0; index < pointCount; ++index) {
        rs2_deproject_pixel_to_point(
                xyzValues + index * 3, &intrinsics,
                pixelValues + index * 2, depthValues[index]);
    }

    env->ReleaseFloatArrayElements(pixels, pixelValues, JNI_ABORT);
    env->ReleaseFloatArrayElements(depths, depthValues, JNI_ABORT);
    env->ReleaseFloatArrayElements(xyz, xyzValues, 0);
}
