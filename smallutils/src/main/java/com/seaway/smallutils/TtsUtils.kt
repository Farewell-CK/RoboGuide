package com.seaway.smallutils

import android.content.Context
import android.speech.tts.TextToSpeech
import android.util.Log
import java.util.Locale
import java.util.UUID

/**
 * 系统TTS（TextToSpeech）播报工具类，用于将文本内容通过 Android 系统语音引擎播报出来。
 * 使用前需先调用 [init]（引擎初始化是异步的，初始化完成前调用 [speak] 的内容会排队等待引擎就绪后播报）。
 */
object TtsUtils {
    private const val TAG = "TtsUtils"

    private var tts: TextToSpeech? = null
    private var ready = false
    private val pendingTexts = mutableListOf<String>()

    /**
     * 初始化TTS引擎，建议在 Application#onCreate 中调用一次
     */
    fun init(context: Context) {
        if (tts != null) return
        tts = TextToSpeech(context.applicationContext) { status ->
            ready = status == TextToSpeech.SUCCESS
            if (ready) {
                val langResult = tts?.setLanguage(Locale.CHINA)
                if (langResult == TextToSpeech.LANG_MISSING_DATA || langResult == TextToSpeech.LANG_NOT_SUPPORTED) {
                    Log.e(TAG, "Locale.CHINA not supported by TTS engine, result=$langResult, falling back to default locale")
                    tts?.language = Locale.getDefault()
                }
                pendingTexts.forEach { speakNow(it) }
                pendingTexts.clear()
            } else {
                Log.e(TAG, "TextToSpeech init failed, status=$status")
            }
        }
    }

    /**
     * 播报文本
     * @param interrupt 为 true 时立即打断当前播报并清空待播队列后播报本次文本（用于随时间变化、
     * 旧内容已过时的场景，例如导航播报）；为 false（默认）时依次排队播报，不会互相打断
     */
    fun speak(text: String, interrupt: Boolean = false) {
        if (text.isBlank()) return
        if (ready) {
            speakNow(text, interrupt)
        } else if (interrupt) {
            pendingTexts.clear()
            pendingTexts.add(text)
        } else {
            pendingTexts.add(text)
        }
    }

    /**
     * 停止当前播报并清空待播报队列
     */
    fun stop() {
        pendingTexts.clear()
        tts?.stop()
    }

    private fun speakNow(text: String, interrupt: Boolean = false) {
        val queueMode = if (interrupt) TextToSpeech.QUEUE_FLUSH else TextToSpeech.QUEUE_ADD
        tts?.speak(text, queueMode, null, UUID.randomUUID().toString())
    }

    /**
     * 释放TTS引擎资源，建议在 Application 生命周期结束时调用（一般无需手动调用）
     */
    fun shutdown() {
        tts?.stop()
        tts?.shutdown()
        tts = null
        ready = false
        pendingTexts.clear()
    }
}
