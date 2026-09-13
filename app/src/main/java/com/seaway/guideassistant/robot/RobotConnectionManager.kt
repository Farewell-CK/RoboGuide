package com.seaway.guideassistant.robot

import com.robonix.client.data.grpc.AtlasClient
import com.robonix.client.data.model.PilotEvent
import com.robonix.client.domain.ChatRepository
import com.seaway.guideassistant.utils.MMKVUtils
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import org.koin.core.component.KoinComponent
import org.koin.core.component.inject
import java.util.UUID

/**
 * 机器狗（robonix）连接状态的唯一持有者，仿 [com.seaway.guideassistant.ble.GlassesManager] 的单例模式。
 * 通过 Koin 拿到 `:robonix` 模块原有的 [AtlasClient]/[ChatRepository]，按其原始调用方式
 * （Atlas 查在线状态、Liaison 下发文本任务）接入，不改动 robonix 侧任何代码。
 */
object RobotConnectionManager : KoinComponent {

    private const val KEY_HOST = "robot_host"
    private const val KEY_PORT = "robot_port"

    sealed class ConnectionState {
        object Idle : ConnectionState()
        object Connecting : ConnectionState()
        object Connected : ConnectionState()
        data class Failed(val reason: String) : ConnectionState()
    }

    val connectionState = MutableStateFlow<ConnectionState>(ConnectionState.Idle)

    private val atlasClient: AtlasClient by inject()
    private val chatRepository: ChatRepository by inject()

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private val sessionId = UUID.randomUUID().toString()

    fun savedHost(): String = MMKVUtils.getAny(KEY_HOST, "") as? String ?: ""

    fun savedPort(): Int = MMKVUtils.getAny(KEY_PORT, 0) as? Int ?: 0

    fun endpoint(): String? {
        val host = savedHost()
        val port = savedPort()
        if (host.isBlank() || port <= 0) return null
        return "$host:$port"
    }

    fun connect(host: String, port: Int) {
        MMKVUtils.putAny(KEY_HOST, host)
        MMKVUtils.putAny(KEY_PORT, port)
        connectionState.value = ConnectionState.Connecting
        scope.launch {
            try {
                val snapshot = atlasClient.getSystemSnapshot("$host:$port")
                connectionState.value = if (snapshot.error != null || snapshot.summary.state == "offline") {
                    ConnectionState.Failed(snapshot.error ?: "机器狗离线，无法连接")
                } else {
                    ConnectionState.Connected
                }
            } catch (e: Exception) {
                connectionState.value = ConnectionState.Failed(e.message ?: "连接失败")
            }
        }
    }

    /** 断开机器狗连接：清除已保存的连接信息并回到未连接状态 */
    fun disconnect() {
        MMKVUtils.putAny(KEY_HOST, "")
        MMKVUtils.putAny(KEY_PORT, 0)
        connectionState.value = ConnectionState.Idle
    }

    fun sendIndoorInstruction(text: String, onEvent: (PilotEvent) -> Unit, onError: (String) -> Unit = {}) {
        val ep = endpoint()
        if (ep == null) {
            onError("机器狗未连接")
            return
        }
        scope.launch {
            try {
                chatRepository.submitTextTask(ep, text, sessionId, userId = "guide-assistant")
                    .collect { event -> onEvent(event) }
            } catch (e: Exception) {
                onError(e.message ?: "指令下发失败")
            }
        }
    }

    /** 中止机器狗当前会话下可能还未结束的任务，用于重新下发指令前的清理，或用户手动结束室内任务 */
    fun cancelIndoorInstruction() {
        val ep = endpoint() ?: return
        scope.launch {
            try {
                chatRepository.submitAbortTask(ep, sessionId, userId = "guide-assistant").collect { }
            } catch (_: Exception) {
                // 静默失败：这是清理性调用，不需要向用户展示错误
            }
        }
    }
}
