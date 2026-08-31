package com.elabrador.mobilenavigation;

import android.Manifest;
import android.content.Context;
import android.content.pm.PackageManager;
import android.hardware.Sensor;
import android.hardware.SensorEvent;
import android.hardware.SensorEventListener;
import android.hardware.SensorManager;
import android.location.Location;
import android.location.LocationListener;
import android.location.LocationManager;
import android.os.Bundle;
import android.os.SystemClock;

import androidx.core.content.ContextCompat;

final class PhonePoseTracker implements SensorEventListener, LocationListener {
    interface Listener {
        void onHeading(float headingDegrees);

        void onLocation(Location location);

        void onLocationStatus(String status);
    }

    private static final float HEADING_SMOOTHING = 0.15f;
    private static final long MAX_LAST_KNOWN_AGE_MILLIS = 30_000L;
    private static final long GPS_PREFERENCE_NANOS = 10_000_000_000L;

    private final Context context;
    private final Listener listener;
    private final SensorManager sensorManager;
    private final LocationManager locationManager;
    private final Sensor rotationVectorSensor;
    private final float[] rotationMatrix = new float[9];
    private final float[] orientation = new float[3];

    private boolean hasHeading;
    private float smoothedHeading;
    private long lastDeliveredLocationNanos;
    private long lastGpsLocationNanos;

    PhonePoseTracker(Context context, Listener listener) {
        this.context = context.getApplicationContext();
        this.listener = listener;
        sensorManager = (SensorManager) context.getSystemService(Context.SENSOR_SERVICE);
        locationManager = (LocationManager) context.getSystemService(Context.LOCATION_SERVICE);
        rotationVectorSensor = sensorManager == null
                ? null
                : sensorManager.getDefaultSensor(Sensor.TYPE_ROTATION_VECTOR);
    }

    void start() {
        if (rotationVectorSensor == null) {
            listener.onLocationStatus("手机不支持方向传感器");
        } else {
            sensorManager.registerListener(
                    this, rotationVectorSensor, SensorManager.SENSOR_DELAY_UI);
        }

        if (!hasLocationPermission()) {
            listener.onLocationStatus("等待定位权限");
            return;
        }

        boolean requested = false;
        try {
            if (locationManager.isProviderEnabled(LocationManager.GPS_PROVIDER)) {
                locationManager.requestLocationUpdates(
                        LocationManager.GPS_PROVIDER, 1000L, 0.5f, this);
                requested = true;
                publishLastKnown(LocationManager.GPS_PROVIDER);
            }
            if (locationManager.isProviderEnabled(LocationManager.NETWORK_PROVIDER)) {
                locationManager.requestLocationUpdates(
                        LocationManager.NETWORK_PROVIDER, 1000L, 0.5f, this);
                requested = true;
                publishLastKnown(LocationManager.NETWORK_PROVIDER);
            }
            listener.onLocationStatus(requested ? "正在获取手机位置" : "请打开手机定位");
        } catch (SecurityException error) {
            listener.onLocationStatus("定位权限不可用");
        }
    }

    void stop() {
        if (sensorManager != null) {
            sensorManager.unregisterListener(this);
        }
        if (locationManager != null) {
            locationManager.removeUpdates(this);
        }
    }

    boolean hasLocationPermission() {
        return ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_FINE_LOCATION)
                == PackageManager.PERMISSION_GRANTED
                || ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_COARSE_LOCATION)
                == PackageManager.PERMISSION_GRANTED;
    }

    @Override
    public void onSensorChanged(SensorEvent event) {
        SensorManager.getRotationMatrixFromVector(rotationMatrix, event.values);
        SensorManager.getOrientation(rotationMatrix, orientation);
        float heading = (float) Math.toDegrees(orientation[0]);
        heading = (heading + 360f) % 360f;

        if (!hasHeading) {
            smoothedHeading = heading;
            hasHeading = true;
        } else {
            float delta = ((heading - smoothedHeading + 540f) % 360f) - 180f;
            smoothedHeading = (smoothedHeading + HEADING_SMOOTHING * delta + 360f) % 360f;
        }
        listener.onHeading(smoothedHeading);
    }

    @Override
    public void onAccuracyChanged(Sensor sensor, int accuracy) {
        // Heading remains available while Android recalibrates sensor accuracy.
    }

    @Override
    public void onLocationChanged(Location location) {
        long measurementNanos = location.getElapsedRealtimeNanos();
        boolean gps = LocationManager.GPS_PROVIDER.equals(location.getProvider());
        if (gps) {
            if (measurementNanos <= lastGpsLocationNanos) return;
            lastGpsLocationNanos = measurementNanos;
        } else {
            if (measurementNanos <= lastDeliveredLocationNanos) return;
            if (lastGpsLocationNanos > 0L
                    && measurementNanos - lastGpsLocationNanos < GPS_PREFERENCE_NANOS) return;
        }
        lastDeliveredLocationNanos = Math.max(lastDeliveredLocationNanos, measurementNanos);
        listener.onLocation(location);
        listener.onLocationStatus("手机定位已更新");
    }

    @Override
    public void onProviderEnabled(String provider) {
        listener.onLocationStatus("正在获取手机位置");
    }

    @Override
    public void onProviderDisabled(String provider) {
        listener.onLocationStatus("定位源已关闭");
    }

    @Override
    public void onStatusChanged(String provider, int status, Bundle extras) {
        // Kept for Android versions that still dispatch provider status changes.
    }

    private void publishLastKnown(String provider) {
        Location location = locationManager.getLastKnownLocation(provider);
        if (location == null
                || Math.max(0L, System.currentTimeMillis() - location.getTime())
                > MAX_LAST_KNOWN_AGE_MILLIS) return;
        onLocationChanged(location);
    }
}
