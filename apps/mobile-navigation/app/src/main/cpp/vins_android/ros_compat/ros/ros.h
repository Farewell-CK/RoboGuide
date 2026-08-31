#pragma once

#include <android/log.h>
#include <cassert>
#include <sstream>

namespace ros {
class NodeHandle {};
}

#define VINS_LOG(priority, ...) \
    __android_log_print(priority, "VINS-Mono", __VA_ARGS__)
#define ROS_DEBUG(...) do { } while (0)
#define ROS_INFO(...) VINS_LOG(ANDROID_LOG_INFO, __VA_ARGS__)
#define ROS_WARN(...) VINS_LOG(ANDROID_LOG_WARN, __VA_ARGS__)
#define ROS_ERROR(...) VINS_LOG(ANDROID_LOG_ERROR, __VA_ARGS__)
#define ROS_ASSERT(value) assert(value)
#define ROS_BREAK() assert(false)

#define VINS_STREAM_LOG(priority, expression) do { \
    std::ostringstream vins_log_stream; \
    vins_log_stream << expression; \
    __android_log_print(priority, "VINS-Mono", "%s", vins_log_stream.str().c_str()); \
} while (0)

#define ROS_DEBUG_STREAM(expression) do { } while (0)
#define ROS_INFO_STREAM(expression) VINS_STREAM_LOG(ANDROID_LOG_INFO, expression)
#define ROS_WARN_STREAM(expression) VINS_STREAM_LOG(ANDROID_LOG_WARN, expression)
#define ROS_ERROR_STREAM(expression) VINS_STREAM_LOG(ANDROID_LOG_ERROR, expression)
