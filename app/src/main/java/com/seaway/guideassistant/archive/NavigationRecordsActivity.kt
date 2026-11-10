package com.seaway.guideassistant.archive

import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.InputType
import android.text.format.DateFormat
import android.text.format.Formatter
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.app.AlertDialog
import com.elabrador.mobilenavigation.archive.NavigationArchive
import org.json.JSONArray
import org.json.JSONObject
import java.util.Date
import java.util.concurrent.Executors

/** Local archive browser. Disk IO/export run in one background worker; recording is independent. */
class NavigationRecordsActivity : AppCompatActivity() {
    private val io = Executors.newSingleThreadExecutor()
    private val handler = Handler(Looper.getMainLooper())
    private lateinit var rows: LinearLayout
    private lateinit var endpoint: EditText
    private lateinit var token: EditText
    private lateinit var clearToken: CheckBox
    private lateinit var status: TextView
    private var visible = false
    private var loading = false
    private var exportingSession: String? = null
    private var lastRows = ""

    private val createZip = registerForActivityResult(ActivityResultContracts.CreateDocument("application/zip")) { uri ->
        val id = exportingSession
        exportingSession = null
        if (uri != null && id != null) {
            status.text = "正在导出……"
            io.execute {
                try {
                    contentResolver.openOutputStream(uri, "w").use { output ->
                        requireNotNull(output) { "无法打开保存位置" }
                        NavigationArchive.exportZipTo(id, output)
                    }
                    runOnUiThread { status.text = "已导出导航档案" }
                } catch (error: Exception) {
                    runOnUiThread { status.text = "导出失败：${error.message.orEmpty()}" }
                }
            }
        }
    }

    private val refresh = object : Runnable {
        override fun run() {
            if (!visible) return
            loadSessions()
            handler.postDelayed(this, 2000)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        exportingSession = savedInstanceState?.getString("exporting_session")
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(16), dp(16), dp(24))
        }
        setContentView(ScrollView(this).apply { addView(root) })
        root.addView(TextView(this).apply { text = "导航数据记录"; textSize = 24f })
        root.addView(TextView(this).apply {
            text = "每次导航自动保存在本机。结束后可导出，或上传到你配置的平台；包含压缩视频，以及每次推理配对的 RGB 关键帧和深度。"
            setPadding(0, dp(12), 0, dp(12))
        })
        endpoint = EditText(this).apply {
            hint = "平台地址，例如 https://data.example.com"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
            setText(NavigationArchive.uploadEndpoint())
            setSingleLine(true)
        }
        token = EditText(this).apply {
            hint = if (NavigationArchive.hasUploadToken()) "访问令牌已配置；留空保留" else "访问令牌（平台需要时填写）"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            setSingleLine(true)
            // Credential input is intentionally excluded from Activity saved state.
            isSaveEnabled = false
        }
        clearToken = CheckBox(this).apply { text = "清除已保存的访问令牌" }
        root.addView(endpoint)
        root.addView(token)
        root.addView(clearToken)
        root.addView(CheckBox(this).apply {
            text = "自动上传（导航中上传已保存片段，失败后重试）"
            isChecked = NavigationArchive.autoUploadEnabled()
            setOnCheckedChangeListener { _, value -> NavigationArchive.configureAutoUpload(value) }
        })
        root.addView(Button(this).apply {
            text = "保存平台配置"
            setOnClickListener {
                try {
                    NavigationArchive.configureUpload(endpoint.text.toString().trim(),
                        if (clearToken.isChecked) "" else token.text.toString().takeIf { it.isNotEmpty() })
                    token.setText("")
                    token.hint = if (NavigationArchive.hasUploadToken()) "访问令牌已配置；留空保留" else "访问令牌（平台需要时填写）"
                    clearToken.isChecked = false
                    status.text = if (NavigationArchive.uploadEndpoint().isBlank()) "仅保存到本机" else "平台配置已保存；选择记录上传"
                } catch (error: Exception) {
                    status.text = "配置未保存：${error.message.orEmpty()}"
                }
            }
        })
        status = TextView(this).apply {
            text = "正在读取本地记录……"
            accessibilityLiveRegion = android.view.View.ACCESSIBILITY_LIVE_REGION_POLITE
        }
        root.addView(status)
        root.addView(Button(this).apply { text = "刷新记录"; setOnClickListener { loadSessions() } })
        rows = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        root.addView(rows)
        root.addView(Button(this).apply { text = "返回导航"; setOnClickListener { finish() } })
    }

    override fun onSaveInstanceState(outState: Bundle) {
        exportingSession?.let { outState.putString("exporting_session", it) }
        super.onSaveInstanceState(outState)
    }

    override fun onResume() {
        super.onResume()
        visible = true
        handler.post(refresh)
    }

    override fun onPause() {
        visible = false
        handler.removeCallbacks(refresh)
        super.onPause()
    }

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        io.shutdown()
        super.onDestroy()
    }

    private fun loadSessions() {
        if (loading || io.isShutdown) return
        loading = true
        io.execute {
            try {
                val data = NavigationArchive.sessions()
                runOnUiThread {
                    loading = false
                    if (isDestroyed) return@runOnUiThread
                    val serialized = data.toString()
                    if (serialized != lastRows) {
                        lastRows = serialized
                        renderSessions(data)
                    }
                    if (status.text.toString() == "正在读取本地记录……") {
                        status.text = "已读取 ${data.length()} 份本地记录"
                    }
                }
            } catch (error: Exception) {
                runOnUiThread { loading = false; status.text = "读取记录失败：${error.message.orEmpty()}" }
            }
        }
    }

    private fun renderSessions(data: JSONArray) {
        rows.removeAllViews()
        if (data.length() == 0) {
            rows.addView(TextView(this).apply { text = "还没有导航记录，开始一次导航后会自动记录。" })
            return
        }
        for (index in 0 until data.length()) {
            val item = data.optJSONObject(index) ?: continue
            val id = item.optString("session_id")
            val state = item.optString("state")
            val recording = id == NavigationArchive.activeSessionId() || state == "recording"
            val card = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(0, dp(16), 0, dp(12))
            }
            val started = item.optLong("started_at_ms")
            card.addView(TextView(this).apply {
                text = if (started > 0) DateFormat.format("yyyy-MM-dd HH:mm:ss", Date(started)) else id
                textSize = 18f
            })
            val dropped = item.optLong("dropped_records", item.optLong("dropped", 0))
            card.addView(TextView(this).apply {
                text = buildString {
                    append("本地：${stateLabel(state)} · ${Formatter.formatFileSize(this@NavigationRecordsActivity, item.optLong("total_bytes"))}")
                    append("\n上传：${uploadLabel(item.optString("upload_state"))}")
                    if (dropped > 0) append("\n有 $dropped 条数据未能写入")
                    val error = item.optString("upload_error")
                    if (error.isNotBlank()) append("\n$error")
                    append("\n编号：$id")
                }
            })
            val buttons = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
            buttons.addView(Button(this).apply {
                text = "导出 ZIP"
                isEnabled = !recording
                setOnClickListener { exportingSession = id; createZip.launch("navigation-$id.zip") }
            }, LinearLayout.LayoutParams(0, -2, 1f))
            buttons.addView(Button(this).apply {
                text = "上传 / 继续上传"
                isEnabled = item.optString("upload_state") != "uploading"
                setOnClickListener {
                    if (NavigationArchive.uploadEndpoint().isBlank()) {
                        Toast.makeText(this@NavigationRecordsActivity, "请先保存平台地址", Toast.LENGTH_SHORT).show()
                    } else {
                        status.text = if (NavigationArchive.upload(id)) "已加入上传队列，网络失败后可继续上传" else "未能开始上传，请刷新后查看状态"
                        loadSessions()
                    }
                }
            }, LinearLayout.LayoutParams(0, -2, 1f))
            card.addView(buttons)
            card.addView(Button(this).apply {
                text = "实验标注 / 结果"
                setOnClickListener { editExperiment(item) }
            })
            card.addView(Button(this).apply {
                text = "删除本机记录"
                isEnabled = !recording && !item.optBoolean("local_operation_busy")
                setOnClickListener { confirmDelete(id, started, item.optLong("total_bytes")) }
            })
            rows.addView(card)
        }
    }

    private fun editExperiment(item: JSONObject) {
        if (io.isShutdown) return
        val id = item.optString("session_id")
        status.text = "正在读取实验标注……"
        io.execute {
            try {
                val history = NavigationArchive.experimentAnnotations(id)
                runOnUiThread {
                    if (isDestroyed || isFinishing) return@runOnUiThread
                    status.text = "实验标注可在导航中填写，也可结束后补充"
                    ArchiveExperimentDialog.show(this, id, item.optLong("started_at_ms"), item.optLong("ended_at_ms"), history) { note, complete ->
                        if (io.isShutdown) {
                            complete("页面已关闭，请重新打开记录页")
                        } else {
                            io.execute {
                                try {
                                    NavigationArchive.saveExperimentAnnotation(id, note)
                                    runOnUiThread {
                                        if (!isDestroyed) {
                                            complete(null)
                                            status.text = "实验标注已保存；已导出的 ZIP 需重新导出，已上传记录需重新上传"
                                            lastRows = ""
                                            loadSessions()
                                        }
                                    }
                                } catch (error: Exception) {
                                    runOnUiThread {
                                        if (!isDestroyed) complete("保存失败：${error.message.orEmpty()}")
                                    }
                                }
                            }
                        }
                    }
                }
            } catch (error: Exception) {
                runOnUiThread { if (!isDestroyed) status.text = "读取标注失败：${error.message.orEmpty()}" }
            }
        }
    }

    private fun confirmDelete(id: String, started: Long, bytes: Long) {
        val time = if (started > 0) DateFormat.format("yyyy-MM-dd HH:mm:ss", Date(started)).toString() else id
        AlertDialog.Builder(this)
            .setTitle("删除这条本机记录？")
            .setMessage("$time\n${Formatter.formatFileSize(this, bytes)}\n\n将永久删除这次记录的手机内部数据和缓存 ZIP，无法恢复。你另存到“下载”等文件夹的 ZIP，以及电脑平台上的副本会保留。")
            .setNegativeButton("取消", null)
            .setPositiveButton("删除") { _, _ ->
                status.text = "正在删除本机记录……"
                io.execute {
                    try {
                        NavigationArchive.deleteLocalSession(id)
                        runOnUiThread {
                            if (!isDestroyed) {
                                status.text = "已删除本机记录"
                                loadSessions()
                            }
                        }
                    } catch (error: Exception) {
                        runOnUiThread { if (!isDestroyed) status.text = "删除失败：${error.message.orEmpty()}" }
                    }
                }
            }
            .show()
    }

    private fun stateLabel(value: String): String = when (value) {
        "recording" -> "正在记录"
        "complete", "completed", "closed" -> "已结束"
        "interrupted" -> "异常中断后恢复"
        "incomplete" -> "已结束，存在缺失"
        "failed", "error" -> "记录异常"
        else -> value.ifBlank { "待整理" }
    }

    private fun uploadLabel(value: String): String = when (value) {
        "complete", "completed", "uploaded" -> "平台已保存并校验"
        "uploading" -> "正在上传"
        "receiving" -> "已上传当前片段，导航仍在记录"
        "queued" -> "等待上传"
        "failed", "error" -> "上传失败，可重试"
        else -> "尚未上传"
    }

    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()
}
