package com.seaway.guideassistant.ble.cane

import android.Manifest
import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanResult
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import java.util.UUID
import java.util.concurrent.ConcurrentLinkedQueue
import java.util.concurrent.atomic.AtomicInteger

/**
 * 智能盲杖 BLE 客户端（移植自 WhiteCane demo）。
 *
 * 设计要点：
 *  1. GATT 写必须串行：用 Channel 做命令队列，一条写完再发下一条。
 *  2. 下行 SEQ 每次递增：固件按 SEQ 去重，同 SEQ 会被当重传只回 ACK 不执行。
 *  3. 写用 WRITE_TYPE_NO_RESPONSE；订阅要写 CCCD 0x2902 = 0x0001。
 *  4. 最长帧 <= 20 B，不需要协商 MTU。
 */
class CaneBleClient(context: Context) {

    companion object {
        const val DEVICE_NAME = "ICT_CANE"

        val SVC: UUID = UUID.fromString("0000abf0-0000-1000-8000-00805f9b34fb")
        val CHR_WRITE: UUID = UUID.fromString("0000abf1-0000-1000-8000-00805f9b34fb")
        val CHR_NOTIFY: UUID = UUID.fromString("0000abf2-0000-1000-8000-00805f9b34fb")
        val CCCD: UUID = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")

        /** 命令超时：固件保证每条命令立刻回 ACK，3 s 足够 */
        private const val ACK_TIMEOUT_MS = 3000L
        private const val SCAN_TIMEOUT_MS = 15_000L
    }

    enum class ConnState { IDLE, SCANNING, CONNECTING, READY, FAILED }

    private val context = context.applicationContext
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    private val _state = MutableStateFlow(ConnState.IDLE)
    val state: StateFlow<ConnState> = _state

    /** 上行事件流（STATE / DATA / ACK） */
    private val _events = MutableSharedFlow<CaneProtocol.Event>(extraBufferCapacity = 64)
    val events: SharedFlow<CaneProtocol.Event> = _events

    /** 错误流：避免"点了没反应"却看不到原因 */
    private val _errors = MutableSharedFlow<String>(extraBufferCapacity = 16)
    val errors: SharedFlow<String> = _errors

    private var adapter: BluetoothAdapter? = null
    private var gatt: BluetoothGatt? = null
    private var notifyChr: BluetoothGattCharacteristic? = null
    private var writeChr: BluetoothGattCharacteristic? = null
    private var scanning = false
    private var scanCb: ScanCallback? = null
    private var scanTimeoutJob: Job? = null
    private var pumpJob: Job? = null

    /** 下一条命令用的 SEQ；订阅成功后归零，与固件约定一致 */
    private val seq = AtomicInteger(0)

    /** 命令队列：保证 GATT 写操作串行（每次连接重建，避免旧连接残留命令） */
    private var writeQueue = Channel<ByteArray>(Channel.UNLIMITED)

    private val pendingAck = ConcurrentLinkedQueue<Pending>()

    private data class Pending(
        val cmdType: Int,
        val cmdSeq: Int,
        val onAck: (CaneProtocol.Ack?) -> Unit,
    )

    // ---------------------------------------------------------------- 权限

    /** Android 12+ 需要 BLUETOOTH_SCAN/CONNECT；11 及以下用位置权限 */
    fun requiredPermissions(): Array<String> =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            arrayOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT)
        } else {
            arrayOf(Manifest.permission.ACCESS_FINE_LOCATION)
        }

    fun hasPermissions(): Boolean = requiredPermissions().all {
        ContextCompat.checkSelfPermission(context, it) == PackageManager.PERMISSION_GRANTED
    }

    // ---------------------------------------------------------------- 扫描 + 连接

    @SuppressLint("MissingPermission")
    fun connect() {
        val cur = _state.value
        if (cur == ConnState.SCANNING || cur == ConnState.CONNECTING || cur == ConnState.READY) return
        if (!hasPermissions()) {
            _errors.tryEmit("缺少蓝牙权限")
            _state.value = ConnState.FAILED
            return
        }
        val mgr = context.getSystemService(Context.BLUETOOTH_SERVICE) as BluetoothManager
        adapter = mgr.adapter
        val scanner = adapter?.takeIf { it.isEnabled }?.bluetoothLeScanner
        if (scanner == null) {
            _errors.tryEmit("蓝牙未开启")
            _state.value = ConnState.FAILED
            return
        }
        _state.value = ConnState.SCANNING
        val cb = object : ScanCallback() {
            override fun onScanResult(callbackType: Int, result: ScanResult) {
                if (!scanning) return
                val name = result.device.name ?: result.scanRecord?.deviceName
                if (name == DEVICE_NAME) {
                    stopScan()
                    _state.value = ConnState.CONNECTING
                    gatt = result.device.connectGatt(context, false, gattCallback, BluetoothDevice.TRANSPORT_LE)
                }
            }

            override fun onScanFailed(errorCode: Int) {
                stopScan()
                _errors.tryEmit("扫描失败（错误码 $errorCode）")
                _state.value = ConnState.FAILED
            }
        }
        scanCb = cb
        scanning = true
        scanner.startScan(cb)

        scanTimeoutJob?.cancel()
        scanTimeoutJob = scope.launch {
            delay(SCAN_TIMEOUT_MS)
            if (scanning) {
                stopScan()
                _errors.tryEmit("未找到 $DEVICE_NAME，请确认盲杖已开机")
                _state.value = ConnState.FAILED
            }
        }
    }

    @SuppressLint("MissingPermission")
    private fun stopScan() {
        scanTimeoutJob?.cancel()
        if (!scanning) return
        scanning = false
        scanCb?.let { cb -> runCatching { adapter?.bluetoothLeScanner?.stopScan(cb) } }
    }

    @SuppressLint("MissingPermission")
    fun disconnect() {
        stopScan()
        teardown()
        _state.value = ConnState.IDLE
    }

    /** 关闭 GATT、停写队列、让所有等待中的命令以超时收场 */
    @SuppressLint("MissingPermission")
    private fun teardown() {
        pumpJob?.cancel()
        pumpJob = null
        writeQueue.close()
        val g = gatt
        gatt = null
        writeChr = null
        notifyChr = null
        runCatching { g?.disconnect() }
        runCatching { g?.close() }
        while (true) pendingAck.poll()?.onAck?.invoke(null) ?: break
    }

    // ---------------------------------------------------------------- GATT 回调

    private val gattCallback = object : BluetoothGattCallback() {

        @SuppressLint("MissingPermission")
        override fun onConnectionStateChange(g: BluetoothGatt, status: Int, newState: Int) {
            if (newState == BluetoothProfile.STATE_CONNECTED) {
                g.discoverServices()
            } else if (g === gatt) {
                // 非主动断开（主动断开时 gatt 已置空，不会走到这里）
                teardown()
                _errors.tryEmit("盲杖连接已断开")
                _state.value = ConnState.FAILED
            } else {
                runCatching { g.close() }
            }
        }

        @SuppressLint("MissingPermission")
        override fun onServicesDiscovered(g: BluetoothGatt, status: Int) {
            val svc = g.getService(SVC)
            writeChr = svc?.getCharacteristic(CHR_WRITE)
            notifyChr = svc?.getCharacteristic(CHR_NOTIFY)
            val notify = notifyChr
            val cccd = notify?.getDescriptor(CCCD)
            if (writeChr == null || notify == null || cccd == null) {
                _errors.tryEmit("未找到盲杖 BLE 服务")
                teardown()
                _state.value = ConnState.FAILED
                return
            }
            g.setCharacteristicNotification(notify, true)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                g.writeDescriptor(cccd, BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE)
            } else {
                @Suppress("DEPRECATION")
                cccd.value = BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
                @Suppress("DEPRECATION")
                g.writeDescriptor(cccd)
            }
            // 不要在这里置 READY：CCCD 没写完就写特征会被丢掉，等 onDescriptorWrite。
        }

        override fun onDescriptorWrite(g: BluetoothGatt, d: BluetoothGattDescriptor, status: Int) {
            if (status != BluetoothGatt.GATT_SUCCESS || d.uuid != CCCD) {
                _errors.tryEmit("订阅失败（CCCD 写入 status=$status），请重连")
                teardown()
                _state.value = ConnState.FAILED
                return
            }
            seq.set(0)   // 订阅后固件会补发 STATE(SEQ=0)
            writeQueue = Channel(Channel.UNLIMITED)
            val queue = writeQueue
            pumpJob?.cancel()
            pumpJob = scope.launch { pumpWrites(queue) }
            _state.value = ConnState.READY
        }

        // API 33+ 的新重载（带 value）
        override fun onCharacteristicChanged(g: BluetoothGatt, ch: BluetoothGattCharacteristic, value: ByteArray) {
            handle(value)
        }

        @Deprecated("API < 33")
        @Suppress("DEPRECATION")
        override fun onCharacteristicChanged(g: BluetoothGatt, ch: BluetoothGattCharacteristic) {
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) {
                ch.value?.let { handle(it) }
            }
        }
    }

    private fun handle(raw: ByteArray) {
        val ev = CaneProtocol.decode(raw) ?: return   // 坏帧直接丢，别猜
        scope.launch { _events.emit(ev) }
        if (ev is CaneProtocol.Ack) {
            val it = pendingAck.iterator()
            while (it.hasNext()) {
                val p = it.next()
                if (p.cmdType == ev.cmdType && p.cmdSeq == ev.cmdSeq) {
                    it.remove()
                    p.onAck(ev)
                    return
                }
            }
        }
    }

    // ---------------------------------------------------------------- 写队列

    @SuppressLint("MissingPermission")
    private suspend fun pumpWrites(queue: Channel<ByteArray>) {
        for (cmd in queue) {
            val g = gatt
            val ch = writeChr
            if (g == null || ch == null) continue
            // 任何异常都必须吞掉：队列协程一旦死掉，之后所有按钮都会"没反应"
            val ok = try {
                ch.writeType = BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                    g.writeCharacteristic(ch, cmd, BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE) ==
                        BluetoothGatt.GATT_SUCCESS
                } else {
                    @Suppress("DEPRECATION")
                    ch.value = cmd
                    @Suppress("DEPRECATION")
                    g.writeCharacteristic(ch)
                }
            } catch (e: Exception) {
                false
            }
            if (!ok) _errors.tryEmit("命令发送被 GATT 拒绝（请断开重连）")
            // 无响应写没有 ATT 应答，只能靠节奏控制：120 ms 给固件和控制器留余量
            delay(120)
        }
    }

    // ---------------------------------------------------------------- 对外命令

    private fun nextSeq(): Int = seq.getAndUpdate { (it + 1) and 0xFF }

    /** 发一条命令并按 SEQ 等 ACK；超时或连接断开时回调 null */
    private fun send(frame: ByteArray, cmdType: Int, onAck: (CaneProtocol.Ack?) -> Unit) {
        if (_state.value != ConnState.READY) {
            onAck(null)
            return
        }
        val cmdSeq = frame[1].toInt() and 0xFF
        pendingAck.add(Pending(cmdType, cmdSeq, onAck))
        writeQueue.trySend(frame)

        scope.launch {
            delay(ACK_TIMEOUT_MS)
            val it = pendingAck.iterator()
            while (it.hasNext()) {
                val p = it.next()
                if (p.cmdType == cmdType && p.cmdSeq == cmdSeq) {
                    it.remove()
                    p.onAck(null)
                    return@launch
                }
            }
        }
    }

    fun vibrate(
        mask: Int,
        intensity: Int = 100,
        mode: Int = CaneProtocol.VIB_TAP_LONG,
        count: Int = 1,
        onAck: (CaneProtocol.Ack?) -> Unit = {},
    ) = send(CaneProtocol.motor(nextSeq(), mask, intensity, mode, count), CaneProtocol.DN_MOTOR, onAck)

    fun stopVibration(onAck: (CaneProtocol.Ack?) -> Unit = {}) =
        send(CaneProtocol.motorStop(nextSeq()), CaneProtocol.DN_MOTOR, onAck)

    fun setLight(on: Boolean, onAck: (CaneProtocol.Ack?) -> Unit = {}) =
        send(CaneProtocol.light(nextSeq(), on), CaneProtocol.DN_LIGHT, onAck)

    /** action: 0 或 1 = 开始磁标定（等价）；2 = 中止 */
    fun calibrate(action: Int, onAck: (CaneProtocol.Ack?) -> Unit = {}) =
        send(CaneProtocol.calibrate(nextSeq(), action), CaneProtocol.DN_CALIBRATE, onAck)
}
