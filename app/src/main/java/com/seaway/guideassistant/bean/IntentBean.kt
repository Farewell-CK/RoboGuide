package com.seaway.guideassistant.bean

/**
 * 大模型意图识别请求/响应，对应预留的LLM意图识别接口
 */
data class IntentRequest(val text: String)

data class IntentResponse(val intent: String, val confidence: Float? = null)
