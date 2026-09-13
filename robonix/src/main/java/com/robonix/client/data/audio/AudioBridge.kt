package com.robonix.client.data.audio

import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class AudioBridge @Inject constructor() {
    private val client = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .build()

    private var micSocket: WebSocket? = null
    private var speakerSocket: WebSocket? = null
    private var connected = false
    private var currentEndpoint: String = ""

    fun connect(endpoint: String): Flow<AudioBridgeEvent> = callbackFlow {
        currentEndpoint = endpoint
        val request = Request.Builder().url(endpoint).build()

        val listener = object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                connected = true
                trySend(AudioBridgeEvent.Connected(endpoint))
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                trySend(AudioBridgeEvent.TextMessage(text))
            }

            override fun onMessage(webSocket: WebSocket, bytes: okio.ByteString) {
                trySend(AudioBridgeEvent.AudioData(bytes.toByteArray()))
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(1000, null)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                connected = false
                trySend(AudioBridgeEvent.Disconnected(reason))
                close()
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                connected = false
                trySend(AudioBridgeEvent.Error(t.message ?: "bridge error"))
                close()
            }
        }

        val ws = client.newWebSocket(request, listener)
        micSocket = ws

        awaitClose {
            ws.close(1000, "client close")
            connected = false
        }
    }

    fun sendPcm(data: ByteArray) {
        micSocket?.send(okio.ByteString.of(*data))
    }

    fun disconnect() {
        micSocket?.close(1000, "client disconnect")
        speakerSocket?.close(1000, "client disconnect")
        micSocket = null
        speakerSocket = null
        connected = false
        currentEndpoint = ""
    }

    fun isConnected(): Boolean = connected
}

sealed class AudioBridgeEvent {
    data class Connected(val endpoint: String) : AudioBridgeEvent()
    data class AudioData(val pcm: ByteArray) : AudioBridgeEvent()
    data class TextMessage(val text: String) : AudioBridgeEvent()
    data class Disconnected(val reason: String) : AudioBridgeEvent()
    data class Error(val message: String) : AudioBridgeEvent()
}
