package com.seaway.guideassistant.naviindoor

import android.graphics.BitmapFactory
import android.os.SystemClock
import android.view.View
import android.view.WindowManager
import android.widget.*
import androidx.appcompat.app.AlertDialog
import com.seaway.guideassistant.naviindoor.core.*
import com.seaway.guideassistant.naviindoor.data.*
import java.util.UUID

internal fun NativeIndoorActivity.showLocalization() {
    screen("定位当前位置") { selectedMap?.let { showMap(it.id) } ?: showMaps() }
    val token = gate.generation
    val mapNodes = nodes.toList()
    resumeLive = { showLocalization() }
    window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
    guidanceText = body.label("正在准备定位，请保持镜头朝前。", 23f)
    headingText = body.label("等待罗盘", 14f)
    debugText = body.label("", 14f)
    speech.speak("开始定位，请缓慢转动手机，使镜头对准周围环境。", force = true)
    body.button("复制诊断记录") { copyDiagnostics() }
    work("读取本地图定位参考…", { api.references(mapNodes) }) { references ->
        if (references.isEmpty()) { status.text = "该地图尚无可用定位参考，请先由管理员采集点位参考画面。"; return@work }
        preview {
            fun next() {
                if (!gate.accepts(token) || isFinishing) return
                if (frameBusy) return
                frameBusy = true
                val capturedAt = SystemClock.elapsedRealtime()
                camera.take({ bytes ->
                    if (!gate.accepts(token)) return@take
                    io.execute {
                        val result = runCatching { rankLocations(api.encode(bytes), references) }
                        runOnUiThread {
                            if (!gate.accepts(token)) return@runOnUiThread
                            frameBusy = false
                            val elapsed = SystemClock.elapsedRealtime() - capturedAt
                            result.fold({ candidates ->
                                diagnostic("localization", mapOf("request_ms" to elapsed, "candidates" to candidates.map { mapOf("node" to it.reference.nodeId, "name" to it.reference.name, "score" to it.score) }))
                                debugText?.text = "特征请求：" + elapsed + " ms\n" + candidates.joinToString("\n") { it.reference.name + "：" + "%.3f".format(it.score) }
                                if (elapsed <= 12_000 && candidates.firstOrNull()?.score?.let { it > .5 } == true) {
                                    showLocalized(candidates); return@runOnUiThread
                                }
                                status.text = if (elapsed > 12_000) "本次画面结果过期，正在重新拍摄，请检查网络。" else "尚未匹配，请改变镜头方向后继续识别。"
                            }, { status.text = errorText(it); speech.speak("定位请求未完成，请检查网络后继续。") })
                            handler.postDelayed({ next() }, 800)
                        }
                    }
                }, { error ->
                    if (gate.accepts(token)) { frameBusy = false; status.text = errorText(error); handler.postDelayed({ next() }, 1500) }
                })
            }
            next()
        }
    }
}

private fun NativeIndoorActivity.showLocalized(candidates: List<LocationCandidate>) {
    screen("确认定位结果") { showLocalization() }
    val winner = candidates.first()
    body.label("识别到：" + winner.reference.name, 26f)
    body.label("相似度 " + "%.3f".format(winner.score) + "。请确认实际位置，再选择目的地。")
    speech.speak("识别到" + winner.reference.name + "，请确认当前位置。", force = true)
    fun accept(candidate: LocationCandidate) {
        currentNode = nodes.firstOrNull { it.id == candidate.reference.nodeId }
        if (currentNode == null) { message("该定位点不在当前地图，请重新定位。"); return }
        showDestinations()
    }
    body.button("当前位置正确，选择目的地") { accept(winner) }
    body.button("查看参考图片") {
        work("读取录制参考图片…", {
            runCatching { api.referenceImage(winner.reference.id) }.getOrElse { api.referenceImage(winner.reference.nodeId) }
        }) { bytes ->
            val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
            val image = ImageView(this).apply { setImageBitmap(bitmap); adjustViewBounds = true; contentDescription = "定位点参考照片" }
            AlertDialog.Builder(this).setTitle(winner.reference.name).setView(image).setPositiveButton("关闭", null).show()
        }
    }
    candidates.drop(1).filter { it.score > .5 }.forEach { candidate ->
        body.button("实际在“" + candidate.reference.name + "”（" + "%.3f".format(candidate.score) + "）") { accept(candidate) }
    }
    body.button("重新定位") { showLocalization() }
}

internal fun NativeIndoorActivity.showDestinations() {
    val origin = currentNode ?: return showLocalization()
    screen("选择目的地") { selectedMap?.let { showMap(it.id) } ?: showMaps() }
    body.label("起点：" + origin.name)
    val routes = nodes.filter { it.id != origin.id }.mapNotNull { destination -> planRoute(paths, origin.id, destination.id)?.let { destination to it } }
    if (routes.isEmpty()) body.label("此点位没有可用的出发路线，请先录制相应方向的路线。")
    routes.forEach { (destination, path) ->
        body.button(destination.name + " · " + path.waypoints.size + " 个参考点") {
            navigationBaseSteps = sensors.steps
            val session = NavigationSession(path, destination.name)
            showNavigation(session)
        }
    }
    body.button("重新定位") { showLocalization() }
}

internal fun NativeIndoorActivity.showNavigation(session: NavigationSession) {
    screen("导航至 " + session.destination) { confirm("结束本次导航", "结束后可重新定位或选择目的地。") { session.stop(); showDestinations() } }
    navigation = session
    resumeLive = { showNavigation(session) }
    val token = gate.generation
    window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
    guidanceText = body.label(if (session.phase == NavigationPhase.orienting) "请对齐出发方向。" else session.guidance(), 25f)
    guidanceText?.accessibilityLiveRegion = View.ACCESSIBILITY_LIVE_REGION_POLITE
    arrowText = body.label("↑", 64f).apply { gravity = android.view.Gravity.CENTER; setTextIsSelectable(false) }
    headingText = body.label("等待方向信息", 14f)
    progressText = body.label("已确认 " + session.target + " / " + session.path.waypoints.size + " 个参考点")
    obstacleText = body.label("视觉障碍提醒：仅画面识别，不测距、不自动绕行。", 15f)
    body.addView(CheckBox(this).apply {
        text = "开启室内障碍提醒"; isChecked = obstacleEnabled
        setOnCheckedChangeListener { _, checked ->
            obstacleEnabled = checked; obstacleEpoch++; obstacleConfirmation.clear(); obstacleLastAt = 0
            obstacleText?.text = if (checked) "等待新画面；不测距、不自动绕行。" else "障碍提醒已关闭。"
        }
    })
    debugText = body.label("正在准备相机…", 13f)
    body.button("我已到位，重新识别") {
        session.confirmPosition(); showNavigation(session)
        message("已记录到位确认，仍需新画面匹配通过。")
    }
    body.button("重播当前提示") { speech.speak(guidanceText?.text.toString(), force = true) }
    body.button("复制诊断记录") { copyDiagnostics() }
    preview {
        fun next() {
            if (!gate.accepts(token) || session.phase == NavigationPhase.arrived || session.phase == NavigationPhase.stopped) return
            if (session.phase == NavigationPhase.orienting) {
                renderNavigation(session.orient(sensors.heading)); handler.postDelayed({ next() }, 800); return
            }
            val capturedAt = SystemClock.elapsedRealtime()
            val heading = sensors.heading
            val movement = (sensors.steps - navigationBaseSteps).coerceAtLeast(0)
            val target = session.target
            camera.take({ bytes ->
                if (!gate.accepts(token)) return@take
                submitObstacle(bytes, capturedAt, token)
                handler.postDelayed({ next() }, 800)
                if (frameBusy || target != session.target) return@take
                frameBusy = true
                io.execute {
                    val result = runCatching { api.encode(bytes) }
                    runOnUiThread {
                        if (!gate.accepts(token)) return@runOnUiThread
                        frameBusy = false
                        val elapsed = SystemClock.elapsedRealtime() - capturedAt
                        result.fold({ embedding ->
                            if (elapsed > 12_000) {
                                session.interrupt(); status.text = "特征请求耗时过长，已丢弃旧画面并重新拍摄。"
                            } else {
                                val update = session.observe(embedding, capturedAt, movement, heading)
                                diagnostic("route_match", mapOf("target" to target, "forward_limit" to update.limit, "scores" to update.scores,
                                    "decision" to update.decision?.reason?.name, "request_ms" to elapsed, "heading" to heading, "movement" to movement))
                                debugText?.text = "参考范围 " + (target + 1) + "～" + (update.limit + 1) + "；特征请求 " + elapsed + " ms\n" +
                                    (update.decision?.message ?: "") + "\n" + (target..update.limit).joinToString("；") { "第" + (it + 1) + "点 " + "%.3f".format(update.scores[it] ?: Double.NaN) }
                                status.text = ""
                                renderNavigation(update)
                            }
                        }, { session.interrupt(); status.text = errorText(it); speech.speak("导航请求未完成，请停下检查网络。") })
                    }
                }
            }, { error ->
                if (gate.accepts(token)) { status.text = errorText(error); handler.postDelayed({ next() }, 1500) }
            })
        }
        next()
    }
    fun expire() {
        if (!gate.accepts(token)) return
        if (obstacleLastAt != 0L && SystemClock.elapsedRealtime() - obstacleLastAt > 3000) {
            obstacleLastAt = 0; obstacleConfirmation.clear()
            obstacleText?.text = "障碍结果已过期，等待更新画面；请留意实际环境。"
        }
        handler.postDelayed({ expire() }, 250)
    }
    expire()
    updateHeading()
}

internal fun NativeIndoorActivity.renderNavigation(update: NavigationUpdate) {
    if (update.phase == NavigationPhase.arrived) { showArrival(update.text); return }
    guidanceText?.text = update.text
    val total = navigation?.path?.waypoints?.size ?: return
    progressText?.text = "路线识别进度 " + (update.accepted * 100 / total) + "% · 已确认 " + update.accepted + " / " + total + " 个参考点"
    speech.speak(update.text)
}
private fun NativeIndoorActivity.showArrival(text: String) {
    val destinationId = navigation?.path?.endId
    screen("已到达目的地") { selectedMap?.let { showMap(it.id) } ?: showMaps() }
    currentNode = nodes.firstOrNull { it.id == destinationId }
    body.label("✓", 72f)
    body.label(text, 27f)
    body.label("路线识别进度 100%")
    body.button("选择下一个目的地") { showDestinations() }
    body.button("返回地图") { selectedMap?.let { showMap(it.id) } ?: showMaps() }
    body.button("复制诊断记录") { copyDiagnostics() }
    speech.speak(text, force = true)
}

private fun NativeIndoorActivity.submitObstacle(bytes: ByteArray, capturedAt: Long, token: Int) {
    if (!obstacleEnabled || obstacleBusy) return
    obstacleBusy = true
    val epoch = obstacleEpoch
    val id = UUID.randomUUID().toString()
    obstacleWorker.execute {
        val result = runCatching { api.obstacle(bytes, id) }
        runOnUiThread {
            obstacleBusy = false
            if (!gate.accepts(token) || epoch != obstacleEpoch || !obstacleEnabled) return@runOnUiThread
            if (SystemClock.elapsedRealtime() - capturedAt > 3000) {
                obstacleConfirmation.clear(); obstacleText?.text = "障碍结果已过期，等待新画面。"; return@runOnUiThread
            }
            result.fold({ observation ->
                val confirmed = obstacleConfirmation.update(observation.detections, capturedAt)
                obstacleLastAt = capturedAt
                val text = confirmed.firstOrNull()?.message ?: if (observation.detections.isNotEmpty()) "疑似障碍确认1/2，请留意实际环境。" else observation.message
                obstacleText?.text = text
                obstacleText?.setTextColor(if (confirmed.isNotEmpty()) 0xFFB45309.toInt() else 0xFF475569.toInt())
                diagnostic("obstacle", mapOf("frame_id" to id, "message" to text, "detections" to observation.detections, "inference_ms" to observation.inferenceMs))
                if (confirmed.isNotEmpty()) speech.speak(text, obstacle = true)
            }, {
                obstacleConfirmation.clear(); obstacleLastAt = 0
                obstacleText?.text = "障碍识别暂不可用，请留意实际环境。"
            })
        }
    }
}
