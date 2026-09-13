package com.seaway.guideassistant.ble

import android.annotation.SuppressLint
import android.app.Activity
import android.content.Context
import android.os.Build
import android.util.Log
import com.hjq.permissions.OnPermissionCallback
import com.hjq.permissions.Permission
import com.hjq.permissions.XXPermissions
import com.moyoung.glasses.CRPBleClient
import com.moyoung.glasses.conn.CRPBleConnection
import com.moyoung.glasses.conn.CRPBleDevice
import com.moyoung.glasses.conn.listener.CRPBleConnectionStateListener
import com.moyoung.glasses.scan.bean.CRPScanDevice
import com.moyoung.glasses.scan.callback.CRPScanCallback
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch

/**
 * 导盲眼镜 BLE 扫描/连接/电量状态的唯一持有者。
 * App 启动流程：MainActivity 登录后调用 [requestPermissionsAndScan] -> 权限通过后自动扫描 ->
 * 扫描到目标设备后自动连接 -> 连接成功后查询电量，状态通过 [connectionState]/[batteryState] 广播给 UI。
 */
object GlassesManager {

    private const val TAG = "GlassSDK"
    private const val TARGET_DEVICE_NAME = "G11M PRO"
    private const val SCAN_TIMEOUT_MS = 10000L

    sealed class ConnectionState {
        object Idle : ConnectionState()
        object PermissionDenied : ConnectionState()
        object Scanning : ConnectionState()
        object Connecting : ConnectionState()
        object Connected : ConnectionState()
        object Disconnected : ConnectionState()
        data class Reconnecting(val attempt: Int, val maxAttempts: Int) : ConnectionState()
        data class Failed(val reason: String) : ConnectionState()
    }

    data class BatteryState(val level: Int, val charging: Boolean)

    val connectionState = MutableStateFlow<ConnectionState>(ConnectionState.Idle)
    val batteryState = MutableStateFlow<BatteryState?>(null)

    // 断线后按地址直连重试的间隔，用完后回落到重新扫描
    private val reconnectDelaysMs = longArrayOf(2000L, 5000L, 10000L)

    private lateinit var client: CRPBleClient
    private var bleDevice: CRPBleDevice? = null
    private var connection: CRPBleConnection? = null
    private var lastConnectedAddress: String? = null
    private var reconnectAttempts = 0
    private var manualDisconnect = false
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    fun init(context: Context) {
        client = CRPBleClient.create(context)
    }

    fun currentConnection(): CRPBleConnection? = connection

    /** 用户在设备详情页手动断开，不触发自动重连。 */
    fun disconnect() {
        manualDisconnect = true
        bleDevice?.let { device ->
            if (device.isConnected()) device.disconnect()
        }
    }

    private fun requiredPermissions(): Array<String> =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            arrayOf(Permission.BLUETOOTH_SCAN, Permission.BLUETOOTH_CONNECT)
        } else {
            arrayOf(Permission.ACCESS_FINE_LOCATION)
        }

    /** 供 App 启动时自动调用，也供权限被拒绝后的手动重试按钮调用。 */
    fun requestPermissionsAndScan(activity: Activity) {
        val permissions = requiredPermissions()
        if (XXPermissions.isGranted(activity, *permissions)) {
            startScan()
            return
        }
        XXPermissions.with(activity)
            .permission(*permissions)
            .request(object : OnPermissionCallback {
                override fun onGranted(permissions: MutableList<String>?, all: Boolean) {
                    if (all) startScan() else connectionState.value = ConnectionState.PermissionDenied
                }

                override fun onDenied(permissions: MutableList<String>?, never: Boolean) {
                    connectionState.value = ConnectionState.PermissionDenied
                }
            })
    }

    private fun startScan() {
        connectionState.value = ConnectionState.Scanning
        client.scanDevice(object : CRPScanCallback {
            @SuppressLint("MissingPermission")
            override fun onScanning(device: CRPScanDevice) {
                val name = device.device.name
                Log.d(TAG, "Found: $name [${device.device.address}] RSSI: ${device.rssi}")
                if (name == TARGET_DEVICE_NAME) {
                    client.cancelScan()
                    connect(device.device.address)
                }
            }

            override fun onScanComplete(results: List<CRPScanDevice>) {
                Log.d(TAG, "Scan complete, found ${results.size} devices")
                if (connectionState.value == ConnectionState.Scanning) {
                    connectionState.value = ConnectionState.Failed("未找到眼镜设备")
                }
            }
        }, SCAN_TIMEOUT_MS)
    }

    private fun connect(address: String) {
        lastConnectedAddress = address
        connectionState.value = ConnectionState.Connecting
        establishConnection(address)
    }

    /** 断线后按已知地址直接重连，不重新走扫描；重试次数用完后回落到重新扫描。 */
    private fun scheduleReconnect() {
        val address = lastConnectedAddress ?: return
        if (reconnectAttempts >= reconnectDelaysMs.size) {
            Log.d(TAG, "Reconnect attempts exhausted, fallback to re-scan")
            reconnectAttempts = 0
            startScan()
            return
        }
        val delayMs = reconnectDelaysMs[reconnectAttempts]
        reconnectAttempts++
        connectionState.value = ConnectionState.Reconnecting(reconnectAttempts, reconnectDelaysMs.size)
        scope.launch {
            delay(delayMs)
            establishConnection(address)
        }
    }

    private fun establishConnection(address: String) {
        val device = client.getBleDevice(address)
        bleDevice = device
        val conn = device.connect()
        connection = conn
        conn.setConnectionStateListener(CRPBleConnectionStateListener { newState ->
            when (newState) {
                CRPBleConnectionStateListener.STATE_CONNECTED -> {
                    Log.d(TAG, "Connected!")
                    reconnectAttempts = 0
                    connectionState.value = ConnectionState.Connected
                    conn.setBatteryListener { info ->
                        Log.d(TAG, "Level: ${info.lvl}%, Charging: ${info.charging}")
                        batteryState.value = BatteryState(info.lvl, info.charging)
                    }
                    conn.queryBattery()
                    conn.syncTime()
                }

                CRPBleConnectionStateListener.STATE_DISCONNECTED -> {
                    Log.d(TAG, "Disconnected!")
                    batteryState.value = null
                    connectionState.value = ConnectionState.Disconnected
                    if (manualDisconnect) {
                        manualDisconnect = false
                    } else {
                        scheduleReconnect()
                    }
                }
            }
        })
        conn.connect()
    }
}
