package com.seaway.guideassistant.ble.cane

/**
 * 智能盲杖 BLE 协议 v18.2（proto_ver = 0x12）—— **纯 Kotlin，不依赖任何 Android API**。
 *
 * 放在纯 JVM 模块里（`src/main/kotlin`），这样单元测试不需要模拟器。
 * 本文件与仓库里另外两份实现互为参照，三者必须逐字节一致：
 *   - 固件：        main/cane_protocol.h
 *   - Python 参照： tools/protocol_test_vectors.py
 *   - 本文档：      android_example/CaneProtocol.kt
 *
 * 帧格式：TYPE(1) | SEQ(1) | PAYLOAD(N) | BCC(1)，BCC = 整帧异或，整帧异或为 0 即通过。
 * 最长帧 14 字节 <= 20，**不需要协商 MTU**（默认 23 就够）。
 */
object CaneProtocol {

    const val PROTO_VER = 0x13

    // ---------- 帧类型（上下行命名空间分离，互不冲突）----------
    const val UP_STATE = 0x01
    const val UP_DATA = 0x10
    const val UP_ACK = 0x20
    const val DN_MOTOR = 0x01
    const val DN_LIGHT = 0x02
    const val DN_CALIBRATE = 0x03

    /** 各类型载荷长度；未知类型必须整帧丢弃（向前兼容） */
    private val UP_LEN = mapOf(UP_STATE to 8, UP_DATA to 11, UP_ACK to 3)
    private val DN_LEN = mapOf(DN_MOTOR to 4, DN_LIGHT to 1, DN_CALIBRATE to 1)

    const val MAX_FRAME = 20

    // ---------- STATE.flags ----------
    const val STF_IMU_OK = 0x01
    const val STF_TILT_ZEROED = 0x02   // v18 起**恒为 0**：tilt 不再有"置零"概念
    const val STF_LOW_BATTERY = 0x04
    const val STF_FACING_VALID = 0x08  // rel / body_facing 可信
    const val STF_MAG_CALIBRATED = 0x10

    // ---------- DATA.state ----------
    const val ST_IDLE = 0
    const val ST_SWING = 1
    const val ST_LIFTED = 2
    const val ST_COLLIDING = 3
    const val ST_CALIBRATING = 4
    const val ST_DIP = 5          // 坑/洼地（v18.2 新增）

    // ---------- 无效值约定 ----------
    const val SECTOR_INVALID = 0xFFFF     // sector 无效
    const val REACH_INVALID = 255         // reach 无效
    const val BATTERY_NONE = 255          // 未检测电量（永不置低电标志）

    // ---------- ACK.status ----------
    const val ACK_DONE = 0
    const val ACK_SKIPPED = 1

    // ---------- 震动 ----------
    const val MOTOR_FRONT = 0x01
    const val MOTOR_RIGHT = 0x02
    const val MOTOR_BACK = 0x04
    const val MOTOR_LEFT = 0x08
    const val MOTOR_ALL = 0x0F

    const val VIB_TAP_SHORT = 1        // 短点震：100ms 通 / 100ms 断
    const val VIB_TAP_LONG = 2         // 长点震：300ms 通 / 150ms 断
    const val VIB_INTERVAL = 3         // 短间隔震：1000ms 通 / 500ms 断
    const val VIB_INTERVAL_SLOW = 4    // 长间隔震：3000ms 通 / 500ms 断
    // 所有 mode 的 count 都是「次数」，范围 1..20

    const val COUNT_MIN = 1
    const val COUNT_MAX = 20      // 次数上限

    /** 扇区编号：0 前 / 1 右 / 2 后 / 3 左（与马达 mask 无关，震哪个马达由 App 策略决定） */
    fun sectorFromRel(rel01deg: Int): Int = ((rel01deg + 2250) % 3600) / 900

    /** rel 反算：杖尖绝对方位 - 人体正前方，零位在后背 */
    fun relFromAbs(orientation01deg: Int, bodyFacing01deg: Int): Int =
        (orientation01deg - bodyFacing01deg + 1800 + 3600) % 3600

    // ---------- CRC / BCC ----------
    fun bcc(bytes: ByteArray): Int {
        var x = 0
        for (b in bytes) x = x xor (b.toInt() and 0xFF)
        return x
    }

    /** 整帧异或为 0 即合法；校验位恰为 0x00 也是合法帧 */
    fun checkFrame(frame: ByteArray): Boolean = frame.isNotEmpty() && bcc(frame) == 0

    // ---------- 组帧 ----------
    fun frame(type: Int, seq: Int, payload: ByteArray): ByteArray {
        val body = ByteArray(2 + payload.size)
        body[0] = type.toByte()
        body[1] = (seq and 0xFF).toByte()
        payload.copyInto(body, 2)
        return body + byteArrayOf(bcc(body).toByte())
    }

    fun motor(seq: Int, mask: Int, intensity: Int, mode: Int, count: Int): ByteArray {
        require(mask in 0..0x0F) { "mask 只有低 4 位有效" }
        require(intensity in 0..100) { "intensity 0..100" }
        // count 一律是次数（1..20）；mode 非法时 count 无意义
        val p = if (mode in VIB_TAP_SHORT..VIB_INTERVAL_SLOW) {
            count.coerceIn(COUNT_MIN, COUNT_MAX)
        } else {
            0
        }
        return frame(DN_MOTOR, seq, byteArrayOf(mask.toByte(), intensity.toByte(), mode.toByte(), p.toByte()))
    }

    /** mask = 0 或 mode = 0 表示"立刻全停" */
    fun motorStop(seq: Int): ByteArray = frame(DN_MOTOR, seq, byteArrayOf(0, 0, 0, 0))

    fun light(seq: Int, on: Boolean): ByteArray = frame(DN_LIGHT, seq, byteArrayOf(if (on) 1 else 0))

    /** action: 0 = 置零(等价 1，只动磁力计) / 1 = 开始磁标定 / 2 = 中止 */
    fun calibrate(seq: Int, action: Int): ByteArray =
        frame(DN_CALIBRATE, seq, byteArrayOf(action.toByte()))

    // ---------- 解析 ----------
    sealed interface Event

    data class State(
        val protoVer: Int,
        val flags: Int,
        val batteryPct: Int,
        val lightOn: Boolean,
        val motorMask: Int,
        val caneLenCm: Int,
        val fwVer: Int,
        val hwVer: Int,
    ) : Event {
        val imuOk get() = flags and CaneProtocol.STF_IMU_OK != 0
        val lowBattery get() = flags and CaneProtocol.STF_LOW_BATTERY != 0
        val facingValid get() = flags and CaneProtocol.STF_FACING_VALID != 0
        val magCalibrated get() = flags and CaneProtocol.STF_MAG_CALIBRATED != 0
    }

    data class Data(
        val tilt01deg: Int,          // 0..900，与竖直方向夹角
        val sector01deg: Int,        // 0 最左 / 900 正前 / 1800 最右；0xFFFF 无效
        val orientation01deg: Int,   // 0..3599，杖尖绝对磁方位
        val bodyFacing01deg: Int,    // 0..3599，人体正前方（需 facingValid）
        val reachCm: Int,            // 255 = 无效
        val state: Int,
        val peak: Int,
    ) : Event {
        val sectorValid get() = sector01deg != CaneProtocol.SECTOR_INVALID

        /** 手机自算的"杖尖相对后背方位"：固件自 v18 起不再发送 `rel` */
        val relCompat01deg get() =
            CaneProtocol.relFromAbs(orientation01deg, bodyFacing01deg)
    }

    data class Ack(val cmdType: Int, val cmdSeq: Int, val status: Int) : Event {
        val done get() = status == CaneProtocol.ACK_DONE
    }

    /**
     * 解析一帧。返回 null 表示：太短 / 未知类型 / 长度与类型不符 / 校验失败
     * —— 这些情况按协议**必须整帧丢弃**，不要尝试猜测。
     */
    fun decode(raw: ByteArray): Event? {
        if (raw.size < 3) return null
        if (!checkFrame(raw)) return null

        val type = raw[0].toInt() and 0xFF
        val seq = raw[1].toInt() and 0xFF
        val payload = raw.copyOfRange(2, raw.size - 1)

        val expect = UP_LEN[type] ?: return null
        if (payload.size != expect) return null

        return when (type) {
            UP_STATE -> State(
                protoVer = payload[0].toInt() and 0xFF,
                flags = payload[1].toInt() and 0xFF,
                batteryPct = payload[2].toInt() and 0xFF,
                lightOn = payload[3].toInt() != 0,
                motorMask = payload[4].toInt() and 0xFF,
                caneLenCm = payload[5].toInt() and 0xFF,
                fwVer = payload[6].toInt() and 0xFF,
                hwVer = payload[7].toInt() and 0xFF,
            )

            UP_DATA -> Data(
                tilt01deg = u16(payload, 0),
                sector01deg = u16(payload, 2),
                reachCm = payload[4].toInt() and 0xFF,   // reach 在 sector 之后
                orientation01deg = u16(payload, 5),      // 奇数偏移，字节拼装
                bodyFacing01deg = u16(payload, 7),
                state = payload[9].toInt() and 0xFF,
                peak = payload[10].toInt() and 0xFF,
            )

            UP_ACK -> Ack(
                cmdType = payload[0].toInt() and 0xFF,
                cmdSeq = payload[1].toInt() and 0xFF,
                status = payload[2].toInt() and 0xFF,
            )

            else -> null
        }
    }

    private fun u16(p: ByteArray, off: Int): Int =
        ((p[off].toInt() and 0xFF) or ((p[off + 1].toInt() and 0xFF) shl 8))
}
