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
import java.io.OutputStream
import java.util.UUID
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

/**
 * Roboguide Bluetooth SPP plugin.
 *
 * Bridges classic Bluetooth Serial Port Profile (RFCOMM) to Flutter through
 * MethodChannel(s) for control and an EventChannel for received bytes.
 *
 * Threading rules (ANR safety):
 * - connect() runs on a worker thread; the platform thread is never blocked
 *   by the RFCOMM handshake (which can take seconds).
 * - writes go through a single writer-thread queue: enqueue and return,
 *   failures are surfaced on the status channel.
 *
 * MainActivity registers the channels and passes this class in.
 */
class BluetoothSppPlugin(
    private val context: Context,
    private val methodChannel: MethodChannel,
    eventChannel: EventChannel,
    private val statusChannel: EventChannel,
) : MethodChannel.MethodCallHandler {

    private val sppUUID: UUID = UUID.fromString("00001101-0000-1000-8000-00805f9b34fb")
    private val mainHandler = Handler(Looper.getMainLooper())

    @Volatile
    private var eventSink: EventChannel.EventSink? = null
    @Volatile
    private var statusSink: EventChannel.EventSink? = null
    @Volatile
    private var socket: BluetoothSocket? = null
    private var readThread: Thread? = null
    private var writerThread: Thread? = null
    private val writeQueue = LinkedBlockingQueue<ByteArray>()
    @Volatile
    private var outputStream: OutputStream? = null
    @Volatile
    private var connected = false

    init {
        eventChannel.setStreamHandler(object : EventChannel.StreamHandler {
            override fun onListen(arguments: Any?, events: EventChannel.EventSink?) {
                eventSink = events
            }
            override fun onCancel(arguments: Any?) {
                eventSink = null
            }
        })
        statusChannel.setStreamHandler(object : EventChannel.StreamHandler {
            override fun onListen(arguments: Any?, events: EventChannel.EventSink?) {
                statusSink = events
            }
            override fun onCancel(arguments: Any?) {
                statusSink = null
            }
        })
    }

    private fun emitData(data: Any?) {
        val sink = eventSink ?: return
        mainHandler.post {
            sink.success(data)
        }
    }

    private fun emitStatus(state: String, reason: String = "") {
        val sink = statusSink ?: return
        mainHandler.post {
            sink.success(mapOf("state" to state, "reason" to reason))
        }
    }

    fun startListening() {
        methodChannel.setMethodCallHandler(this)
    }

    fun stop() {
        disconnectQuietly("plugin stopped")
        methodChannel.setMethodCallHandler(null)
    }

    override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {
        when (call.method) {
            "getBondedDevices" -> getBondedDevices(result)
            "connect" -> connectAsync(call.argument<String>("mac"), result)
            "disconnect" -> {
                disconnectQuietly("user")
                emitData(null)
                result.success(true)
            }
            "write" -> writeAsync(call.argument<ByteArray>("bytes"), result)
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
    private fun connectAsync(mac: String?, result: MethodChannel.Result) {
        if (mac == null || mac.isEmpty()) {
            result.error("ARG", "mac required", null)
            return
        }
        if (!hasBluetoothPermission()) {
            result.error("PERMISSION", "BLUETOOTH_CONNECT permission not granted", null)
            return
        }
        if (connected) {
            result.success(true)
            return
        }
        // RFCOMM handshake can block for seconds; never run it on the
        // platform thread.
        Thread {
            try {
                val adapter = BluetoothAdapter.getDefaultAdapter()
                    ?: throw IOException("no bluetooth adapter")
                adapter.cancelDiscovery()
                val device = adapter.getRemoteDevice(mac)
                val sock: BluetoothSocket = try {
                    device.createRfcommSocketToServiceRecord(sppUUID)
                } catch (e: IOException) {
                    device.createInsecureRfcommSocketToServiceRecord(sppUUID)
                }
                sock.connect()
                synchronized(this) {
                    socket = sock
                    outputStream = sock.outputStream
                    connected = true
                }
                startWriterThread()
                startReadLoop(sock)
                emitStatus("connected")
                mainHandler.post { result.success(true) }
            } catch (e: Exception) {
                Log.e(TAG, "connect failed", e)
                emitStatus("disconnected", "connect failed: ${e.message}")
                mainHandler.post { result.error("CONNECT", e.message ?: "connect failed", null) }
            }
        }.apply { isDaemon = true }.start()
    }

    /** Enqueue and return; the writer thread does the blocking I/O. */
    private fun writeAsync(bytes: ByteArray?, result: MethodChannel.Result) {
        if (bytes == null) {
            result.error("ARG", "bytes required", null)
            return
        }
        if (!connected || outputStream == null) {
            result.error("NOT_CONNECTED", "no active bluetooth connection", null)
            return
        }
        if (!writeQueue.offer(bytes)) {
            result.error("WRITE", "write queue full (link stalled?)", null)
            return
        }
        result.success(true)
    }

    private fun startWriterThread() {
        writerThread = Thread {
            try {
                while (connected) {
                    val bytes = writeQueue.poll(500, TimeUnit.MILLISECONDS) ?: continue
                    val stream = outputStream ?: break
                    try {
                        stream.write(bytes)
                        stream.flush()
                    } catch (e: IOException) {
                        Log.d(TAG, "write failed", e)
                        disconnectQuietly("write failed: ${e.message}")
                        emitStatus("disconnected", "write failed: ${e.message}")
                        emitData(null)
                        break
                    }
                }
            } catch (_: InterruptedException) {
                // shutting down
            }
        }
        writerThread?.isDaemon = true
        writerThread?.start()
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
                    emitData(chunk)
                }
            } catch (e: IOException) {
                Log.d(TAG, "read loop ended", e)
            } finally {
                val wasConnected = connected
                disconnectQuietly("link closed")
                if (wasConnected) {
                    emitStatus("disconnected", "link closed")
                    emitData(null) // signal EOF
                }
            }
        }
        readThread?.isDaemon = true
        readThread?.start()
    }

    private fun disconnectQuietly(reason: String) {
        synchronized(this) {
            connected = false
            outputStream = null
        }
        writeQueue.clear()
        readThread?.interrupt()
        readThread = null
        writerThread?.interrupt()
        writerThread = null
        try { socket?.close() } catch (_: Exception) {}
        socket = null
        emitStatus("disconnected", reason)
    }

    private fun hasBluetoothPermission(): Boolean {
        return context.checkSelfPermission(android.Manifest.permission.BLUETOOTH_CONNECT) ==
                PackageManager.PERMISSION_GRANTED
    }

    companion object {
        private const val TAG = "RoboguideBtSpp"
    }
}
