package com.seaway.guideassistant.llm

data class IntentRecognizerConfig(
    val apiKey: String,
    val endpoint: String = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
    val model: String = "qwen-plus",
    val timeoutSeconds: Long = 20L
)
