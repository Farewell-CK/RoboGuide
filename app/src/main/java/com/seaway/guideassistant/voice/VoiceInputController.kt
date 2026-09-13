package com.seaway.guideassistant.voice

import android.app.Activity
import com.hjq.permissions.OnPermissionCallback
import com.hjq.permissions.Permission
import com.hjq.permissions.XXPermissions
import okhttp3.OkHttpClient

/**
 * 语音输入控制器：编排录音(AudioRecorder)与ASR识别(AsrWebSocketClient)，
 * 提供点击开始/再次点击结束发送的交互（对应prototype/app.js的bindVoice：非按住说话）
 */
class VoiceInputController(
    private val activity: Activity,
    private val okHttpClient: OkHttpClient,
    private val callbacks: Callbacks,
) {
    interface Callbacks {
        fun onListeningStarted()
        fun onTranscript(text: String)
        fun onError(reason: VoiceErrorReason)
    }

    @Volatile
    private var isListening = false
    private var recorder: AudioRecorder? = null
    private var asrClient: AsrWebSocketClient? = null
    private var hasDetectedSpeech = false
    private var lastVoiceTimestampMs = 0L

    fun isRecording(): Boolean = isListening

    fun toggle() {
        if (isListening) stopAndSend() else requestPermissionAndStart()
    }

    private fun requestPermissionAndStart() {
        XXPermissions.with(activity)
            .permission(Permission.RECORD_AUDIO)
            .request(object : OnPermissionCallback {
                override fun onGranted(permissions: MutableList<String>?, all: Boolean) {
                    if (all) startListening()
                }

                override fun onDenied(permissions: MutableList<String>?, never: Boolean) {
                    callbacks.onError(VoiceErrorReason.PERMISSION_DENIED)
                }
            })
    }

    private fun startListening() {
        hasDetectedSpeech = false
        lastVoiceTimestampMs = 0L
        val client = AsrWebSocketClient(okHttpClient)
        asrClient = client
        client.connect(
            onOpen = {
                client.sendInit()
                val rec = AudioRecorder(
                    onPcmChunk = { chunk -> handlePcmChunk(chunk, client) },
                    onError = {
                        stopInternal()
                        callbacks.onError(VoiceErrorReason.RECORDING_FAILED)
                    }
                )
                recorder = rec
                if (rec.start()) {
                    isListening = true
                    callbacks.onListeningStarted()
                } else {
                    stopInternal()
                    callbacks.onError(VoiceErrorReason.RECORDING_FAILED)
                }
            },
            onResult = { text ->
                stopInternal()
                if (text.isBlank()) callbacks.onError(VoiceErrorReason.NO_SPEECH) else callbacks.onTranscript(text)
            },
            onFailure = {
                stopInternal()
                callbacks.onError(VoiceErrorReason.CONNECTION_FAILED)
            }
        )
    }

    private fun stopAndSend() {
        recorder?.stop()
        recorder = null
        asrClient?.finish()
        isListening = false
    }

    private fun stopInternal() {
        recorder?.stop()
        recorder = null
        asrClient?.close()
        asrClient = null
        isListening = false
    }

    /**
     * 简单的静音检测：说话后若连续静音超过[SILENCE_TIMEOUT_MS]，视为一句话结束，
     * 自动发送结束信号（等价于再次点击btnVoice），无需用户手动点击结束。
     */
    private fun handlePcmChunk(chunk: ByteArray, client: AsrWebSocketClient) {
        client.sendPcm(chunk)
        val now = System.currentTimeMillis()
        if (averageAmplitude(chunk) >= SPEECH_AMPLITUDE_THRESHOLD) {
            hasDetectedSpeech = true
            lastVoiceTimestampMs = now
        } else if (hasDetectedSpeech && now - lastVoiceTimestampMs >= SILENCE_TIMEOUT_MS) {
            stopAndSend()
        }
    }

    private fun averageAmplitude(chunk: ByteArray): Int {
        var sum = 0L
        var i = 0
        while (i + 1 < chunk.size) {
            val sample = ((chunk[i + 1].toInt() shl 8) or (chunk[i].toInt() and 0xFF)).toShort().toInt()
            sum += kotlin.math.abs(sample)
            i += 2
        }
        val sampleCount = chunk.size / 2
        return if (sampleCount == 0) 0 else (sum / sampleCount).toInt()
    }

    fun release() {
        stopInternal()
    }

    companion object {
        private const val SPEECH_AMPLITUDE_THRESHOLD = 800
        private const val SILENCE_TIMEOUT_MS = 800L
    }
}
