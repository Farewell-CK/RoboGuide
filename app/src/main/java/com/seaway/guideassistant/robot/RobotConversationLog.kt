package com.seaway.guideassistant.robot

import com.seaway.smallutils.TtsUtils
import kotlinx.coroutines.flow.MutableStateFlow
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * 机器狗指令下发记录（对应 robonix `ChatScreen.kt` 会话列表的简化版），供 [com.seaway.guideassistant.fragment.NavigateFragment]
 * 渲染"对话记录"列表。仅内存态，不做持久化。
 */
object RobotConversationLog {

    /** 流式事件中逐字返回的分片，text_chunk 会被后续的 final_text 覆盖，不适合单独播报 */
    private const val KIND_TEXT_CHUNK = "text_chunk"

    data class Entry(val timestamp: String, val instruction: String, val resultSummary: String, val kind: String = "")

    val entries = MutableStateFlow<List<Entry>>(emptyList())

    fun append(instruction: String, resultSummary: String, kind: String = "") {
        val timestamp = SimpleDateFormat("HH:mm:ss", Locale.getDefault()).format(Date())
        entries.value = entries.value + Entry(timestamp, instruction, resultSummary, kind)
        if (kind != KIND_TEXT_CHUNK) {
            TtsUtils.speak(resultSummary)
        }
    }
}
