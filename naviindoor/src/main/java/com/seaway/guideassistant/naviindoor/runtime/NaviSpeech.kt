package com.seaway.guideassistant.naviindoor.runtime

import android.content.Context
import android.os.SystemClock
import android.speech.tts.TextToSpeech
import java.util.Locale

class NaviSpeech(context: Context, private val unavailable: () -> Unit) {
    var enabled = true
    private var ready = false
    private var pending: String? = null
    private var pendingAt = 0L
    private var pendingObstacle = false
    private var last = ""
    private var lastAt = 0L
    private var obstacleUntil = 0L
    private val tts = TextToSpeech(context) { status -> android.os.Handler(android.os.Looper.getMainLooper()).post { initialize(status) } }
    private fun initialize(status: Int) {
        ready = status == TextToSpeech.SUCCESS && tts.setLanguage(Locale.SIMPLIFIED_CHINESE) >= TextToSpeech.LANG_AVAILABLE
        if (ready) {
            tts.setSpeechRate(.95f)
            pending?.takeIf { !pendingObstacle || SystemClock.elapsedRealtime() - pendingAt <= 3000 }?.let { speak(it, force = true) }
            pending = null
        }
        else unavailable()
    }
    fun speak(text: String, obstacle: Boolean = false, force: Boolean = false) {
        if (!enabled || text.isBlank()) return
        val now = SystemClock.elapsedRealtime()
        if (!obstacle && !force && now < obstacleUntil) return
        if (!obstacle && !force && now - lastAt < 2500) return
        if (!force && text == last && now - lastAt < if (obstacle) 6000 else 15_000) return
        if (obstacle) obstacleUntil = now + 3500
        last = text; lastAt = now
        if (!ready) { pending = text; pendingAt = now; pendingObstacle = obstacle; return }
        tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, "navi-" + now)
    }
    fun stop() { pending = null; last = ""; obstacleUntil = 0; tts.stop() }
    fun close() { stop(); tts.shutdown() }
}
