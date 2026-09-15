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

    data class Entry(val timestamp: String, val instruction: String, val resultSummary: String, val kind: String = "")

    val entries = MutableStateFlow<List<Entry>>(emptyList())

    /** 调用方（NavigateFragment）只会为 final_text/error 调用这里，中间态流式事件不会进入本记录 */
    fun append(instruction: String, resultSummary: String, kind: String = "") {
        val timestamp = SimpleDateFormat("HH:mm:ss", Locale.getDefault()).format(Date())
        entries.value = entries.value + Entry(timestamp, instruction, resultSummary, kind)
        if(kind == "final_text"||kind=="error") {
            TtsUtils.speak(resultSummary)
        }
    }

    /** 重新下发新指令前调用，清空上一阶段残留的记录 */
    fun clear() {
        entries.value = emptyList()
    }
}
