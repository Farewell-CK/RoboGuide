package com.seaway.guideassistant.llm

object JsonExtractor {
    /**
     * 尽量兼容模型偶尔返回 ```json ... ``` 的情况。
     */
    fun extract(raw: String): String {
        val text = raw.trim()
            .removePrefix("```json")
            .removePrefix("```")
            .removeSuffix("```")
            .trim()

        val start = text.indexOf('{')
        val end = text.lastIndexOf('}')
        require(start >= 0 && end > start) {
            "LLM response does not contain a JSON object: $raw"
        }
        return text.substring(start, end + 1)
    }
}
