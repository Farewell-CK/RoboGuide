# VINS-Mono Android boundary

This directory replaces only the original ROS process boundary:

- `ros_compat` maps ROS logging/assertions and `std_msgs::Header` timestamps to Android equivalents.
- D455F image and IMU measurements enter through JNI instead of ROS topics.
- Feature tracking and estimator remain separate native targets because the upstream ROS packages were separate processes and contain package-global parameters with identical names.

The algorithm sources under `third_party/vins_mono` are the copied eLabrador sources and remain the source of truth. ROS node files and visualization publishers are intentionally not compiled; their message transport role is implemented by the Android bridge.
