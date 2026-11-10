package com.seaway.guideassistant.archive

import android.text.InputFilter
import android.text.InputType
import android.text.format.DateFormat
import android.view.View
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Spinner
import android.widget.TextView
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import com.elabrador.mobilenavigation.archive.NavigationArchive
import org.json.JSONArray
import org.json.JSONObject
import java.util.Date

/** Optional human observations. Unconfirmed results are never converted to false. */
internal object ArchiveExperimentDialog {
    private val arrivalValues = listOf("unconfirmed", "arrived", "not_arrived")
    private val arrivalLabels = listOf("未确认", "人工确认已到达", "人工确认未到达")
    private val takeoverValues = listOf("unconfirmed", "yes", "no")
    private val takeoverLabels = listOf("未确认", "发生过人工接管", "人工确认未接管")

    /** onSave performs IO off the UI thread, then completes on the UI thread. */
    fun show(
        activity: AppCompatActivity,
        sessionId: String,
        startedAtMillis: Long,
        endedAtMillis: Long,
        history: JSONArray,
        onSave: (JSONObject, (String?) -> Unit) -> Unit
    ) {
        val latestRecord = history.optJSONObject(history.length() - 1)
        val latest = latestRecord?.optJSONObject("annotation") ?: latestRecord?.optJSONObject("data") ?: latestRecord
        val active = sessionId == NavigationArchive.activeSessionId()
        val root = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(activity, 20), dp(activity, 8), dp(activity, 20), dp(activity, 16))
        }
        root.addView(TextView(activity).apply {
            text = buildString {
                if (startedAtMillis > 0) append(DateFormat.format("yyyy-MM-dd HH:mm:ss", Date(startedAtMillis)))
                append("\n所有标注均为可选项。保存后进入这次导航档案；已导出的 ZIP 需要重新导出。")
            }
        })
        if (history.length() > 0) {
            root.addView(TextView(activity).apply {
                text = "下面显示最近一次标注，保存时会新增一条并保留历史。"
                setPadding(0, dp(activity, 8), 0, 0)
            })
            root.addView(Button(activity).apply {
                text = "查看标注历史（${history.length()}）"
                setOnClickListener { showHistory(activity, history) }
            })
        }

        val experimentId = textField(activity, root, "实验编号", 128, singleLine = true).apply {
            setText(latest?.optString("experiment_id")?.takeIf { it.isNotBlank() } ?: sessionId)
        }
        val scene = textField(activity, root, "场景备注（可选）", 1000).apply {
            setText(latest?.optString("scene_notes").orEmpty())
            hint = "例如：室内走廊、室外台阶、光照变化"
        }
        val arrival = choices(activity, root, "人工到达确认", arrivalLabels,
            arrivalValues.indexOf(latest?.optString("arrival_confirmation")).coerceAtLeast(0))
        val takeover = choices(activity, root, "人工接管确认", takeoverLabels,
            takeoverValues.indexOf(latest?.optString("manual_takeover")).coerceAtLeast(0))
        val failure = textField(activity, root, "失败原因（可选）", 1000).apply {
            setText(latest?.optString("failure_reason").orEmpty())
            hint = "没有失败或尚不明确时可留空"
        }
        val notes = textField(activity, root, "其他结果或说明（可选）", 2000).apply {
            setText(latest?.optString("notes").orEmpty())
        }
        val eventSeconds = textField(activity, root, "上述情况发生于导航开始后多少秒（可选）", 24, singleLine = true).apply {
            inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_DECIMAL
            hint = if (active) "留空使用保存时刻" else "不确定可留空"
            if (latest != null && !latest.isNull("relative_seconds")) setText(latest.opt("relative_seconds").toString())
        }
        val errorText = TextView(activity).apply {
            visibility = View.GONE
            accessibilityLiveRegion = View.ACCESSIBILITY_LIVE_REGION_POLITE
        }
        root.addView(errorText)
        val dialog = AlertDialog.Builder(activity)
            .setTitle("实验标注 / 结果")
            .setView(ScrollView(activity).apply { addView(root) })
            .setNegativeButton("取消", null)
            .setPositiveButton("保存", null)
            .create()
        dialog.setOnShowListener {
            val save = dialog.getButton(AlertDialog.BUTTON_POSITIVE)
            save.setOnClickListener {
                errorText.visibility = View.GONE
                eventSeconds.error = null
                val secondsText = eventSeconds.text.toString().trim()
                val seconds = secondsText.takeIf { it.isNotEmpty() }?.toDoubleOrNull()
                val duration = if (endedAtMillis >= startedAtMillis && endedAtMillis > 0) (endedAtMillis - startedAtMillis) / 1000.0 else null
                if (secondsText.isNotEmpty() && (seconds == null || !seconds.isFinite() || seconds < 0)) {
                    eventSeconds.error = "请输入大于或等于 0 的秒数，或留空"
                    eventSeconds.requestFocus()
                    return@setOnClickListener
                }
                if (seconds != null && duration != null && seconds > duration + 1.0) {
                    eventSeconds.error = "发生时间不能晚于这次导航结束时间（约 ${duration.toLong()} 秒）"
                    eventSeconds.requestFocus()
                    return@setOnClickListener
                }
                val stillActive = sessionId == NavigationArchive.activeSessionId()
                val payload = JSONObject()
                    .put("experiment_id", experimentId.text.toString().trim().ifBlank { sessionId })
                    .put("scene_notes", scene.text.toString().trim())
                    .put("arrival_confirmation", arrivalValues[arrival.selectedItemPosition])
                    .put("manual_takeover", takeoverValues[takeover.selectedItemPosition])
                    .put("failure_reason", failure.text.toString().trim())
                    .put("notes", notes.text.toString().trim())
                    .put("relative_seconds", seconds ?: JSONObject.NULL)
                    .put("time_source", if (seconds != null) "manual" else if (stillActive) "captured_while_recording" else "unspecified")
                save.isEnabled = false
                dialog.getButton(AlertDialog.BUTTON_NEGATIVE).isEnabled = false
                dialog.setCancelable(false)
                onSave(payload) { error ->
                    if (error == null) {
                        dialog.dismiss()
                    } else {
                        save.isEnabled = true
                        dialog.getButton(AlertDialog.BUTTON_NEGATIVE).isEnabled = true
                        dialog.setCancelable(true)
                        errorText.text = error
                        errorText.visibility = View.VISIBLE
                    }
                }
            }
        }
        dialog.show()
    }

    private fun textField(activity: AppCompatActivity, root: LinearLayout, title: String, maxLength: Int, singleLine: Boolean = false): EditText {
        val field = EditText(activity).apply {
            id = View.generateViewId()
            inputType = InputType.TYPE_CLASS_TEXT or if (singleLine) 0 else InputType.TYPE_TEXT_FLAG_MULTI_LINE
            setSingleLine(singleLine)
            filters = arrayOf(InputFilter.LengthFilter(maxLength))
            if (!singleLine) minLines = 2
        }
        root.addView(TextView(activity).apply {
            text = title
            labelFor = field.id
            setPadding(0, dp(activity, 12), 0, 0)
        })
        root.addView(field)
        return field
    }

    private fun choices(activity: AppCompatActivity, root: LinearLayout, title: String, labels: List<String>, selected: Int): Spinner {
        val field = Spinner(activity).apply {
            id = View.generateViewId()
            adapter = ArrayAdapter(activity, android.R.layout.simple_spinner_item, labels).apply {
                setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item)
            }
            setSelection(selected)
            minimumHeight = dp(activity, 48)
        }
        root.addView(TextView(activity).apply {
            text = title
            labelFor = field.id
            setPadding(0, dp(activity, 12), 0, 0)
        })
        root.addView(field)
        return field
    }

    private fun showHistory(activity: AppCompatActivity, history: JSONArray) {
        val text = buildString {
            for (index in history.length() - 1 downTo 0) {
                val record = history.optJSONObject(index) ?: continue
                val note = record.optJSONObject("annotation") ?: record.optJSONObject("data") ?: record
                if (isNotEmpty()) append("\n\n")
                append("第 ${index + 1} 条标注")
                val recorded = record.optLong("recorded_at_ms", record.optLong("wall_time_ms"))
                if (recorded > 0) append(" · ${DateFormat.format("yyyy-MM-dd HH:mm:ss", Date(recorded))}")
                append("\n实验编号：${note.optString("experiment_id")}")
                note.optString("scene_notes").takeIf { it.isNotBlank() }?.let { append("\n场景：$it") }
                val arrived = arrivalValues.indexOf(note.optString("arrival_confirmation")).coerceAtLeast(0)
                val takenOver = takeoverValues.indexOf(note.optString("manual_takeover")).coerceAtLeast(0)
                append("\n${arrivalLabels[arrived]}；${takeoverLabels[takenOver]}")
                if (!note.isNull("relative_seconds")) append("\n发生于导航开始后 ${note.opt("relative_seconds")} 秒")
                else append("\n发生时间未确认")
                note.optString("failure_reason").takeIf { it.isNotBlank() }?.let { append("\n失败原因：$it") }
                note.optString("notes").takeIf { it.isNotBlank() }?.let { append("\n说明：$it") }
            }
        }
        AlertDialog.Builder(activity)
            .setTitle("实验标注历史")
            .setMessage(text)
            .setPositiveButton("关闭", null)
            .show()
    }

    private fun dp(activity: AppCompatActivity, value: Int) = (value * activity.resources.displayMetrics.density).toInt()
}
