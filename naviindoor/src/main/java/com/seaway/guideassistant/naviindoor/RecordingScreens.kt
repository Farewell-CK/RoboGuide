package com.seaway.guideassistant.naviindoor

import android.os.SystemClock
import android.view.WindowManager
import android.widget.*
import com.seaway.guideassistant.naviindoor.core.*
import com.seaway.guideassistant.naviindoor.data.*
import java.io.File

internal fun NativeIndoorActivity.chooseRecording() {
    val user = profile ?: return showHome()
    require(user.admin)
    if (nodes.size < 2) { message("请先在地图上添加至少两个地点。"); return }
    screen("录制路线") { selectedMap?.let { showMap(it.id) } ?: showMaps() }
    body.label("选择起点与终点；录制方向需要与实际导航方向一致。")
    fun selector(title: String): Spinner {
        body.label(title)
        return Spinner(this).apply { adapter = ArrayAdapter(this@chooseRecording, android.R.layout.simple_spinner_dropdown_item, nodes.map { it.name }) }.also { body.addView(it) }
    }
    val start = selector("起点")
    val end = selector("终点").apply { setSelection(1) }
    body.button("新建录制草稿") {
        val from = nodes[start.selectedItemPosition]; val to = nodes[end.selectedItemPosition]
        require(from.id != to.id) { "起点与终点不能相同" }
        ask("路线名称", from.name + " → " + to.name) { name ->
            val created = RouteDraft.create(draftRoot, name, from.id, to.id, user, api.connection.database)
            showRecording(created)
        }
    }
}

internal fun NativeIndoorActivity.showRecording(routeDraft: RouteDraft) {
    screen("路线录制：" + routeDraft.manifest.name) {
        confirm("保留草稿并返回", "已采集的画面保存在本机，之后可继续录制或上传。") { showDrafts() }
    }
    draft = routeDraft
    resumeLive = { showRecording(routeDraft) }
    val token = gate.generation
    window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
    body.label("画面先保存在本机，不依赖实时网络。走到拐角请转向后补拍，终点请补拍清晰画面。")
    headingText = body.label("等待罗盘", 14f)
    val count = body.label("已采集 " + routeDraft.manifest.samples.size + " 个点", 23f)
    var pendingTurn: Turn? = null
    var savePending = false
    fun sample(turn: Turn? = null, complete: (() -> Unit)? = null) {
        if (!gate.accepts(token) || frameBusy || savePending || busy) return
        val heading = sensors.heading
        if (heading == null) { status.text = "罗盘未就绪，请保持手机握法与导航时一致。"; return }
        frameBusy = true
        val capturedTurn = turn ?: pendingTurn
        camera.take({ bytes ->
            if (!gate.accepts(token)) return@take
            savePending = true
            io.execute {
                val result = runCatching { routeDraft.append(bytes, heading, capturedTurn) }
                runOnUiThread {
                    if (!gate.accepts(token)) return@runOnUiThread
                    frameBusy = false; savePending = false
                    result.fold({
                        pendingTurn = null
                        count.text = "已采集 " + routeDraft.manifest.samples.size + " 个点"
                        status.text = "采样已保存到手机。"
                        complete?.invoke()
                    }, { status.text = errorText(it); recording = false })
                }
            }
        }, { error -> if (gate.accepts(token)) { frameBusy = false; status.text = errorText(error) } })
    }
    preview {
        fun tick() {
            if (!gate.accepts(token)) return
            if (recording) sample()
            handler.postDelayed({ tick() }, 2500)
        }
        tick()
    }
    val toggle = body.button("开始／继续录制") { }
    toggle.setOnClickListener {
        if (busy) return@setOnClickListener
        recording = !recording
        toggle.text = if (recording) "暂停录制" else "开始／继续录制"
        status.text = if (recording) "正在录制，每约2.5秒采集一帧。" else "已暂停，草稿保留。"
        if (recording) { speech.speak("开始录制，请从起点沿路线行走。", force = true); sample() }
    }
    body.button("立即补拍一个直行点") { pendingTurn = Turn.straight; sample(Turn.straight) }
    body.button("已左转，补拍转弯后画面") { pendingTurn = Turn.left; sample(Turn.left) }
    body.button("已右转，补拍转弯后画面") { pendingTurn = Turn.right; sample(Turn.right) }
    body.button("已到终点，补拍并结束") {
        if (frameBusy || savePending) { message("正在保存采样，请稍后再点终点确认。"); return@button }
        recording = false
        sample(complete = { showDraftDetail(routeDraft) })
    }
    body.button("结束录制并查看草稿") {
        if (frameBusy || savePending) { message("正在保存采样，请稍后重试。"); return@button }
        recording = false; showDraftDetail(routeDraft)
    }
}

internal fun NativeIndoorActivity.showDrafts() {
    val user = profile ?: return showHome()
    screen("本地路线草稿") { showHome() }
    work("读取本地草稿…", {
        draftRoot.listFiles()?.filter { File(it, "manifest.json").isFile }?.mapNotNull { runCatching { RouteDraft.load(it) }.getOrNull() }
            ?.filter { it.manifest.ownerId == user.id && it.manifest.database == api.connection.database.trimEnd('/') } ?: emptyList()
    }) { drafts ->
        if (drafts.isEmpty()) body.label("当前账号在此项目没有本地录制草稿。")
        drafts.sortedByDescending { it.manifest.createdAt }.forEach { item ->
            body.button(item.manifest.name + " · " + item.manifest.samples.size + "点" + if (File(item.directory, "uploaded").isFile) " · 已上传" else "") { showDraftDetail(item) }
        }
    }
}

internal fun NativeIndoorActivity.showDraftDetail(routeDraft: RouteDraft) {
    val user = profile ?: return showHome()
    screen("路线草稿") { showDrafts() }
    body.label(routeDraft.manifest.name, 24f)
    body.label("已保存 " + routeDraft.manifest.samples.size + " 个采样点；已提取 " + routeDraft.manifest.samples.count { it.embedding != null } + " 个特征。")
    body.label("提取或上传中断后，可在这里重试，沿用同一条路线和采样ID。")
    if (user.admin) {
        body.button("继续录制") {
            require(!File(routeDraft.directory, "uploaded").isFile) { "此路线已上传，请新建草稿录制，避免修改已在使用的路线" }
            showRecording(routeDraft)
        }
        body.button("提取特征并上传／重试") {
            require(routeDraft.manifest.samples.size >= 2) { "至少录制两个点后才能上传" }
            val token = gate.generation
            work("提取画面特征…", {
                val path = routeDraft.process(api, user) { done, total -> runOnUiThread { if (gate.accepts(token)) status.text = "提取特征 " + done + " / " + total } }
                api.savePath(path, user)
                val endpointSamples = listOf(routeDraft.manifest.samples.first() to path.startId, routeDraft.manifest.samples.last() to path.endId)
                endpointSamples.forEach { (raw, nodeId) ->
                    api.write("place_embeddings", obj("id" to raw.id, "node_id" to nodeId,
                        "place_name" to (nodes.firstOrNull { it.id == nodeId }?.name ?: routeDraft.manifest.name),
                        "embedding" to NaviJson.gson.toJson(raw.embedding), "user_id" to user.id,
                        "organization_id" to user.organizationId, "created_at" to raw.timestamp), upsert = true)
                    api.uploadReference(raw.id, File(routeDraft.directory, raw.filename).readBytes())
                }
                File(routeDraft.directory, "uploaded").writeText(java.time.Instant.now().toString())
                path
            }) { path ->
                diagnostic("route_uploaded", mapOf("path_id" to path.id, "samples" to path.waypoints.size))
                status.text = "上传完成，后台已核实 " + path.waypoints.size + " 个连续采样点。返回地图重新加载后可测试导航。"
                speech.speak("路线已完整上传。", force = true)
            }
        }
    }
    routeDraft.manifest.samples.forEachIndexed { i, raw -> body.label("第" + (i + 1) + "点 · " + raw.turn.name + " · 朝向 " + "%.0f°".format(raw.heading), 14f) }
    body.button("返回地图") { selectedMap?.let { showMap(it.id) } ?: showMaps() }
}
