package com.seaway.guideassistant.llm

import com.seaway.guideassistant.bean.IntentResponse

/**
 * 本地关键词兜底意图识别：在大模型接口未配置或请求失败时使用
 */
object LocalIntentClassifier {
    private val navigateKeywords = listOf("导航", "出行", "带我去", "走到", "怎么走", "去", "路线")

    fun classify(text: String): IntentResponse {
        val intent = if (navigateKeywords.any { text.contains(it) }) "navigate" else "other"
        return IntentResponse(intent)
    }
}
