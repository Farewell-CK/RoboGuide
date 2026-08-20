package com.roboguide.remote

import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothSocket
import android.content.Context
import android.content.pm.PackageManager
import android.os.Handler
import android.os.Looper
import android.util.Log
import io.flutter.plugin.common.EventChannel
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel
import java.io.IOException
import java.util.UUID

/**
 * Roboguide Bluetooth SPP plugin.
 *
 * Bridges classic Bluetooth Serial Port Profile (RFCOMM) to Flutter through a
 * MethodChannel for control + an EventChannel for received bytes.
 *
 * MainActivity registers the channels and passes this class in.
 */
class BluetoothSppPlugin(
    private val context: Context,
    private val methodChannel: MethodChannel,
    eventChannel: EventChannel,
) : MethodChannel.MethodCallHandler {

    private val sppUUID: UUID = UUID.fromString("00001101-0000-1000-8000-00805f9b34fb")
    private val mainHandler = Handler(Looper.getMainLooper())

    @Volatile
    private var eventSink: EventChannel.EventSink? = null
    @Volatile
    private var socket: BluetoothSocket? = null
    private var readThread: Thread? = null
    @Volatile
    private var connected = false

    init {
        // Listen for the Dart side subscribing to the event stream; the sink
        // may be null until then, so all event sends guard against it.
        eventChannel.setStreamHandler(object : EventChannel.StreamHandler {
            override fun onListen(arguments: Any?, events: EventChannel.EventSink?) {
                eventSink = events
            }

            override fun onCancel(arguments: Any?) {
                eventSink = null
            }
        })
    }

    private fun emit(data: Any?) {
        val sink = eventSink
        if (sink != null) {
            mainHandler.post { sink.success(data) }
        }
    }

    fun startListening() {
        methodChannel.setMethodCallHandler(this)
    }

    fun stop() {
        disconnectQuietly()
        methodChannel.setMethodCallHandler(null)
    }

    override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {
        when (call.method) {
            "getBondedDevices" -> getBondedDevices(result)
            "connect" -> connect(call.argument<String>("mac"), result)
            "disconnect" -> disconnect(result)
            "write" -> write(call.argument<ByteArray>("bytes"), result)
            "isConnected" -> result.success(connected)
            else -> result.notImplemented()
        }
    }

    @SuppressLint("MissingPermission")
    private fun getBondedDevices(result: MethodChannel.Result) {
        if (!hasBluetoothPermission()) {
            result.error("PERMISSION", "BLUETOOTH_CONNECT permission not granted", null)
            return
        }
        val adapter = BluetoothAdapter.getDefaultAdapter()
        if (adapter == null) {
            result.success(emptyList<Map<String, String>>())
            return
        }
        val devices = adapter.bondedDevices.map { d: BluetoothDevice ->
            mapOf("name" to (d.name ?: ""), "address" to d.address)
        }
        result.success(devices)
    }

    @SuppressLint("MissingPermission")
    private fun connect(mac: String?, result: MethodChannel.Result) {
        if (mac == null || mac.isEmpty()) {
            result.error("ARG", "mac required", null)
            return
        }
        if (!hasBluetoothPermission()) {
            result.error("PERMISSION", "BLUETOOTH_CONNECT permission not granted", null)
            return
        }
        try {
            val adapter = BluetoothAdapter.getDefaultAdapter() ?: run {
                result.error("BT", "no bluetooth adapter", null); return
            }
            // cancel discovery first (required before RFCOMM connect on some devices)
            adapter.cancelDiscovery()
            val device = adapter.getRemoteDevice(mac)
            val sock: BluetoothSocket = try {
                device.createRfcommSocketToServiceRecord(sppUUID)
            } catch (e: IOException) {
                // insecure fallback
                device.createInsecureRfcommSocketToServiceRecord(sppUUID)
            }
            sock.connect()
            socket = sock
            connected = true
            startReadLoop(sock)
            result.success(true)
        } catch (e: Exception) {
            Log.e(TAG, "connect failed", e)
            result.error("CONNECT", e.message ?: "connect failed", null)
        }
    }

    @SuppressLint("MissingPermission")
    private fun disconnect(result: MethodChannel.Result) {
        disconnectQuietly()
        result.success(true)
    }

    @SuppressLint("MissingPermission")
    private fun write(bytes: ByteArray?, result: MethodChannel.Result) {
        val sock = socket
        if (bytes == null) {
            result.error("ARG", "bytes required", null); return
        }
        if (sock == null || !connected) {
            result.error("NOT_CONNECTED", "no active bluetooth connection", null); return
        }
        try {
            sock.outputStream.write(bytes)
            sock.outputStream.flush()
            result.success(true)
        } catch (e: Exception) {
            result.error("WRITE", e.message ?: "write failed", null)
        }
    }

    private fun startReadLoop(sock: BluetoothSocket) {
        readThread = Thread {
            try {
                val input = sock.inputStream
                val buffer = ByteArray(4096)
                while (connected) {
                    val n = input.read(buffer)
                    if (n <= 0) break
                    val chunk = ByteArray(n)
                    System.arraycopy(buffer, 0, chunk, 0, n)
                    emit(chunk)
                }
            } catch (e: IOException) {
                Log.d(TAG, "read loop ended", e)
            } finally {
                connected = false
                emit(null) // signal EOF
            }
        }
        readThread?.isDaemon = true
        readThread?.start()
    }

    private fun disconnectQuietly() {
        connected = false
        readThread?.interrupt()
        readThread = null
        try { socket?.close() } catch (_: Exception) {}
        socket = null
    }

    private fun hasBluetoothPermission(): Boolean {
        return context.checkSelfPermission(android.Manifest.permission.BLUETOOTH_CONNECT) ==
                PackageManager.PERMISSION_GRANTED
    }

    companion object {
        private const val TAG = "RoboguideBtSpp"
    }
}
