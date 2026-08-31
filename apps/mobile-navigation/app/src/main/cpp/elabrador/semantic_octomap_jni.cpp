#include <jni.h>
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <limits>
#include <memory>
#include <vector>

#include <octomap/Pointcloud.h>
#include <semantics_octree/semantics_max.h>
#include <semantics_octree/semantics_octree.h>

namespace {
using Tree = octomap::SemanticsOcTree<octomap::SemanticsMax>;

struct MobileOctomap {
    Tree tree;
    float maxRange = 10.0f;
    float validMin = 0.2f;
    float validMax = 66.0f;
    float raycastRange = 10.0f;
    bool globalCrop = true;
    octomap::point3d lastOrigin;
    bool hasOrigin = false;

    explicit MobileOctomap(float resolution) : tree(resolution) {
        // Mirrors OctomapGeneratorNode::reset() and octomap_generator.yaml.
        tree.setResolution(resolution);
        tree.setClampingThresMin(0.12);
        tree.setClampingThresMax(0.97);
        tree.setOccupancyThres(0.5);
        tree.setProbHit(0.8);
        tree.setProbMiss(0.2);
    }
};

static void throwIllegalArgument(JNIEnv* env, const char* message) {
    jclass cls = env->FindClass("java/lang/IllegalArgumentException");
    if (cls) env->ThrowNew(cls, message);
}
}

extern "C" JNIEXPORT jlong JNICALL
Java_com_elabrador_mobilenavigation_NativeOctomap_nativeCreate(JNIEnv* env, jclass, jfloat resolution) {
    if (!(resolution > 0.0f)) {
        throwIllegalArgument(env, "OctoMap resolution must be positive");
        return 0;
    }
    return reinterpret_cast<jlong>(new MobileOctomap(resolution));
}

extern "C" JNIEXPORT void JNICALL
Java_com_elabrador_mobilenavigation_NativeOctomap_nativeDestroy(JNIEnv*, jclass, jlong handle) {
    delete reinterpret_cast<MobileOctomap*>(handle);
}

extern "C" JNIEXPORT void JNICALL
Java_com_elabrador_mobilenavigation_NativeOctomap_nativeClear(JNIEnv*, jclass, jlong handle) {
    auto* map = reinterpret_cast<MobileOctomap*>(handle);
    if (map) {
        map->tree.clear();
        map->hasOrigin = false;
    }
}

extern "C" JNIEXPORT jint JNICALL
Java_com_elabrador_mobilenavigation_NativeOctomap_nativeInsert(
        JNIEnv* env, jclass, jlong handle, jfloatArray xyz, jintArray semanticRgb,
        jfloatArray confidence, jfloatArray sensorToWorld) {
    auto* map = reinterpret_cast<MobileOctomap*>(handle);
    if (!map || !xyz || !semanticRgb || !confidence || !sensorToWorld) return 0;
    const jsize xyzSize = env->GetArrayLength(xyz);
    const jsize colorSize = env->GetArrayLength(semanticRgb);
    const jsize confidenceSize = env->GetArrayLength(confidence);
    const jsize transformSize = env->GetArrayLength(sensorToWorld);
    if (xyzSize % 3 != 0 || colorSize * 3 != xyzSize
            || confidenceSize != colorSize || transformSize != 16) {
        throwIllegalArgument(env, "OctoMap point arrays have inconsistent lengths");
        return 0;
    }
    jfloat* points = env->GetFloatArrayElements(xyz, nullptr);
    jint* colors = env->GetIntArrayElements(semanticRgb, nullptr);
    jfloat* confidences = env->GetFloatArrayElements(confidence, nullptr);
    jfloat* transform = env->GetFloatArrayElements(sensorToWorld, nullptr);
    const octomap::point3d origin(transform[3], transform[7], transform[11]);
    map->lastOrigin = origin;
    map->hasOrigin = true;
    octomap::Pointcloud endpoints;
    std::vector<std::size_t> valid;
    std::vector<octomap::point3d> worldPoints;
    valid.reserve(static_cast<std::size_t>(colorSize));
    worldPoints.reserve(static_cast<std::size_t>(colorSize));
    for (jsize i = 0; i < colorSize; ++i) {
        const float sensorX = points[i * 3];
        const float sensorY = points[i * 3 + 1];
        const float sensorZ = points[i * 3 + 2];
        const float x = transform[0] * sensorX + transform[1] * sensorY
                + transform[2] * sensorZ + transform[3];
        const float y = transform[4] * sensorX + transform[5] * sensorY
                + transform[6] * sensorZ + transform[7];
        const float z = transform[8] * sensorX + transform[9] * sensorY
                + transform[10] * sensorZ + transform[11];
        if (!std::isfinite(x) || !std::isfinite(y) || !std::isfinite(z)) continue;
        const float dx = x - origin.x(), dy = y - origin.y(), dz = z - origin.z();
        const float distance = std::sqrt(dx * dx + dy * dy + dz * dz);
        if (distance < map->validMin || distance > map->validMax) continue;
        if (distance <= map->maxRange) {
            endpoints.push_back(x, y, z);
        } else {
            const float scale = (map->maxRange + 1.0f) / distance;
            endpoints.push_back(origin.x() + dx * scale, origin.y() + dy * scale,
                                origin.z() + dz * scale);
        }
        valid.push_back(static_cast<std::size_t>(i));
        worldPoints.emplace_back(x, y, z);
    }
    if (endpoints.size() > 0) {
        map->tree.insertPointCloud(endpoints, origin, map->raycastRange, false, true);
    }
    for (std::size_t pointIndex = 0; pointIndex < valid.size(); ++pointIndex) {
        const std::size_t i = valid[pointIndex];
        const float x = worldPoints[pointIndex].x();
        const float y = worldPoints[pointIndex].y();
        const float z = worldPoints[pointIndex].z();
        const float dx = x - origin.x(), dy = y - origin.y(), dz = z - origin.z();
        const float distance = std::sqrt(dx * dx + dy * dy + dz * dz);
        if (distance < map->validMin || distance > map->maxRange) continue;
        const jint packed = colors[i];
        map->tree.averageNodeColor(x, y, z,
                                   static_cast<uint8_t>((packed >> 16) & 0xff),
                                   static_cast<uint8_t>((packed >> 8) & 0xff),
                                   static_cast<uint8_t>(packed & 0xff));
        octomap::SemanticsMax semantic;
        semantic.semantic_color = octomap::ColorOcTreeNode::Color(
                static_cast<uint8_t>((packed >> 16) & 0xff),
                static_cast<uint8_t>((packed >> 8) & 0xff),
                static_cast<uint8_t>(packed & 0xff));
        semantic.confidence = confidences[i];
        map->tree.updateNodeSemantics(x, y, z, semantic);
    }
    if (map->globalCrop) {
        std::vector<octomap::OcTreeKey> toRemove;
        for (Tree::leaf_iterator it = map->tree.begin_leafs(); it != map->tree.end_leafs(); ++it) {
            if (origin.distance(it.getCoordinate()) > map->maxRange) {
                toRemove.push_back(it.getKey());
            }
        }
        for (const octomap::OcTreeKey& key : toRemove) map->tree.deleteNode(key);
    }
    env->ReleaseFloatArrayElements(xyz, points, JNI_ABORT);
    env->ReleaseIntArrayElements(semanticRgb, colors, JNI_ABORT);
    env->ReleaseFloatArrayElements(confidence, confidences, JNI_ABORT);
    env->ReleaseFloatArrayElements(sensorToWorld, transform, JNI_ABORT);
    return static_cast<jint>(valid.size());
}

extern "C" JNIEXPORT jint JNICALL
Java_com_elabrador_mobilenavigation_NativeOctomap_nativeLeafCount(JNIEnv*, jclass, jlong handle) {
    auto* map = reinterpret_cast<MobileOctomap*>(handle);
    if (!map) return 0;
    return static_cast<jint>(map->tree.getNumLeafNodes());
}

extern "C" JNIEXPORT jfloatArray JNICALL
Java_com_elabrador_mobilenavigation_NativeOctomap_nativeExportLeafs(
        JNIEnv* env, jclass, jlong handle) {
    auto* map = reinterpret_cast<MobileOctomap*>(handle);
    if (!map) return env->NewFloatArray(0);
    // The source planner ignores free leaves (occupancy < 0.5). Exporting
    // them is unnecessary for the local cost map and creates huge Java
    // allocations on every semantic frame.
    const octomap::point3d bbxMin = map->hasOrigin
            ? octomap::point3d(map->lastOrigin.x() - 7.5f,
                               map->lastOrigin.y() - 7.5f,
                               map->lastOrigin.z() - 2.5f)
            : octomap::point3d(-7.5f, -7.5f, -2.5f);
    const octomap::point3d bbxMax = map->hasOrigin
            ? octomap::point3d(map->lastOrigin.x() + 7.5f,
                               map->lastOrigin.y() + 7.5f,
                               map->lastOrigin.z())
            : octomap::point3d(7.5f, 7.5f, 0.0f);
    std::size_t occupiedCount = 0;
    for (Tree::leaf_iterator it = map->tree.begin_leafs();
         it != map->tree.end_leafs(); ++it) {
        const octomap::point3d point = it.getCoordinate();
        if (point.x() < bbxMin.x() || point.x() > bbxMax.x()
                || point.y() < bbxMin.y() || point.y() > bbxMax.y()
                || point.z() < bbxMin.z() || point.z() > bbxMax.z()) continue;
        if (it->getOccupancy() >= 0.5f) ++occupiedCount;
    }
    const jsize count = static_cast<jsize>(std::min<std::size_t>(
            occupiedCount, static_cast<std::size_t>(std::numeric_limits<jsize>::max() / 8)));
    jfloatArray result = env->NewFloatArray(count * 8);
    if (!result) return nullptr;
    std::vector<float> data(static_cast<std::size_t>(count) * 8, 0.0f);
    std::size_t index = 0;
    for (Tree::leaf_iterator it = map->tree.begin_leafs();
         it != map->tree.end_leafs() && index < static_cast<std::size_t>(count); ++it) {
        const octomap::point3d point = it.getCoordinate();
        if (point.x() < bbxMin.x() || point.x() > bbxMax.x()
                || point.y() < bbxMin.y() || point.y() > bbxMax.y()
                || point.z() < bbxMin.z() || point.z() > bbxMax.z()) continue;
        if (it->getOccupancy() < 0.5f) continue;
        const octomap::SemanticsMax semantic = it->getSemantics();
        data[index * 8] = point.x();
        data[index * 8 + 1] = point.y();
        data[index * 8 + 2] = point.z();
        data[index * 8 + 3] = static_cast<float>(it->getOccupancy());
        data[index * 8 + 4] = semantic.semantic_color.r;
        data[index * 8 + 5] = semantic.semantic_color.g;
        data[index * 8 + 6] = semantic.semantic_color.b;
        data[index * 8 + 7] = semantic.confidence;
        ++index;
    }
    env->SetFloatArrayRegion(result, 0, static_cast<jsize>(data.size()), data.data());
    return result;
}
