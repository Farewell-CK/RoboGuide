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

        // Request runtime permissions on API 31+.
        // BLUETOOTH_SCAN/CONNECT are needed for connect(); RECORD_AUDIO is
        // needed later by the PTT recorder (flutter_sound). Asking together
        // here avoids a silent permission failure when Hold-to-Talk starts.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            val wanted = mutableListOf<String>()
            if (checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) != PackageManager.PERMISSION_GRANTED) {
                wanted.add(Manifest.permission.BLUETOOTH_SCAN)
                wanted.add(Manifest.permission.BLUETOOTH_CONNECT)
            }
            if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                wanted.add(Manifest.permission.RECORD_AUDIO)
            }
            if (wanted.isNotEmpty()) {
                ActivityCompat.requestPermissions(
                    this,
                    wanted.toTypedArray(),
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
