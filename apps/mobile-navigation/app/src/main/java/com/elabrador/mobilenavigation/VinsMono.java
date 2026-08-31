package com.elabrador.mobilenavigation;

import com.intel.realsense.librealsense.Extrinsic;
import com.intel.realsense.librealsense.Intrinsic;

/** Owns the two native processes from the source VINS-Mono pipeline. */
final class VinsMono implements AutoCloseable {
    // VINS-Mono mode 0 fixes an accurate camera/IMU transform. D455 supplies
    // that rigid factory calibration through librealsense getExtrinsicTo().
    private static final int SOURCE_EXTRINSIC_MODE = 0;

    static final class TrackedFrame {
        final VinsInputBuffer.ImageSample image;
        final double[] features;
        final long generation;

        TrackedFrame(VinsInputBuffer.ImageSample image, double[] features, long generation) {
            this.image = image;
            this.features = features;
            this.generation = generation;
        }
    }

    static final class Pose {
        final double timestamp;
        final double x, y, z;
        final double qx, qy, qz, qw;
        final boolean initialized;
        private final double[] imuFromCameraRotation;
        private final double[] imuFromCameraTranslation;

        Pose(double[] value, double[] imuFromCameraRotation,
             double[] imuFromCameraTranslation) {
            x = value[0]; y = value[1]; z = value[2];
            qx = value[3]; qy = value[4]; qz = value[5]; qw = value[6];
            initialized = ((int) value[10]) == 1;
            timestamp = value[11];
            this.imuFromCameraRotation = imuFromCameraRotation;
            this.imuFromCameraTranslation = imuFromCameraTranslation;
        }

        /** Source relation: world_R_camera = world_R_imu * imu_R_camera. */
        float[] cameraToWorld() {
            double xx = qx * qx, yy = qy * qy, zz = qz * qz;
            double xy = qx * qy, xz = qx * qz, yz = qy * qz;
            double wx = qw * qx, wy = qw * qy, wz = qw * qz;
            double[] worldFromImu = {
                    1 - 2 * (yy + zz), 2 * (xy - wz), 2 * (xz + wy),
                    2 * (xy + wz), 1 - 2 * (xx + zz), 2 * (yz - wx),
                    2 * (xz - wy), 2 * (yz + wx), 1 - 2 * (xx + yy)
            };
            double[] worldFromCamera = multiply3x3(worldFromImu, imuFromCameraRotation);
            double cameraX = x + worldFromImu[0] * imuFromCameraTranslation[0]
                    + worldFromImu[1] * imuFromCameraTranslation[1]
                    + worldFromImu[2] * imuFromCameraTranslation[2];
            double cameraY = y + worldFromImu[3] * imuFromCameraTranslation[0]
                    + worldFromImu[4] * imuFromCameraTranslation[1]
                    + worldFromImu[5] * imuFromCameraTranslation[2];
            double cameraZ = z + worldFromImu[6] * imuFromCameraTranslation[0]
                    + worldFromImu[7] * imuFromCameraTranslation[1]
                    + worldFromImu[8] * imuFromCameraTranslation[2];
            return new float[] {
                    (float) worldFromCamera[0], (float) worldFromCamera[1],
                    (float) worldFromCamera[2], (float) cameraX,
                    (float) worldFromCamera[3], (float) worldFromCamera[4],
                    (float) worldFromCamera[5], (float) cameraY,
                    (float) worldFromCamera[6], (float) worldFromCamera[7],
                    (float) worldFromCamera[8], (float) cameraZ,
                    0f, 0f, 0f, 1f
            };
        }

        /** Source ego-map yaw is the world heading of camera +X (right), not IMU Z-yaw. */
        float egoRightAxisYawRadians() {
            float[] worldFromCamera = cameraToWorld();
            return (float) Math.atan2(worldFromCamera[4], worldFromCamera[0]);
        }

        static Pose interpolate(Pose first, Pose second, double timestamp) {
            if (first == null || second == null || !first.initialized || !second.initialized) {
                return null;
            }
            double duration = second.timestamp - first.timestamp;
            if (duration <= 0.0) return first;
            double alpha = Math.max(0.0, Math.min(1.0,
                    (timestamp - first.timestamp) / duration));
            double[] worldFromImu = slerp(
                    new double[]{first.qx, first.qy, first.qz, first.qw},
                    new double[]{second.qx, second.qy, second.qz, second.qw}, alpha);
            double[] firstExtrinsic = quaternionFromMatrix(first.imuFromCameraRotation);
            double[] secondExtrinsic = quaternionFromMatrix(second.imuFromCameraRotation);
            double[] extrinsic = matrixFromQuaternion(
                    slerp(firstExtrinsic, secondExtrinsic, alpha));
            double[] translation = new double[3];
            for (int i = 0; i < translation.length; i++) {
                translation[i] = lerp(first.imuFromCameraTranslation[i],
                        second.imuFromCameraTranslation[i], alpha);
            }
            double[] value = {
                    lerp(first.x, second.x, alpha),
                    lerp(first.y, second.y, alpha),
                    lerp(first.z, second.z, alpha),
                    worldFromImu[0], worldFromImu[1], worldFromImu[2], worldFromImu[3],
                    0.0, 0.0, 0.0, 1.0, timestamp
            };
            return new Pose(value, extrinsic, translation);
        }

        private static double lerp(double first, double second, double alpha) {
            return first + (second - first) * alpha;
        }

        private static double[] slerp(double[] first, double[] second, double alpha) {
            double dot = 0.0;
            for (int i = 0; i < 4; i++) dot += first[i] * second[i];
            double[] target = second.clone();
            if (dot < 0.0) {
                dot = -dot;
                for (int i = 0; i < 4; i++) target[i] = -target[i];
            }
            double[] result = new double[4];
            if (dot > 0.9995) {
                for (int i = 0; i < 4; i++) result[i] = lerp(first[i], target[i], alpha);
            } else {
                double theta = Math.acos(Math.max(-1.0, Math.min(1.0, dot)));
                double sinTheta = Math.sin(theta);
                double firstWeight = Math.sin((1.0 - alpha) * theta) / sinTheta;
                double secondWeight = Math.sin(alpha * theta) / sinTheta;
                for (int i = 0; i < 4; i++) {
                    result[i] = firstWeight * first[i] + secondWeight * target[i];
                }
            }
            double norm = Math.sqrt(result[0] * result[0] + result[1] * result[1]
                    + result[2] * result[2] + result[3] * result[3]);
            if (norm == 0.0) return new double[]{0.0, 0.0, 0.0, 1.0};
            for (int i = 0; i < 4; i++) result[i] /= norm;
            return result;
        }

        private static double[] quaternionFromMatrix(double[] matrix) {
            double x, y, z, w;
            double trace = matrix[0] + matrix[4] + matrix[8];
            if (trace > 0.0) {
                double scale = Math.sqrt(trace + 1.0) * 2.0;
                w = 0.25 * scale;
                x = (matrix[7] - matrix[5]) / scale;
                y = (matrix[2] - matrix[6]) / scale;
                z = (matrix[3] - matrix[1]) / scale;
            } else if (matrix[0] > matrix[4] && matrix[0] > matrix[8]) {
                double scale = Math.sqrt(1.0 + matrix[0] - matrix[4] - matrix[8]) * 2.0;
                w = (matrix[7] - matrix[5]) / scale;
                x = 0.25 * scale;
                y = (matrix[1] + matrix[3]) / scale;
                z = (matrix[2] + matrix[6]) / scale;
            } else if (matrix[4] > matrix[8]) {
                double scale = Math.sqrt(1.0 + matrix[4] - matrix[0] - matrix[8]) * 2.0;
                w = (matrix[2] - matrix[6]) / scale;
                x = (matrix[1] + matrix[3]) / scale;
                y = 0.25 * scale;
                z = (matrix[5] + matrix[7]) / scale;
            } else {
                double scale = Math.sqrt(1.0 + matrix[8] - matrix[0] - matrix[4]) * 2.0;
                w = (matrix[3] - matrix[1]) / scale;
                x = (matrix[2] + matrix[6]) / scale;
                y = (matrix[5] + matrix[7]) / scale;
                z = 0.25 * scale;
            }
            return new double[]{x, y, z, w};
        }

        private static double[] matrixFromQuaternion(double[] quaternion) {
            double x = quaternion[0], y = quaternion[1];
            double z = quaternion[2], w = quaternion[3];
            double xx = x * x, yy = y * y, zz = z * z;
            double xy = x * y, xz = x * z, yz = y * z;
            double wx = w * x, wy = w * y, wz = w * z;
            return new double[]{
                    1 - 2 * (yy + zz), 2 * (xy - wz), 2 * (xz + wy),
                    2 * (xy + wz), 1 - 2 * (xx + zz), 2 * (yz - wx),
                    2 * (xz - wy), 2 * (yz + wx), 1 - 2 * (xx + yy)
            };
        }

        private static double[] multiply3x3(double[] left, double[] right) {
            double[] result = new double[9];
            for (int row = 0; row < 3; row++) {
                for (int column = 0; column < 3; column++) {
                    for (int k = 0; k < 3; k++) {
                        result[row * 3 + column] += left[row * 3 + k] * right[k * 3 + column];
                    }
                }
            }
            return result;
        }
    }

    private long trackerHandle;
    private long estimatorHandle;
    private final Object trackerLock = new Object();
    private final Object estimatorLock = new Object();
    private boolean skippedFirstFeatureMessage;
    private volatile long generation;
    private boolean trackerRestartPending;
    private volatile double cachedTimeOffsetSeconds;

    VinsMono(Intrinsic colorIntrinsic, Extrinsic cameraToImu) {
        trackerHandle = NativeVinsFeatureTracker.nativeCreate(
                colorIntrinsic.getWidth(), colorIntrinsic.getHeight(),
                colorIntrinsic.getFx(), colorIntrinsic.getFy(),
                colorIntrinsic.getPpx(), colorIntrinsic.getPpy(),
                colorIntrinsic.getModel().value(), toDoubleArray(colorIntrinsic.getCoeffs()),
                150, 25, 1.0, false);
        float[] rotationColumnMajor = cameraToImu.getRotation();
        double[] rotationRowMajor = new double[9];
        for (int row = 0; row < 3; row++) {
            for (int column = 0; column < 3; column++) {
                rotationRowMajor[row * 3 + column] = rotationColumnMajor[column * 3 + row];
            }
        }
        float[] sourceTranslation = cameraToImu.getTranslation();
        double[] translation = {sourceTranslation[0], sourceTranslation[1], sourceTranslation[2]};
        estimatorHandle = NativeVinsEstimator.nativeCreate(
                colorIntrinsic.getWidth(), colorIntrinsic.getHeight(), 460.0,
                0.1, 0.01, 0.0002, 2.0e-5, 9.80665,
                0.04, 8, 10.0, true, 0.0, SOURCE_EXTRINSIC_MODE,
                rotationRowMajor, translation);
        if (trackerHandle == 0 || estimatorHandle == 0) {
            close();
            throw new IllegalStateException("Unable to create native VINS-Mono");
        }
    }

    private static double[] toDoubleArray(float[] values) {
        double[] result = new double[values.length];
        for (int i = 0; i < values.length; i++) result[i] = values[i];
        return result;
    }

    TrackedFrame track(VinsInputBuffer.ImageSample image) {
        boolean restart;
        double[] features;
        long frameGeneration;
        synchronized (trackerLock) {
            if (trackerHandle == 0 || image == null) return null;
            features = NativeVinsFeatureTracker.nativeTrack(
                    trackerHandle, image.grayscale, image.width, image.height,
                    image.stride, image.timestampSeconds);
            restart = NativeVinsFeatureTracker.nativeConsumeRestart(trackerHandle);
            if (restart) {
                trackerRestartPending = true;
                generation++;
            }
            if (restart || features.length == 0) {
                features = null;
            } else if (!skippedFirstFeatureMessage) {
                // Source feature_callback skips the first message because velocities are not initialized.
                skippedFirstFeatureMessage = true;
                features = null;
            }
            frameGeneration = generation;
        }
        if (restart) resetEstimatorOnly();
        return features == null ? null : new TrackedFrame(image, features, frameGeneration);
    }

    boolean consumeTrackerRestart() {
        synchronized (trackerLock) {
            boolean result = trackerRestartPending;
            trackerRestartPending = false;
            return result;
        }
    }

    Pose process(VinsInputBuffer input, TrackedFrame tracked) {
        synchronized (estimatorLock) {
            if (estimatorHandle == 0 || input == null || tracked == null
                    || tracked.generation != generation) return null;
            // Match estimator_node.cpp: getMeasurements() reads estimator.td and
            // consumes the corresponding IMU interval immediately before that
            // feature frame is processed. Do not slice IMU earlier on the tracker
            // thread because td is estimated online and may have changed meanwhile.
            VinsInputBuffer.Measurement measurement = input.awaitMeasurementForFeature(
                    tracked.image, cachedTimeOffsetSeconds);
            if (measurement == null) return null;
            double[] imu = new double[measurement.imu.size() * 7];
            for (int i = 0; i < measurement.imu.size(); i++) {
                VinsInputBuffer.UnifiedImuSample sample = measurement.imu.get(i);
                int offset = i * 7;
                imu[offset] = sample.timestampSeconds;
                imu[offset + 1] = sample.ax;
                imu[offset + 2] = sample.ay;
                imu[offset + 3] = sample.az;
                imu[offset + 4] = sample.gx;
                imu[offset + 5] = sample.gy;
                imu[offset + 6] = sample.gz;
            }
            if (!NativeVinsEstimator.nativeProcess(estimatorHandle,
                    tracked.image.timestampSeconds, imu, tracked.features)) return null;
            cachedTimeOffsetSeconds =
                    NativeVinsEstimator.nativeCurrentTimeOffset(estimatorHandle);
            return latestPoseLocked();
        }
    }

    double currentTimeOffsetSeconds() {
        // The source feature tracker and estimator are separate ROS nodes. Reading
        // td must not stall image tracking while the estimator is optimizing.
        return cachedTimeOffsetSeconds;
    }

    Pose latestPose() {
        synchronized (estimatorLock) {
            return latestPoseLocked();
        }
    }

    private Pose latestPoseLocked() {
        if (estimatorHandle == 0) return null;
        double[] value = NativeVinsEstimator.nativeLatestPose(estimatorHandle);
        if (value.length != 24) return null;
        double[] rotation = new double[9];
        double[] translation = new double[3];
        System.arraycopy(value, 12, rotation, 0, rotation.length);
        System.arraycopy(value, 21, translation, 0, translation.length);
        return new Pose(value, rotation, translation);
    }

    private void resetEstimatorOnly() {
        synchronized (estimatorLock) {
            if (estimatorHandle != 0) NativeVinsEstimator.nativeReset(estimatorHandle);
            cachedTimeOffsetSeconds = 0.0;
        }
    }

    void reset() {
        synchronized (trackerLock) {
            if (trackerHandle != 0) NativeVinsFeatureTracker.nativeReset(trackerHandle);
            skippedFirstFeatureMessage = false;
            trackerRestartPending = false;
            generation++;
        }
        resetEstimatorOnly();
    }

    @Override
    public void close() {
        synchronized (trackerLock) {
            if (trackerHandle != 0) {
                NativeVinsFeatureTracker.nativeDestroy(trackerHandle);
                trackerHandle = 0;
            }
        }
        synchronized (estimatorLock) {
            if (estimatorHandle != 0) {
                NativeVinsEstimator.nativeDestroy(estimatorHandle);
                estimatorHandle = 0;
            }
        }
    }
}
