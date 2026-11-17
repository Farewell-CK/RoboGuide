package com.seaway.guideassistant.navindoor

import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import java.io.ByteArrayOutputStream
import java.util.concurrent.Executors

class NaviIndoorActivity : FlutterActivity(), SensorEventListener {
    private var manager: SensorManager? = null
    private var token: String? = null
    private var steps = 0
    private val imageWorker = Executors.newSingleThreadExecutor()

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        manager = getSystemService(Context.SENSOR_SERVICE) as SensorManager
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "navi/navigation_image")
            .setMethodCallHandler { call, result ->
                if (call.method != "compress") {
                    result.notImplemented()
                } else {
                    val bytes = call.arguments as? ByteArray
                    if (bytes == null || bytes.size > 8 * 1024 * 1024) {
                        result.error("invalid_image", "Invalid navigation image", null)
                    } else {
                        imageWorker.execute {
                            var bitmap: Bitmap? = null
                            try {
                                val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
                                BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
                                require(bounds.outWidth > 0 && bounds.outHeight > 0 &&
                                    bounds.outWidth.toLong() * bounds.outHeight <= 12_000_000L)
                                val decoded = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                                    ?: error("Cannot decode navigation image")
                                bitmap = decoded
                                // Preserve full pixel dimensions and field of view.
                                val output = ByteArrayOutputStream()
                                check(decoded.compress(Bitmap.CompressFormat.JPEG, 85, output))
                                val packed = output.toByteArray()
                                runOnUiThread { result.success(if (packed.size < bytes.size) packed else bytes) }
                            } catch (_: Exception) {
                                runOnUiThread { result.error("image_compression", "Use original image", null) }
                            } finally {
                                bitmap?.recycle()
                            }
                        }
                    }
                }
            }
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "navi/navigation_motion")
            .setMethodCallHandler { call, result ->
                val requested = call.argument<String>("token")
                when (call.method) {
                    "start" -> {
                        manager?.unregisterListener(this)
                        token = requested
                        steps = 0
                        val sensor = manager?.getDefaultSensor(Sensor.TYPE_STEP_DETECTOR)
                        val ready = try {
                            sensor != null && manager?.registerListener(this, sensor, SensorManager.SENSOR_DELAY_NORMAL) == true
                        } catch (_: SecurityException) { false }
                        result.success(ready)
                    }
                    "steps" -> result.success(if (requested == token) steps else null)
                    "stop" -> {
                        if (requested == token) {
                            manager?.unregisterListener(this)
                            token = null
                            steps = 0
                        }
                        result.success(null)
                    }
                    else -> result.notImplemented()
                }
            }
    }

    override fun onSensorChanged(event: SensorEvent) {
        if (token != null && event.sensor.type == Sensor.TYPE_STEP_DETECTOR) steps++
    }
    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}
    override fun onDestroy() {
        imageWorker.shutdown()
        manager?.unregisterListener(this)
        token = null
        super.onDestroy()
    }
}
