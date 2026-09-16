package com.seaway.guideassistant.ws

import android.graphics.Bitmap
import android.os.Handler
import android.os.Looper
import android.util.Log
import com.google.gson.Gson
import com.seaway.guideassistant.base.Constant
import com.seaway.smallutils.BitmapUtils
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.koin.core.component.KoinComponent
import org.koin.core.component.inject
import org.koin.core.qualifier.named

/**
 * 设备监控 watch 服务的WebSocket客户端：设备状态同步、画面推送、导航任务上报（仅上行，
 * 不处理下行业务消息）。仿 [com.seaway.guideassistant.robot.RobotConnectionManager] 的单例模式，
 * 复用 HttpModule 中专供WebSocket使用、不带 HandleErrorInterceptor 的 named("webSocket") 客户端
 * （见该处注释：共用默认okHttpClient会提前读取响应体，打断WebSocket握手移交）。
 */
object DeviceWatchClient : KoinComponent {

    private const val TAG = "DeviceWatchClient"
    private const val RECONNECT_DELAY_MS = 5_000L

    private val okHttpClient: OkHttpClient by inject(named("webSocket"))
    private val gson: Gson by inject()

    private val mainHandler = Handler(Looper.getMainLooper())
    private val ioScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    private var webSocket: WebSocket? = null
    private var isManuallyClosed = false

    fun connect() {
        isManuallyClosed = false
        val requestBuilder = Request.Builder().url(Constant.DMWS_URL)
        if (Constant.DMWS_TOKEN.isNotBlank()) {
            requestBuilder.addHeader("X-Token", Constant.DMWS_TOKEN)
        }
        webSocket = okHttpClient.newWebSocket(requestBuilder.build(), object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                Log.i(TAG, "connected: ${Constant.DMWS_URL}")
                reportDeviceStatus(DeviceType.PHONE,"智能手机","online")
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                Log.e(TAG, "failure: url=${Constant.DMWS_URL} httpCode=${response?.code} httpMessage=${response?.message}", t)
                scheduleReconnect()
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                Log.i(TAG, "closed: code=$code reason=$reason")
                scheduleReconnect()
            }
        })
    }

    fun disconnect() {
        isManuallyClosed = true
        webSocket?.close(1000, null)
        webSocket = null
    }

    private fun scheduleReconnect() {
        if (isManuallyClosed) return
        mainHandler.postDelayed({ connect() }, RECONNECT_DELAY_MS)
    }

    fun reportDeviceStatus(
        deviceType: String,
        deviceName: String,
        status: String,
        battery: Int? = null,
        signal: Int? = null,
        reason: String? = null,
        lastOnlineTime: Long? = null
    ) {
        send(
            MessageType.DEVICE_STATUS,
            deviceType,
            DeviceStatusData(deviceName, status, battery, signal, reason, lastOnlineTime)
        )
    }

    fun pushDeviceFrame(deviceType: String, base64: String) {
        send(MessageType.DEVICE_FRAME, deviceType, DeviceFrameData(base64 = base64))
    }

    fun pushDeviceFrame(deviceType: String, bitmap: Bitmap, quality: Int = 70) {
        ioScope.launch {
            val base64 = BitmapUtils.compressToBase64(bitmap, quality) ?: return@launch
            pushDeviceFrame(deviceType, base64)
        }
    }

    fun reportNavigation(
        instruction: String,
        destination: String,
        phases: List<NavigationPhaseData>,
        currentPhase: Int,
        totalPhases: Int,
        needClarification: Boolean = false,
        clarificationQuestion: String? = null,
        confidence: Float
    ) {
        send(
            MessageType.NAVIGATION,
            deviceType = null,
            data = NavigationData(
                navigationInstruction = instruction,
                destination = destination,
                navigationPhases = phases,
                currentPhase = currentPhase,
                totalPhases = totalPhases,
                needClarification = needClarification,
                clarificationQuestion = clarificationQuestion,
                confidence = confidence
            )
        )
    }

    private fun <T> send(type: String, deviceType: String?, data: T) {
        val socket = webSocket
        if (socket == null) {
            Log.w(TAG, "send skipped, not connected: type=$type")
            return
        }
        val envelope = DeviceWatchEnvelope(type, System.currentTimeMillis(), deviceType, data)
        val json = gson.toJson(envelope)
        socket.send(json)
        Log.i(TAG, "send=$json")
    }
}
