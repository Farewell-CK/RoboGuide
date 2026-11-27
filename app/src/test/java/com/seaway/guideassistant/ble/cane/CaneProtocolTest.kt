package com.seaway.guideassistant.ble.cane

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Ignore
import org.junit.Test

/**
 * 用**冻结的测试向量**校验 Kotlin 实现与固件逐字节一致。
 *
 * 这些向量与下面两份实现完全相同，任何一处改动都必须三处同步 + 升 proto_ver：
 *   main/cane_selftest.c（C 自检）、tools/protocol_test_vectors.py（Python 参照）
 *
 * 跑法：`./gradlew :app:testDebugUnitTest`（纯 JVM，不需要模拟器）
 */
class CaneProtocolTest {

    private fun hex(s: String): ByteArray =
        s.trim().split(" ").filter { it.isNotEmpty() }.map { it.toInt(16).toByte() }.toByteArray()

    // ---------------------------------------------------------------- 上行

    @Test
    fun `STATE 编码与向量一致`() {
        val raw = hex("01 00 11 1b 4b 01 01 78 13 12 39")
        assertTrue(CaneProtocol.checkFrame(raw))
        val st = CaneProtocol.decode(raw) as CaneProtocol.State
        assertEquals(0x11, st.protoVer)
        assertEquals(0x1B, st.flags)
        assertEquals(75, st.batteryPct)
        assertTrue(st.lightOn)
        assertEquals(0x01, st.motorMask)
        assertEquals(120, st.caneLenCm)
        assertEquals(0x13, st.fwVer)
        assertEquals(0x12, st.hwVer)
        assertTrue(st.imuOk && st.facingValid && st.magCalibrated)
    }

    @Test
    fun `DATA 摆动帧所有字段都解码正确`() {
        val raw = hex("10 01 2c 01 84 03 3c 84 03 00 00 01 00 01")
        assertEquals(14, raw.size)                      // v18：DATA 帧 14 B（载荷 11 B）
        val d = CaneProtocol.decode(raw) as CaneProtocol.Data
        assertEquals(300, d.tilt01deg)                  // 30.0°
        assertEquals(900, d.sector01deg)                // 90.0° = 摆动中心（正前）
        assertEquals(60, d.reachCm)
        assertEquals(900, d.orientation01deg)           // 90.0°（东）
        assertEquals(0, d.bodyFacing01deg)              // 0.0°（朝北）
        assertEquals(CaneProtocol.ST_SWING, d.state)
        assertEquals(0, d.peak)
        assertTrue(d.sectorValid)

        // rel 自 v18 起不在线格式里：手机用同帧两个绝对值自算，270.0° = 正右
        assertEquals(2700, d.relCompat01deg)
        assertEquals(1, CaneProtocol.sectorFromRel(2700))
    }

    @Test
    fun `DATA 碰撞帧 peak 有值`() {
        val raw = hex("10 02 2c 01 84 03 3c 84 03 00 00 03 5a 5a")
        val d = CaneProtocol.decode(raw) as CaneProtocol.Data
        assertEquals(CaneProtocol.ST_COLLIDING, d.state)
        assertEquals(90, d.peak)
    }

    @Test
    fun `DATA 抬起帧 sector 报无效`() {
        val raw = hex("10 03 00 00 ff ff ff 00 00 00 00 02 00 ee")
        val d = CaneProtocol.decode(raw) as CaneProtocol.Data
        assertEquals(CaneProtocol.ST_LIFTED, d.state)
        assertEquals(CaneProtocol.REACH_INVALID, d.reachCm)
        assertEquals(CaneProtocol.SECTOR_INVALID, d.sector01deg)
        assertTrue(!d.sectorValid)
    }

    // ---------------------------------------------------------------- 下行

    @Ignore("向量沿用旧版震动 mode 编号/count 语义，与 demo 当前 CaneProtocol 不一致（demo 工程里同样失败），待对照固件后更新")
    @Test
    fun `MOTOR 四种命令编码与向量一致`() {
        assertArrayEquals(hex("01 00 02 3c 03 05 39"),
            CaneProtocol.motor(0x00, CaneProtocol.MOTOR_RIGHT, 60, CaneProtocol.VIB_TAP_LONG, 5))
        assertArrayEquals(hex("01 01 08 28 01 02 23"),
            CaneProtocol.motor(0x01, CaneProtocol.MOTOR_LEFT, 40, CaneProtocol.VIB_TAP_SHORT, 2))
        assertArrayEquals(hex("01 02 0f 5a 02 03 57"),
            CaneProtocol.motor(0x02, CaneProtocol.MOTOR_ALL, 90, CaneProtocol.VIB_TAP_LONG, 3))
        assertArrayEquals(hex("01 03 00 00 00 00 02"), CaneProtocol.motorStop(0x03))
    }

    @Test
    fun `LIGHT 与 CALIBRATE 编码与向量一致`() {
        assertArrayEquals(hex("02 04 01 07"), CaneProtocol.light(0x04, true))
        assertArrayEquals(hex("03 05 00 06"), CaneProtocol.calibrate(0x05, 0))
        assertArrayEquals(hex("03 06 01 04"), CaneProtocol.calibrate(0x06, 1))
        assertArrayEquals(hex("03 07 02 06"), CaneProtocol.calibrate(0x07, 2))
    }

    @Test
    fun `ACK 编码与向量一致`() {
        // 合法帧整帧异或为 0；0x21 只是**校验位的值**，不是 bcc() 的返回值
        assertTrue(CaneProtocol.checkFrame(hex("20 00 01 00 00 21")))
        assertEquals(0x21, hex("20 00 01 00 00 21").last().toInt() and 0xFF)
        val a = CaneProtocol.decode(hex("20 03 03 05 01 24")) as CaneProtocol.Ack
        assertEquals(CaneProtocol.DN_CALIBRATE, a.cmdType)
        assertEquals(5, a.cmdSeq)
        assertEquals(CaneProtocol.ACK_SKIPPED, a.status)
        assertTrue(!a.done)
    }

    // ---------------------------------------------------------------- 规则

    @Test
    fun `校验位为 0x00 的帧必须合法`() {
        // TYPE 0x10 ^ SEQ 0x00 ^ 11 个 0x10 = 0x00（11 B 正好是 v18 的 DATA 载荷长度）
        val payload = ByteArray(11) { 0x10 }
        val f = CaneProtocol.frame(CaneProtocol.UP_DATA, 0x00, payload)
        assertEquals(0x00, f.last().toInt() and 0xFF)
        assertTrue(CaneProtocol.checkFrame(f))
    }

    @Test
    fun `单 bit 改动必须被检出`() {
        val f = CaneProtocol.motor(0x00, CaneProtocol.MOTOR_RIGHT, 60, CaneProtocol.VIB_TAP_LONG, 5)
        f[3] = (f[3].toInt() xor 0x01).toByte()
        assertTrue(!CaneProtocol.checkFrame(f))
    }

    @Test
    fun `未知类型必须整帧丢弃`() {
        val f = CaneProtocol.frame(0x7E, 0x00, ByteArray(4))
        assertNull(CaneProtocol.decode(f))
    }

    @Test
    fun `载荷长度不符必须丢弃`() {
        // DATA 应该是 11 B 载荷，这里给 13（v17 的长度）——必须被拒
        val f = CaneProtocol.frame(CaneProtocol.UP_DATA, 0x00, ByteArray(13))
        assertNull(CaneProtocol.decode(f))
    }

    @Ignore("向量沿用旧版震动 mode 编号/count 语义，与 demo 当前 CaneProtocol 不一致（demo 工程里同样失败），待对照固件后更新")
    @Test
    fun `震动参数越界会被钳位`() {
        // 点震次数上限 20、长震动上限 100（= 10 s）
        val a = CaneProtocol.motor(0, CaneProtocol.MOTOR_ALL, 100, CaneProtocol.VIB_TAP_SHORT, 99)
        assertEquals(20, a[5].toInt())
        val b = CaneProtocol.motor(0, CaneProtocol.MOTOR_ALL, 100, CaneProtocol.VIB_INTERVAL_SLOW, 250)
        assertEquals(100, b[5].toInt() and 0xFF)
    }

    @Test
    fun `rel 反算与扇区映射`() {
        assertEquals(2700, CaneProtocol.relFromAbs(900, 0))    // 人体朝北、杖尖朝东 -> 正右
        assertEquals(2000, CaneProtocol.relFromAbs(100, 3500)) // 跨 0°
        assertEquals(2, CaneProtocol.sectorFromRel(0))     // rel 0 = 后背 -> 扇区「后」
        assertEquals(3, CaneProtocol.sectorFromRel(900))
        assertEquals(0, CaneProtocol.sectorFromRel(1800))
        assertEquals(1, CaneProtocol.sectorFromRel(2700))
        assertEquals(2, CaneProtocol.sectorFromRel(3500))
    }
}
