package com.robonix.client.data.local

import android.content.Context
import com.robonix.client.AppLog
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import javax.inject.Inject
import javax.inject.Singleton

data class Conversation(
    val id: String,
    val title: String = "",
    val updatedAt: Long = System.currentTimeMillis(),
    val messagesJson: String = "[]",
    val timelineJson: String = "[]",
    val planJson: String? = null,
    val planRecordsJson: String = "[]",
    val batchesJson: String = "[]",
    val nodeStatesJson: String = "{}",
)

@Singleton
class ConversationStore @Inject constructor(
    @ApplicationContext private val context: Context,
) {
    private val file: File
        get() = File(context.filesDir, "robonix_conversations.json")

    companion object {
        const val MAX_HISTORY = 30
    }

    suspend fun loadAll(): List<Conversation> = withContext(Dispatchers.IO) {
        try {
            if (!file.exists()) return@withContext emptyList()
            val raw = file.readText()
            val arr = JSONArray(raw)
            (0 until arr.length()).map { i ->
                val obj = arr.getJSONObject(i)
                Conversation(
                    id = obj.optString("id", ""),
                    title = obj.optString("title", ""),
                    updatedAt = obj.optLong("updatedAt", 0),
                    messagesJson = obj.optString("messages", "[]"),
                    timelineJson = obj.optString("timeline", "[]"),
                    planJson = obj.optString("plan", "null").takeIf { it != "null" },
                    planRecordsJson = obj.optString("planRecords", "[]"),
                    batchesJson = obj.optString("batches", "[]"),
                    nodeStatesJson = obj.optString("nodeStates", "{}"),
                )
            }
        } catch (e: Exception) {
            AppLog.write("CONV", "Failed to load conversations: ${e.message}", e)
            emptyList()
        }
    }

    suspend fun saveAll(conversations: List<Conversation>) = withContext(Dispatchers.IO) {
        try {
            val arr = JSONArray()
            conversations.take(MAX_HISTORY).forEach { c ->
                val obj = JSONObject()
                obj.put("id", c.id)
                obj.put("title", c.title)
                obj.put("updatedAt", c.updatedAt)
                obj.put("messages", JSONArray(c.messagesJson))
                obj.put("timeline", JSONArray(c.timelineJson))
                if (c.planJson != null) obj.put("plan", JSONObject(c.planJson))
                obj.put("planRecords", JSONArray(c.planRecordsJson))
                obj.put("batches", JSONArray(c.batchesJson))
                obj.put("nodeStates", JSONObject(c.nodeStatesJson))
                arr.put(obj)
            }
            file.writeText(arr.toString())
        } catch (e: Exception) {
            AppLog.write("CONV", "Failed to save conversations: ${e.message}", e)
        }
    }

    suspend fun delete(id: String) {
        val all = loadAll().toMutableList()
        all.removeAll { it.id == id }
        saveAll(all)
    }

    suspend fun clearAll() {
        file.delete()
    }
}
