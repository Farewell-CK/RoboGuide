package com.roboguide.remote

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.ActivityCompat
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.EventChannel
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    private lateinit var sppPlugin: BluetoothSppPlugin

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)

        val binaryMessenger = flutterEngine.dartExecutor.binaryMessenger
        val methodChannel = MethodChannel(binaryMessenger, "roboguide/bluetooth_spp")
        val eventChannel = EventChannel(binaryMessenger, "roboguide/bluetooth_spp/events")

        // BluetoothSppPlugin registers the event stream handler in its init.
        sppPlugin = BluetoothSppPlugin(
            context = applicationContext,
            methodChannel = methodChannel,
            eventChannel = eventChannel,
        )
        sppPlugin.startListening()

        // Request runtime bluetooth permission on API 31+ (needed for connect()).
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            if (checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) != PackageManager.PERMISSION_GRANTED) {
                ActivityCompat.requestPermissions(
                    this,
                    arrayOf(
                        Manifest.permission.BLUETOOTH_SCAN,
                        Manifest.permission.BLUETOOTH_CONNECT,
                    ),
                    REQUEST_BT_PERMISSION,
                )
            }
        }
    }

    override fun onDestroy() {
        sppPlugin.stop()
        super.onDestroy()
    }

    companion object {
        private const val REQUEST_BT_PERMISSION = 1001
    }
}
