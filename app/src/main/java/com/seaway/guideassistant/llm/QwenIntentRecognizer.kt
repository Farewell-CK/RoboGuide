package com.seaway.guideassistant.llm

import com.orhanobut.logger.Logger
import android.os.SystemClock
import com.elabrador.mobilenavigation.archive.NavigationArchive
import org.json.JSONObject
import java.util.UUID
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

        val session = NavigationArchive.activeSessionId()
        val requestId = UUID.randomUUID().toString()
        NavigationArchive.trackProcessing(session, requestId, "cloud_intent_request")
        val started = SystemClock.elapsedRealtime()
        NavigationArchive.eventFor(session, "cloud.intent.request", NavigationArchive.json(
            "request_id", requestId, "model", config.model,
            "request", JSONObject(requestBody)
        ))
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
            val safeBody = responseBody.replace(config.apiKey, "[REDACTED]")
            val envelope = runCatching { JSONObject(safeBody) }.getOrNull()
            NavigationArchive.eventFor(session, "cloud.intent.response", NavigationArchive.json(
                "request_id", requestId, "http_status", status,
                "duration_ms", SystemClock.elapsedRealtime() - started,
                "raw_response", safeBody, "usage", envelope?.opt("usage"),
                "provider_request_id", envelope?.opt("id")
            ))
            Logger.i("LLM intent response: HTTP $status")
            if (status !in 200..299) {
                throw IOException("LLM request failed: HTTP $status, body=$responseBody")
            }

            val result = try {
                QwenResponseParser.parseIntent(responseBody)
            } catch (e: Exception) {
                throw IOException("Failed to parse LLM response: $responseBody", e)
            }

            NavigationIntentValidator.validate(result)
            NavigationArchive.eventFor(session, "cloud.intent.result", NavigationArchive.json(
                "request_id", requestId, "result", JSONObject(NavigationIntentEncoder.toJson(result))
            ))
            return result
        } catch (error: Exception) {
            NavigationArchive.eventFor(session, "cloud.intent.failure", NavigationArchive.json(
                "request_id", requestId, "duration_ms", SystemClock.elapsedRealtime() - started,
                "error_type", error.javaClass.simpleName
            ))
            throw error
        } finally {
            NavigationArchive.completeProcessing(session, requestId)
            connection.disconnect()
        }
    }
}
