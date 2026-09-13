package com.elabrador.mobilenavigation;

import com.intel.realsense.librealsense.Intrinsic;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;

/** Timestamp synchronization boundary replacing the original ROS topic queues. */
final class VinsInputBuffer {
    static final class ImageSample {
        final double timestampSeconds;
        final byte[] grayscale;
        final int width;
        final int height;
        final int stride;
        final Intrinsic intrinsic;

        ImageSample(double timestampSeconds, byte[] grayscale, int width, int height,
                    int stride, Intrinsic intrinsic) {
            this.timestampSeconds = timestampSeconds;
            this.grayscale = grayscale;
            this.width = width;
            this.height = height;
            this.stride = stride;
            this.intrinsic = intrinsic;
        }
    }

    static final class ImuSample {
        final double timestampSeconds;
        final float x;
        final float y;
        final float z;

        ImuSample(double timestampSeconds, float x, float y, float z) {
            this.timestampSeconds = timestampSeconds;
            this.x = x;
            this.y = y;
            this.z = z;
        }
    }

    static final class UnifiedImuSample {
        final double timestampSeconds;
        final float ax, ay, az;
        final float gx, gy, gz;

        UnifiedImuSample(double timestampSeconds, float ax, float ay, float az,
                         float gx, float gy, float gz) {
            this.timestampSeconds = timestampSeconds;
            this.ax = ax;
            this.ay = ay;
            this.az = az;
            this.gx = gx;
            this.gy = gy;
            this.gz = gz;
        }
    }

    static final class Measurement {
        final ImageSample image;
        final ArrayList<UnifiedImuSample> imu;

        Measurement(ImageSample image, ArrayList<UnifiedImuSample> imu) {
            this.image = image;
            this.imu = imu;
        }
    }

    static final class Status {
        final long images, gyroscope, accelerometer, unifiedImu, pairedImages;
        final long droppedImages;
        final int queuedImages;

        Status(long images, long gyroscope, long accelerometer,
               long unifiedImu, long pairedImages, long droppedImages,
               int queuedImages) {
            this.images = images;
            this.gyroscope = gyroscope;
            this.accelerometer = accelerometer;
            this.unifiedImu = unifiedImu;
            this.pairedImages = pairedImages;
            this.droppedImages = droppedImages;
            this.queuedImages = queuedImages;
        }

        boolean ready() {
            return images > 0 && unifiedImu > 0;
        }
    }

    // The source ROS pipeline uses deep subscriber queues. Thirty-two VGA frames
    // retain just over one second at 30 fps and use about 9.4 MiB, preventing short
    // GC/render stalls from becoming artificial tracker timestamp discontinuities.
    private static final int MAX_IMAGES = 32;
    private static final int MAX_IMU_SAMPLES = 2048;
    private static final long FEATURE_IMU_WAIT_MILLIS = 250L;
    private final Deque<ImageSample> images = new ArrayDeque<>();
    private final Deque<ImuSample> gyroscope = new ArrayDeque<>();
    private final Deque<ImuSample> accelerometer = new ArrayDeque<>();
    private final Deque<UnifiedImuSample> unifiedImu = new ArrayDeque<>();
    private long imageCount, gyroCount, accelCount, unifiedCount, pairedImageCount;
    private long droppedImageCount;
    private double lastUnifiedTimestamp = Double.NEGATIVE_INFINITY;

    synchronized void addImage(double timestampMilliseconds, byte[] rgb, int width,
                               int height, int stride, Intrinsic intrinsic) {
        byte[] grayscale = new byte[width * height];
        for (int y = 0; y < height; y++) {
            int sourceRow = y * stride;
            int targetRow = y * width;
            for (int x = 0; x < width; x++) {
                int source = sourceRow + x * 3;
                int red = rgb[source] & 0xff;
                int green = rgb[source + 1] & 0xff;
                int blue = rgb[source + 2] & 0xff;
                grayscale[targetRow + x] = (byte) ((77 * red + 150 * green + 29 * blue) >> 8);
            }
        }
        images.addLast(new ImageSample(timestampMilliseconds / 1000.0,
                grayscale, width, height, width, intrinsic));
        while (images.size() > MAX_IMAGES) {
            images.removeFirst();
            droppedImageCount++;
        }
        imageCount++;
    }

    synchronized void addGyroscope(double timestampMilliseconds, float x, float y, float z) {
        gyroscope.addLast(new ImuSample(timestampMilliseconds / 1000.0, x, y, z));
        while (gyroscope.size() > MAX_IMU_SAMPLES) gyroscope.removeFirst();
        gyroCount++;
        mergeAvailableImu();
    }

    synchronized void addAccelerometer(double timestampMilliseconds, float x, float y, float z) {
        accelerometer.addLast(new ImuSample(timestampMilliseconds / 1000.0, x, y, z));
        while (accelerometer.size() > MAX_IMU_SAMPLES) accelerometer.removeFirst();
        accelCount++;
        mergeAvailableImu();
    }

    /** Equivalent to realsense2_camera unite_imu_method:=linear_interpolation. */
    private void mergeAvailableImu() {
        while (accelerometer.size() >= 2 && !gyroscope.isEmpty()) {
            ImuSample before = accelerometer.removeFirst();
            ImuSample after = accelerometer.peekFirst();
            while (!gyroscope.isEmpty() && gyroscope.peekFirst().timestampSeconds < before.timestampSeconds) {
                gyroscope.removeFirst();
            }
            while (!gyroscope.isEmpty() && gyroscope.peekFirst().timestampSeconds <= after.timestampSeconds) {
                ImuSample gyro = gyroscope.removeFirst();
                double duration = after.timestampSeconds - before.timestampSeconds;
                float alpha = duration <= 0.0 ? 0.0f
                        : (float) ((gyro.timestampSeconds - before.timestampSeconds) / duration);
                float ax = before.x + alpha * (after.x - before.x);
                float ay = before.y + alpha * (after.y - before.y);
                float az = before.z + alpha * (after.z - before.z);
                // estimator_node.cpp rejects non-increasing IMU timestamps.
                if (gyro.timestampSeconds > lastUnifiedTimestamp) {
                    unifiedImu.addLast(new UnifiedImuSample(gyro.timestampSeconds,
                            ax, ay, az, gyro.x, gyro.y, gyro.z));
                    lastUnifiedTimestamp = gyro.timestampSeconds;
                    while (unifiedImu.size() > MAX_IMU_SAMPLES) unifiedImu.removeFirst();
                    unifiedCount++;
                    notifyAll();
                }
            }
        }
    }

    /** Returns a camera frame only after IMU data brackets its timestamp. */
    synchronized ImageSample pollReadyImage(double timeOffsetSeconds) {
        while (!images.isEmpty() && unifiedImu.size() >= 2) {
            ImageSample image = images.peekFirst();
            double targetTime = image.timestampSeconds + timeOffsetSeconds;
            if (unifiedImu.peekLast().timestampSeconds <= targetTime) return null;
            if (unifiedImu.peekFirst().timestampSeconds < targetTime) {
                images.removeFirst();
                return image;
            }
            images.removeFirst();
        }
        return null;
    }

    synchronized boolean hasReadyImage(double timeOffsetSeconds) {
        return !images.isEmpty()
                && unifiedImu.size() >= 2
                && unifiedImu.peekLast().timestampSeconds
                > images.peekFirst().timestampSeconds + timeOffsetSeconds;
    }

    /**
     * Equivalent to vins_estimator/getMeasurements(): consume IMU only after the
     * feature tracker actually publishes a frame. The caller supplies estimator.td
     * while holding the estimator processing lock.
     */
    synchronized Measurement consumeMeasurementForFeature(
            ImageSample image, double timeOffsetSeconds) {
        if (image == null || unifiedImu.size() < 2) return null;
        double targetTime = image.timestampSeconds + timeOffsetSeconds;
        if (unifiedImu.peekLast().timestampSeconds <= targetTime
                || unifiedImu.peekFirst().timestampSeconds >= targetTime) {
            return null;
        }
        ArrayList<UnifiedImuSample> samples = new ArrayList<>();
        while (!unifiedImu.isEmpty()
                && unifiedImu.peekFirst().timestampSeconds < targetTime) {
            samples.add(unifiedImu.removeFirst());
        }
        if (!unifiedImu.isEmpty()) samples.add(unifiedImu.peekFirst());
        pairedImageCount++;
        return new Measurement(image, samples);
    }

    /** Waits like the source estimator condition variable when the trailing IMU is late. */
    synchronized Measurement awaitMeasurementForFeature(
            ImageSample image, double timeOffsetSeconds) {
        if (image == null) return null;
        double targetTime = image.timestampSeconds + timeOffsetSeconds;
        long deadlineNanos = System.nanoTime() + FEATURE_IMU_WAIT_MILLIS * 1_000_000L;
        while (unifiedImu.size() < 2
                || unifiedImu.peekLast().timestampSeconds <= targetTime) {
            if (!unifiedImu.isEmpty()
                    && unifiedImu.peekFirst().timestampSeconds >= targetTime) {
                return null;
            }
            long remainingNanos = deadlineNanos - System.nanoTime();
            if (remainingNanos <= 0L) return null;
            try {
                wait(Math.max(1L, remainingNanos / 1_000_000L));
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
                return null;
            }
        }
        return consumeMeasurementForFeature(image, timeOffsetSeconds);
    }

    synchronized Status status() {
        return new Status(imageCount, gyroCount, accelCount, unifiedCount,
                pairedImageCount, droppedImageCount, images.size());
    }

    synchronized void clear() {
        images.clear();
        gyroscope.clear();
        accelerometer.clear();
        unifiedImu.clear();
        imageCount = gyroCount = accelCount = unifiedCount = pairedImageCount = 0;
        droppedImageCount = 0;
        lastUnifiedTimestamp = Double.NEGATIVE_INFINITY;
        notifyAll();
    }
}
