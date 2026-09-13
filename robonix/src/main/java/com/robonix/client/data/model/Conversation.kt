package com.robonix.client.data.model

data class ChatMessage(
    val id: String,
    val role: MessageRole,
    val text: String,
    val meta: String = "",
    val planRound: Int? = null,
    val attachments: List<Attachment> = emptyList(),
    val timestamp: Long = System.currentTimeMillis(),
)

enum class MessageRole { User, Agent, Status, Error }

data class Attachment(
    val name: String,
    val mediaType: String,
    val size: Long,
    val dataUrl: String,
)
