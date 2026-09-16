package com.seaway.guideassistant.mock

/**
 * 设备静态演示数据，对应 prototype/app.js 的 deviceDetails + state.devices
 */
data class DeviceInfo(
    val id: String,
    val name: String,
    val icon: String,
    val sn: String,
    val connection: String,
    val signal: String,
    val firmware: String,
    val features: List<String>,
    val lastSync: String,
    var connected: Boolean,
    var battery: Int,
    val hasBattery: Boolean = true,
)

object MockDeviceRepository {
    val glasses = DeviceInfo(
        id = "glasses",
        name = "导盲眼镜",
        icon = "👓",
        sn = "GL-2024-00821",
        connection = "蓝牙 BLE",
        signal = "优秀",
        firmware = "v2.1.0",
        features = listOf("深度相机", "Wi-Fi 实时图传", "语义分割"),
        lastSync = "刚刚",
        connected = false,
        battery = 78,
    )

    val cane = DeviceInfo(
        id = "cane",
        name = "智能导盲杖",
        icon = "🦯",
        sn = "CN-2024-01567",
        connection = "蓝牙 BLE",
        signal = "良好",
        firmware = "v1.8.3",
        features = listOf("方向震动指引", "避障侧向震动", "电量监测"),
        lastSync = "1 分钟前",
        connected = false,
        battery = 65,
    )

    val dog = DeviceInfo(
        id = "dog",
        name = "导盲机器狗",
        icon = "🐕",
        sn = "未发现设备",
        connection = "—",
        signal = "—",
        firmware = "—",
        features = listOf("自主导航", "环境感知", "语音交互"),
        lastSync = "—",
        connected = false,
        battery = 0,
        hasBattery = false, // 机器狗暂无真实电量数据来源，隐藏电量展示
    )

    val all: List<DeviceInfo> get() = listOf(glasses, cane, dog)

    fun findById(id: String): DeviceInfo? = all.firstOrNull { it.id == id }

    /** 模拟"绑定机器狗"成功后的状态变化 */
    fun bindDog() {
        dog.connected = true
    }

    /** 机器狗断开连接后的状态变化 */
    fun unbindDog() {
        dog.connected = false
    }
}
