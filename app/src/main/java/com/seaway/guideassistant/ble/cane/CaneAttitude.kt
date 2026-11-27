package com.seaway.guideassistant.ble.cane

import kotlin.math.PI

/** 盲杖姿态页的纯展示逻辑（不依赖 Android API，可直接单测）。 */
object CaneAttitude {

    /** state 枚举 -> 展示名称，顺序即底部状态条的显示顺序 */
    val STATE_NAMES = listOf("静止", "摆动中", "抬起", "碰撞中", "标定中", "坑/洼地")

    fun stateName(state: Int): String = STATE_NAMES.getOrElse(state) { "未知" }

    /** 碰撞等级名称，下标即等级 0..4 */
    val LEVEL_NAMES = listOf("无碰撞", "轻微", "中等", "较强", "剧烈")

    /** peak(0..100) -> 碰撞等级：0 无；1..25 / 26..50 / 51..75 / 76..100 依次为 1..4 级 */
    fun peakLevel(peak: Int): Int = when {
        peak <= 0 -> 0
        peak <= 25 -> 1
        peak <= 50 -> 2
        peak <= 75 -> 3
        else -> 4
    }

    /** 碰撞动画时长：等级越高持续越久（ms） */
    fun rippleDurationMs(level: Int): Long = if (level <= 0) 0L else 400L + level * 250L

    /** 碰撞动画最大半径占雷达半径的比例：等级越高扩散越大 */
    fun rippleRadiusFraction(level: Int): Float = if (level <= 0) 0f else 0.08f + level * 0.07f

    /** 碰撞动画波纹圈数 = 等级 */
    fun rippleRings(level: Int): Int = level.coerceIn(0, 4)

    /**
     * sector(0.1°) -> 半圆上的指针角（弧度，数学坐标系：0 指向右、逆时针为正）。
     * 0 最左 -> π；900 正前 -> π/2（竖直向上）；1800 最右 -> 0。无效返回 null。
     */
    fun sectorToRadians(sector01deg: Int): Double? {
        if (sector01deg == CaneProtocol.SECTOR_INVALID || sector01deg !in 0..1800) return null
        return PI - sector01deg / 1800.0 * PI
    }

    /** reach 有效返回 cm，255 无效返回 null */
    fun reachOrNull(reachCm: Int): Int? = reachCm.takeIf { it != CaneProtocol.REACH_INVALID }

    /** tilt 超过 900 按 900 饱和，返回度数 */
    fun tiltDegrees(tilt01deg: Int): Float = tilt01deg.coerceIn(0, 900) / 10f

    /** 0.1° 单位转度，保留 1 位小数 */
    fun degText(v01deg: Int): String = String.format("%.1f°", v01deg / 10f)

    /** body_facing 仅在 STATE.flags 标记可信且不在标定中时有效 */
    fun bodyFacingOrNull(data: CaneProtocol.Data, facingValid: Boolean): Int? =
        data.bodyFacing01deg.takeIf { facingValid && data.state != CaneProtocol.ST_CALIBRATING }

    /** orientation 在标定中不可信 */
    fun orientationOrNull(data: CaneProtocol.Data): Int? =
        data.orientation01deg.takeIf { data.state != CaneProtocol.ST_CALIBRATING }
}
