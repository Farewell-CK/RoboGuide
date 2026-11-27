package com.seaway.guideassistant.ble.cane

import android.app.Activity
import android.content.Context
import android.util.Log
import com.hjq.permissions.OnPermissionCallback
import com.hjq.permissions.XXPermissions
import com.seaway.guideassistant.mock.MockDeviceRepository
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

/**
 * 智能导盲杖 BLE 连接/控制的唯一持有者，仿 [com.seaway.guideassistant.ble.GlassesManager] 的单例模式。
 * 连接状态与电量同步写入 [MockDeviceRepository.cane]，并通过 [uiState] 通知首页/设备页刷新。
 */
object CaneManager {

    private const val TAG = "CaneBle"

    data class UiState(val connState: CaneBleClient.ConnState, val battery: Int?, val firmware: String? = null)

    private lateinit var client: CaneBleClient
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    private val _uiState = MutableStateFlow(UiState(CaneBleClient.ConnState.IDLE, null))
    val uiState: StateFlow<UiState> = _uiState

    private val _messages = MutableSharedFlow<String>(extraBufferCapacity = 16)
    /** 错误/命令结果提示，UI 可订阅后 toast 或播报 */
    val messages: SharedFlow<String> = _messages

    private val _data = MutableStateFlow<CaneProtocol.Data?>(null)
    /** 最近一帧 DATA（姿态数据）；盲杖断开后置 null */
    val data: StateFlow<CaneProtocol.Data?> = _data

    private val _caneState = MutableStateFlow<CaneProtocol.State?>(null)
    /** 最近一帧 STATE（flags/杖长等）；盲杖断开后置 null */
    val caneState: StateFlow<CaneProtocol.State?> = _caneState

    val connState: CaneBleClient.ConnState get() = _uiState.value.connState
    val isReady: Boolean get() = connState == CaneBleClient.ConnState.READY

    fun init(context: Context) {
        client = CaneBleClient(context)
        scope.launch {
            client.state.collect { st ->
                MockDeviceRepository.cane.connected = st == CaneBleClient.ConnState.READY
                if (st != CaneBleClient.ConnState.READY) {
                    MockDeviceRepository.cane.firmware = "—"
                    _data.value = null
                    _caneState.value = null
                }
                _uiState.value = _uiState.value.copy(connState = st)
            }
        }
        scope.launch { client.errors.collect { _messages.tryEmit(it) } }
        scope.launch {
            client.events.collect { ev ->
                when (ev) {
                    is CaneProtocol.State -> onState(ev)
                    is CaneProtocol.Data -> _data.value = ev
                    else -> Unit
                }
            }
        }
    }

    private fun onState(ev: CaneProtocol.State) {
        _caneState.value = ev
        val cane = MockDeviceRepository.cane
        cane.firmware = "v${ev.fwVer shr 4}.${ev.fwVer and 0x0F}"   // 高 4 位主版本、低 4 位次版本：0x13 -> v1.3
        if (ev.batteryPct == CaneProtocol.BATTERY_NONE) {
            cane.hasBattery = false
        } else {
            cane.hasBattery = true
            cane.battery = ev.batteryPct
        }
        _uiState.value = _uiState.value.copy(
            battery = ev.batteryPct.takeIf { it != CaneProtocol.BATTERY_NONE },
            firmware = cane.firmware,
        )
    }

    /** 申请蓝牙权限后扫描并连接盲杖 */
    fun requestPermissionsAndConnect(activity: Activity) {
        if (client.hasPermissions()) {
            client.connect()
            return
        }
        XXPermissions.with(activity)
            .permission(*client.requiredPermissions())
            .request(object : OnPermissionCallback {
                override fun onGranted(permissions: MutableList<String>, allGranted: Boolean) {
                    if (allGranted) client.connect()
                }

                override fun onDenied(permissions: MutableList<String>, doNotAskAgain: Boolean) {
                    Log.w(TAG, "蓝牙权限被拒绝")
                    _messages.tryEmit("缺少蓝牙权限，无法连接导盲杖")
                }
            })
    }

    fun disconnect() = client.disconnect()

    // ---------------------------------------------------------------- 控制命令（协议同 demo）

    /** 单路 / 多路震动：300ms、100% 强度（同 demo 的 buzz） */
    fun buzz(mask: Int, name: String) {
        client.vibrate(mask, 100, CaneProtocol.VIB_TAP_LONG, 1) { ack -> report("$name 震动", ack) }
    }

    fun stopVibration() = client.stopVibration { ack -> report("全停", ack) }

    fun setLight(on: Boolean) = client.setLight(on) { ack -> report(if (on) "灯开" else "灯关", ack) }

    fun startCalibration() = client.calibrate(1) { ack ->
        report("磁标定开始", ack, "请 10 秒内把杖水平转满一圈")
    }

    fun abortCalibration() = client.calibrate(2) { ack -> report("中止标定", ack) }

    private fun report(action: String, ack: CaneProtocol.Ack?, extra: String? = null) {
        val msg = when {
            ack == null -> "$action：盲杖无响应"
            !ack.done -> "$action：盲杖未执行"
            else -> "$action 已执行" + (extra?.let { "，$it" } ?: "")
        }
        // 发送线程可能不在主线程，tryEmit 线程安全
        _messages.tryEmit(msg)
    }
}
