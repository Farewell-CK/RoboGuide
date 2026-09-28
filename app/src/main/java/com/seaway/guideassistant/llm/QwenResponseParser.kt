package com.seaway.guideassistant.llm

/**
 * 兼容 OpenAI-compatible 返回中 message.content 的两种常见形式：
 * 1) content 是 JSON 字符串；
 * 2) content 已经是 JSON object。
 */
internal object QwenResponseParser {
    fun parseIntent(responseBody: String): NavigationIntent {
        val root = SimpleJson.parse(responseBody) as? Map<*, *>
            ?: error("LLM response root must be a JSON object")
        val choices = root["choices"] as? List<*>
            ?: error("Missing choices in LLM response")
        val firstChoice = choices.firstOrNull() as? Map<*, *>
            ?: error("Empty choices in LLM response")
        val message = firstChoice["message"] as? Map<*, *>
            ?: error("Missing message in LLM response")
        val content = message["content"]
            ?: error("Missing content in LLM response")

        val structured = when (content) {
            is Map<*, *> -> content
            is String -> {
                val extracted = JsonExtractor.extract(content)
                SimpleJson.parse(extracted) as? Map<*, *>
                    ?: error("message.content JSON must be an object")
            }
            else -> error("Unsupported message.content type: ${content::class.java.simpleName}")
        }

        return NavigationIntentJson.decode(structured)
    }
}
