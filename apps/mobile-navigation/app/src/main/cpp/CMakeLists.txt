cmake_minimum_required(VERSION 3.22.1)
project(elabrador_native LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 14)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
option(ELABRADOR_OPTIMIZED_NATIVE "Optimize native mapping in the fast flavor" OFF)

# The upstream OctoMap sources are vendored from the same dependency used by
# edge. Only the two runtime libraries are built; command-line tools are not
# part of the Android target.
set(OCTOMAP_ROOT ${CMAKE_CURRENT_LIST_DIR}/../../../../third_party/octomap/octomap)

add_library(octomath STATIC
    ${OCTOMAP_ROOT}/src/math/Pose6D.cpp
    ${OCTOMAP_ROOT}/src/math/Quaternion.cpp
    ${OCTOMAP_ROOT}/src/math/Vector3.cpp
)
target_include_directories(octomath PUBLIC ${OCTOMAP_ROOT}/include)

add_library(octomap STATIC
    ${OCTOMAP_ROOT}/src/AbstractOcTree.cpp
    ${OCTOMAP_ROOT}/src/AbstractOccupancyOcTree.cpp
    ${OCTOMAP_ROOT}/src/Pointcloud.cpp
    ${OCTOMAP_ROOT}/src/ScanGraph.cpp
    ${OCTOMAP_ROOT}/src/CountingOcTree.cpp
    ${OCTOMAP_ROOT}/src/OcTree.cpp
    ${OCTOMAP_ROOT}/src/OcTreeNode.cpp
    ${OCTOMAP_ROOT}/src/OcTreeStamped.cpp
    ${OCTOMAP_ROOT}/src/ColorOcTree.cpp
)
target_include_directories(octomap PUBLIC ${OCTOMAP_ROOT}/include)
target_link_libraries(octomap PUBLIC octomath)

add_library(elabrador_native SHARED
    elabrador/semantics_max.cpp
    elabrador/semantic_octomap_jni.cpp
    elabrador/realsense_batch_jni.cpp
    elabrador/neuron_probe_jni.cpp
    elabrador/neuron_shim_jni.cpp
)
target_include_directories(elabrador_native PRIVATE
    ${OCTOMAP_ROOT}/include
    ${CMAKE_CURRENT_LIST_DIR}/elabrador
)
add_library(realsense2 SHARED IMPORTED)
set_target_properties(realsense2 PROPERTIES IMPORTED_LOCATION
    ${CMAKE_CURRENT_LIST_DIR}/../../../../librealsense/src/main/jniLibs/${ANDROID_ABI}/librealsense2.so)
target_link_libraries(elabrador_native PRIVATE octomap octomath realsense2 log)

if(ELABRADOR_OPTIMIZED_NATIVE)
    target_compile_options(octomath PRIVATE -O3)
    target_compile_options(octomap PRIVATE -O3)
    target_compile_options(elabrador_native PRIVATE -O3)
endif()

# Full VINS-Mono port. The two shared libraries preserve the original ROS
# process boundary and therefore keep each package's global parameter symbols
# isolated exactly as they were on Orin.
set(VINS_ROOT ${CMAKE_CURRENT_LIST_DIR}/../../../../third_party/vins_mono)
set(VINS_DEPS ${CMAKE_CURRENT_LIST_DIR}/../../../../third_party/vins_android_deps)
set(OPENCV_ANDROID ${VINS_DEPS}/opencv/sdk/native/jni)
set(EIGEN_ROOT ${VINS_DEPS}/eigen3)
set(BOOST_ROOT ${VINS_DEPS}/boost-header-stage/boost_1_69_0)
set(CERES_ANDROID ${VINS_DEPS}/ceres/${ANDROID_ABI})

if(EXISTS ${OPENCV_ANDROID}/OpenCVConfig.cmake
        AND EXISTS ${EIGEN_ROOT}/Eigen/Core
        AND EXISTS ${BOOST_ROOT}/boost/shared_ptr.hpp
        AND EXISTS ${CERES_ANDROID}/lib/cmake/Ceres/CeresConfig.cmake)
    set(OpenCV_DIR ${OPENCV_ANDROID})
    set(Ceres_DIR ${CERES_ANDROID}/lib/cmake/Ceres)
    set(EIGEN_INCLUDE_DIR ${EIGEN_ROOT})
    find_package(OpenCV REQUIRED COMPONENTS core imgproc calib3d video features2d highgui)
    find_package(Ceres REQUIRED)

    add_library(vins_openmp STATIC IMPORTED)
    set_target_properties(vins_openmp PROPERTIES IMPORTED_LOCATION
        ${CMAKE_ANDROID_NDK}/toolchains/llvm/prebuilt/windows-x86_64/lib/clang/18/lib/linux/aarch64/libomp.a)

    set(VINS_COMMON_INCLUDES
        ${VINS_ROOT}/camera_model/include
        ${VINS_ROOT}/feature_tracker/src
        ${VINS_ROOT}/vins_estimator/src
        ${VINS_DEPS}
        ${EIGEN_ROOT}
        ${BOOST_ROOT}
        ${CMAKE_CURRENT_LIST_DIR}/vins_android/ros_compat
        ${OpenCV_INCLUDE_DIRS}
        ${CERES_INCLUDE_DIRS}
    )

    add_library(vins_camera_model STATIC
        ${VINS_ROOT}/camera_model/src/camera_models/Camera.cc
        ${VINS_ROOT}/camera_model/src/camera_models/CameraFactory.cc
        ${VINS_ROOT}/camera_model/src/camera_models/CostFunctionFactory.cc
        ${VINS_ROOT}/camera_model/src/camera_models/PinholeCamera.cc
        ${VINS_ROOT}/camera_model/src/camera_models/CataCamera.cc
        ${VINS_ROOT}/camera_model/src/camera_models/EquidistantCamera.cc
        ${VINS_ROOT}/camera_model/src/camera_models/ScaramuzzaCamera.cc
        ${VINS_ROOT}/camera_model/src/gpl/gpl.cc
        ${VINS_ROOT}/camera_model/src/gpl/EigenQuaternionParameterization.cc
    )
    target_compile_options(vins_camera_model PRIVATE
        -O3
        -include ${CMAKE_CURRENT_LIST_DIR}/vins_android/platform_compat.h)
    target_include_directories(vins_camera_model PUBLIC ${VINS_COMMON_INCLUDES})
    target_link_libraries(vins_camera_model PUBLIC ${OpenCV_LIBS} ${CERES_LIBRARIES})

    add_library(vins_feature_tracker SHARED
        ${CMAKE_CURRENT_LIST_DIR}/vins_android/feature_tracker_bridge.cpp
        ${VINS_ROOT}/feature_tracker/src/feature_tracker.cpp
    )
    target_compile_options(vins_feature_tracker PRIVATE
        -O3
        -include ${CMAKE_CURRENT_LIST_DIR}/vins_android/platform_compat.h)
    target_include_directories(vins_feature_tracker PRIVATE ${VINS_COMMON_INCLUDES})
    target_link_libraries(vins_feature_tracker PRIVATE
        vins_camera_model ${OpenCV_LIBS} ${CERES_LIBRARIES} realsense2 log)

    add_library(vins_estimator SHARED
        ${CMAKE_CURRENT_LIST_DIR}/vins_android/estimator_bridge.cpp
        ${VINS_ROOT}/vins_estimator/src/estimator.cpp
        ${VINS_ROOT}/vins_estimator/src/feature_manager.cpp
        ${VINS_ROOT}/vins_estimator/src/factor/pose_local_parameterization.cpp
        ${VINS_ROOT}/vins_estimator/src/factor/projection_factor.cpp
        ${VINS_ROOT}/vins_estimator/src/factor/projection_td_factor.cpp
        ${VINS_ROOT}/vins_estimator/src/factor/marginalization_factor.cpp
        ${VINS_ROOT}/vins_estimator/src/utility/utility.cpp
        ${VINS_ROOT}/vins_estimator/src/initial/solve_5pts.cpp
        ${VINS_ROOT}/vins_estimator/src/initial/initial_aligment.cpp
        ${VINS_ROOT}/vins_estimator/src/initial/initial_sfm.cpp
        ${VINS_ROOT}/vins_estimator/src/initial/initial_ex_rotation.cpp
    )
    target_compile_options(vins_estimator PRIVATE
        -O3
        -include ${CMAKE_CURRENT_LIST_DIR}/vins_android/platform_compat.h)
    target_compile_definitions(vins_estimator PRIVATE EIGEN_DONT_PARALLELIZE)
    target_include_directories(vins_estimator PRIVATE ${VINS_COMMON_INCLUDES})
    target_link_libraries(vins_estimator PRIVATE
        ${OpenCV_LIBS} ${CERES_LIBRARIES} vins_openmp log)
else()
    message(STATUS "VINS-Mono dependencies not staged; OctoMap-only native target remains buildable")
endif()
