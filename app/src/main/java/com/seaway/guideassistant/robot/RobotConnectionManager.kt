package com.seaway.guideassistant.robot

import com.robonix.client.data.grpc.AtlasClient
import com.robonix.client.data.model.PilotEvent
import com.robonix.client.domain.ChatRepository
import com.seaway.guideassistant.utils.MMKVUtils
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
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

    /** 参考 robonix-client-android 的 ChatViewModel：task_state.status 落到这些值之一才算这一轮真正结束。
     * 把 "interrupted" 也算作终态，否则一旦服务端报过一次 interrupted，isBusy 会卡在 true 上，
     * 之后所有指令都会带着一个已经失效的 turn id 走 steer，导致持续被打断。 */
    private val TERMINAL_TASK_STATUSES = listOf("done", "completed", "failed", "cancelled", "canceled", "aborted", "interrupted")
    private val TURN_ID_REGEX = Regex("^turn_id=(.+)$")

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

    /** 当前会话内是否还有未到终态的机器狗任务；决定下一条指令走 steer（替换当前轮）还是 task（新开一轮）。 */
    private var isBusy = false

    /** 当前这一轮任务的 turn id，从 status 事件里的 "turn_id=xxx" 解析出来，随 steer/abort 一起带给服务端，
     * 让服务端能精确知道要打断/替换的是哪一轮，而不是"当前正在跑的任意一轮"。 */
    private var activeTurnId = ""

    /** 上一次 sendIndoorInstruction 打开的 gRPC 流；下发新指令前先取消掉，避免同时监听两条流。 */
    private var activeJob: Job? = null

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

    /** 下发一条室内指令。若上一轮任务还没到终态（isBusy），带上它的 turn id 走 steer（替换当前轮），
     * 否则走 task 新开一轮——不再用"先 abort 再 submit"的方式清理上一轮：那种写法是两个互不等待、
     * 各自独立 launch 的 gRPC 调用，abort 又不带 expected_turn_id，很容易打到刚提交的新一轮上，
     * 表现为新指令一下发就立刻收到 interrupted。 */
    fun sendIndoorInstruction(text: String, onEvent: (PilotEvent) -> Unit, onError: (String) -> Unit = {}) {
        val ep = endpoint()
        if (ep == null) {
            onError("机器狗未连接")
            return
        }
        activeJob?.cancel()
        val steer = isBusy
        val turnId = activeTurnId
        activeJob = scope.launch {
            try {
                chatRepository.submitTextTask(
                    ep, text, sessionId, userId = "guide-assistant",
                    steer = steer, expectedTurnId = if (steer) turnId else "",
                ).collect { event ->
                    trackTurnState(event)
                    onEvent(event)
                }
            } catch (e: Exception) {
                onError(e.message ?: "指令下发失败")
            }
        }
    }

    /** 中止机器狗当前会话下还未结束的任务：仅在用户主动要求"停止/结束"时调用（如点击"结束导航"）。
     * 带上当前 turn id 精确指定要中止的那一轮，并立即在本地把状态清成"空闲"——不依赖 abort 请求的
     * 回包来更新状态，这样紧接着触发的下一次下发（比如推进到下一阶段）能确定地走 task 而不是
     * 带着一个已经要求中止的 turn id 去 steer。 */
    fun cancelIndoorInstruction() {
        val ep = endpoint() ?: return
        if (!isBusy) return
        val turnId = activeTurnId
        activeJob?.cancel()
        activeJob = null
        isBusy = false
        activeTurnId = ""
        scope.launch {
            try {
                chatRepository.submitAbortTask(ep, sessionId, userId = "guide-assistant", expectedTurnId = turnId)
                    .collect { }
            } catch (_: Exception) {
                // 静默失败：这是清理性调用，不需要向用户展示错误
            }
        }
    }

    /** 从事件流里维护 isBusy/activeTurnId，镜像 robonix-client-android 的 ChatViewModel.handlePilotEvent。 */
    private fun trackTurnState(event: PilotEvent) {
        when (event.kind) {
            "task_state" -> event.taskState?.let { ts ->
                val status = ts.status.lowercase()
                isBusy = status !in TERMINAL_TASK_STATUSES
                if (!isBusy) activeTurnId = ""
            }
            "status" -> event.status?.let { status ->
                val match = TURN_ID_REGEX.find(status.message)
                if (match != null) {
                    activeTurnId = match.groupValues[1]
                } else if (status.state == 1 || status.state == 2) {
                    activeTurnId = ""
                    isBusy = false
                }
            }
        }
    }
}
