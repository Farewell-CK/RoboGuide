package com.seaway.guideassistant.llm

import com.orhanobut.logger.Logger
import com.seaway.guideassistant.base.Constant.timeoutMillis
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URI

class QwenIntentRecognizer(
    private val config: IntentRecognizerConfig
) {
    fun parse(userText: String): NavigationIntent {
        require(userText.isNotBlank()) { "userText cannot be blank" }
        require(config.apiKey.isNotBlank()) { "apiKey cannot be blank" }

        val requestBody = SimpleJson.stringify(
            linkedMapOf(
                "model" to config.model,
                "temperature" to 0,
                "messages" to listOf(
                    mapOf("role" to "system", "content" to IntentPrompt.systemPrompt),
                    mapOf("role" to "user", "content" to userText.trim())
                )
            )
        )

        val connection = (URI(config.endpoint).toURL().openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            connectTimeout = timeoutMillis
            readTimeout = timeoutMillis
            doOutput = true
            setRequestProperty("Authorization", "Bearer ${config.apiKey}")
            setRequestProperty("Content-Type", "application/json; charset=utf-8")
            setRequestProperty("Accept", "application/json")
        }

        try {
            connection.outputStream.use { it.write(requestBody.toByteArray(Charsets.UTF_8)) }
            val status = connection.responseCode
            val stream = if (status in 200..299) connection.inputStream else connection.errorStream
            val responseBody = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }.orEmpty()
            Logger.i("LLm responseBody: $responseBody")
            if (status !in 200..299) {
                throw IOException("LLM request failed: HTTP $status, body=$responseBody")
            }

            val result = try {
                QwenResponseParser.parseIntent(responseBody)
            } catch (e: Exception) {
                throw IOException("Failed to parse LLM response: $responseBody", e)
            }

            NavigationIntentValidator.validate(result)
            return result
        } finally {
            connection.disconnect()
        }
    }
}
