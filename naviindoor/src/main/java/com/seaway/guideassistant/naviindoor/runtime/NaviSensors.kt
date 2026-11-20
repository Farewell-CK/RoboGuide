package com.seaway.guideassistant.naviindoor.runtime

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.hardware.*
import androidx.core.content.ContextCompat
import com.seaway.guideassistant.naviindoor.core.*
import kotlin.math.*

class NaviSensors(private val context: Context, private val changed: () -> Unit): SensorEventListener {
    private val manager = context.getSystemService(Context.SENSOR_SERVICE) as SensorManager
    var heading: Double? = null; private set
    var steps = 0; private set
    var stepsAvailable = false; private set
    var accuracy = SensorManager.SENSOR_STATUS_UNRELIABLE; private set
    private var lastHeadingAt = 0L
    fun start() {
        stop()
        manager.getDefaultSensor(Sensor.TYPE_ROTATION_VECTOR)?.let { manager.registerListener(this, it, SensorManager.SENSOR_DELAY_UI) }
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.ACTIVITY_RECOGNITION) == PackageManager.PERMISSION_GRANTED) {
            manager.getDefaultSensor(Sensor.TYPE_STEP_DETECTOR)?.let {
                stepsAvailable = runCatching { manager.registerListener(this, it, SensorManager.SENSOR_DELAY_NORMAL) }.getOrDefault(false)
            }
        }
    }
    fun stop() { manager.unregisterListener(this); heading = null; stepsAvailable = false }
    override fun onSensorChanged(event: SensorEvent) {
        if (event.sensor.type == Sensor.TYPE_STEP_DETECTOR) { steps++; changed(); return }
        if (event.sensor.type != Sensor.TYPE_ROTATION_VECTOR) return
        val now = android.os.SystemClock.elapsedRealtime()
        if (now - lastHeadingAt < 100) return
        lastHeadingAt = now
        val matrix = FloatArray(9); val adjusted = FloatArray(9); val angles = FloatArray(3)
        SensorManager.getRotationMatrixFromVector(matrix, event.values)
        SensorManager.getOrientation(matrix, angles)
        // Portrait Activity. Match flutter_compass' pitch/roll remapping so existing recordings stay usable.
        val axisY = when {
            angles[1] < -PI / 4 -> SensorManager.AXIS_Z
            angles[1] > PI / 4 -> SensorManager.AXIS_MINUS_Z
            abs(angles[2]) > PI / 2 -> SensorManager.AXIS_MINUS_Y
            else -> SensorManager.AXIS_Y
        }
        SensorManager.remapCoordinateSystem(matrix, SensorManager.AXIS_X, axisY, adjusted)
        SensorManager.getOrientation(adjusted, angles)
        val current = normalizeHeading(Math.toDegrees(angles[0].toDouble()))
        heading = heading?.let { normalizeHeading(it + relativeHeading(it, current) * .3) } ?: current
        accuracy = event.accuracy
        changed()
    }
    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) { this.accuracy = accuracy }
}
