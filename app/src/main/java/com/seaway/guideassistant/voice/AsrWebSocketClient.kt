package com.seaway.guideassistant.voice

import android.os.Handler
import android.os.Looper
import android.util.Log
import com.seaway.guideassistant.base.Constant
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import org.json.JSONObject

/**
 * ASR语音识别服务的WebSocket客户端，按照外部ASR Service API文档的协议实现：
 * 连接 -> 发送init(is_speaking=true) -> 发送若干段PCM二进制 -> 发送is_speaking=false -> 读取一条最终识别结果
 */
class AsrWebSocketClient(private val okHttpClient: OkHttpClient) {

    private val mainHandler = Handler(Looper.getMainLooper())
    private var webSocket: WebSocket? = null

    fun connect(onOpen: () -> Unit, onResult: (text: String) -> Unit, onFailure: () -> Unit) {
        val requestBuilder = Request.Builder().url(Constant.ASR_WS_URL)
        if (Constant.ASR_AUTH_TOKEN.isNotBlank()) {
            requestBuilder.addHeader("Authorization", "Bearer ${Constant.ASR_AUTH_TOKEN}")
        }
        //WebSocketListener的回调运行在OkHttp的调度线程上，需切回主线程后再回调给调用方（可能触碰视图）
        webSocket = okHttpClient.newWebSocket(requestBuilder.build(), object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                mainHandler.post { onOpen() }
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                val json = try {
                    JSONObject(text)
                } catch (e: Exception) {
                    null
                }
                //仅当服务端标记is_final=true时才视为最终结果，据此结束本轮录音；中间结果忽略
                if (json?.optBoolean("is_final", false) == true) {
                    val result = json.optString("text").orEmpty()
                    mainHandler.post { onResult(result) }
                }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                Log.e(TAG, "ASR websocket failure: url=${Constant.ASR_WS_URL} httpCode=${response?.code} httpMessage=${response?.message}", t)
                mainHandler.post { onFailure() }
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                Log.i(TAG, "ASR websocket closed: code=$code reason=$reason")
            }
        })
    }

    companion object {
        private const val TAG = "AsrWebSocketClient"
    }

    fun sendInit(wavFormat: String = "pcm") {
        val json = JSONObject()
        json.put("is_speaking", true)
        json.put("wav_format", wavFormat)
        webSocket?.send(json.toString())
    }

    fun sendPcm(bytes: ByteArray) {
        webSocket?.send(ByteString.of(*bytes))
    }

    fun finish() {
        val json = JSONObject()
        json.put("is_speaking", false)
        webSocket?.send(json.toString())
    }

    fun close() {
        webSocket?.close(1000, null)
        webSocket = null
    }
}
