package com.seaway.guideassistant.naviindoor

import android.graphics.BitmapFactory
import android.graphics.Color
import android.os.SystemClock
import android.view.View
import android.view.Gravity
import android.view.WindowManager
import android.widget.*
import androidx.appcompat.app.AlertDialog
import com.seaway.guideassistant.naviindoor.core.*
import com.seaway.guideassistant.naviindoor.data.*
import java.util.UUID

internal fun NativeIndoorActivity.showLocalization() {
    screen("室内导航 · 定位", immersive = true) { selectedMap?.let { showMap(it.id) } ?: showMaps() }
    navigationDiagnostics()
    val token = gate.generation
    val mapNodes = nodes.toList()
    resumeLive = { showLocalization() }
    window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
    guidanceText = body.label("正在准备定位，请保持镜头朝前。", 23f)
    headingText = body.label("等待罗盘", 14f)
    debugText = body.label("", 13f).apply { visibility = View.GONE }
    speech.speak("开始定位，请缓慢转动手机，使镜头对准周围环境。", force = true)
    body.button("取消定位", NaviStyle.RED, outlined = true) { selectedMap?.let { showMap(it.id) } ?: showMaps() }
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
    val resultCard = body.card()
    resultCard.label("已识别当前位置", 16f).setTextColor(NaviStyle.GREEN)
    resultCard.label(winner.reference.name, 26f)
    resultCard.label("匹配度 " + "%.3f".format(winner.score) + "，请确认是否与实际位置一致。", 15f)
    speech.speak("识别到" + winner.reference.name + "，请确认当前位置。", force = true)
    fun accept(candidate: LocationCandidate) {
        currentNode = nodes.firstOrNull { it.id == candidate.reference.nodeId }
        if (currentNode == null) { message("该定位点不在当前地图，请重新定位。"); return }
        showDestinations()
    }
    body.button("当前位置正确，选择目的地", NaviStyle.GREEN) { accept(winner) }
    body.button("查看参考图片", NaviStyle.BLUE, outlined = true) {
        work("读取录制参考图片…", {
            runCatching { api.referenceImage(winner.reference.id) }.getOrElse { error ->
                val missingObject = error is ApiFailure && (error.status == 404 ||
                    (error.status == 400 && error.message.orEmpty().contains("Object not found", ignoreCase = true)))
                if (!missingObject) throw error
                api.referenceImage(winner.reference.nodeId)
            }
        }) { bytes ->
            val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
            val image = ImageView(this).apply { setImageBitmap(bitmap); adjustViewBounds = true; contentDescription = "定位点参考照片" }
            AlertDialog.Builder(this).setTitle(winner.reference.name).setView(image).setPositiveButton("关闭", null).show()
        }
    }
    candidates.drop(1).filter { it.score > .5 }.forEach { candidate ->
        body.button("实际在“" + candidate.reference.name + "”（" + "%.3f".format(candidate.score) + "）") { accept(candidate) }
    }
    body.button("重新定位", NaviStyle.BLUE, outlined = true) { showLocalization() }
}

internal fun NativeIndoorActivity.showDestinations() {
    val origin = currentNode ?: return showLocalization()
    screen("选择目的地") { selectedMap?.let { showMap(it.id) } ?: showMaps() }
    body.card().label("当前位置：" + origin.name, 20f)
    body.label("请选择目的地", 22f)
    val routes = nodes.filter { it.id != origin.id }.mapNotNull { destination -> planRoute(paths, origin.id, destination.id)?.let { destination to it } }
    if (routes.isEmpty()) body.label("此点位没有可用的出发路线，请先录制相应方向的路线。")
    routes.forEach { (destination, path) ->
        body.button(destination.name + " · " + path.waypoints.size + " 个参考点", NaviStyle.GREEN) {
            navigationBaseSteps = sensors.steps
            val session = NavigationSession(path, destination.name)
            showNavigation(session)
        }
    }
    body.button("重新定位", NaviStyle.BLUE, outlined = true) { showLocalization() }
}

internal fun NativeIndoorActivity.showNavigation(session: NavigationSession) {
    screen("室内导航", immersive = true) { confirm("结束本次导航", "结束后可重新定位或选择目的地。") { session.stop(); showDestinations() } }
    navigationDiagnostics()
    navigation = session
    resumeLive = { showNavigation(session) }
    val token = gate.generation
    window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
    body.label("前往：" + session.destination, 15f).setTextColor(0xFFB9F6CA.toInt())
    guidanceText = body.label(if (session.phase == NavigationPhase.orienting) "请对齐出发方向。" else session.guidance(), 23f)
    guidanceText?.accessibilityLiveRegion = View.ACCESSIBILITY_LIVE_REGION_POLITE
    arrowText = TextView(this).apply {
        text = "↑"; textSize = 82f; gravity = Gravity.CENTER; setTextColor(0xFF42A5F5.toInt())
        background = NaviStyle.shape(this@showNavigation, 0x88000000.toInt(), 100, 0xCCFFFFFF.toInt())
        contentDescription = "目标方向"
    }.also { arrow -> cameraOverlay?.addView(arrow, FrameLayout.LayoutParams(dp(152), dp(152), Gravity.TOP or Gravity.CENTER_HORIZONTAL).apply { topMargin = dp(88) }) }
    headingText = body.label("等待方向信息", 14f)
    progressText = body.label("已确认 " + session.target + " / " + session.path.waypoints.size + " 个参考点")
    routeProgress = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply {
        max = 100; progress = session.target * 100 / session.path.waypoints.size.coerceAtLeast(1)
        progressTintList = android.content.res.ColorStateList.valueOf(NaviStyle.GREEN)
        progressBackgroundTintList = android.content.res.ColorStateList.valueOf(0xFF455A64.toInt())
        contentDescription = "路线识别进度"
    }.also { body.addView(it, LinearLayout.LayoutParams(-1, dp(6)).apply { topMargin = dp(4); bottomMargin = dp(10) }) }
    obstacleText = body.label("视觉障碍提醒：仅画面识别，不测距、不自动绕行。", 15f)
    body.addView(CheckBox(this).apply {
        text = "开启室内障碍提醒"; isChecked = obstacleEnabled
        setTextColor(Color.WHITE); textSize = 15f
        setOnCheckedChangeListener { _, checked ->
            obstacleEnabled = checked; obstacleEpoch++; obstacleConfirmation.clear(); obstacleLastAt = 0
            obstacleText?.text = if (checked) "等待新画面；不测距、不自动绕行。" else "障碍提醒已关闭。"
        }
    })
    debugText = body.label("正在准备相机…", 13f).apply { visibility = View.GONE }
    body.button("我已到位，重新识别", NaviStyle.BLUE) {
        session.confirmPosition(); showNavigation(session)
        message("已记录到位确认，仍需新画面匹配通过。")
    }
    body.button("重播当前提示", NaviStyle.BLUE, outlined = true) { speech.speak(guidanceText?.text.toString(), force = true) }
    body.button("停止导航", NaviStyle.RED, outlined = true) {
        confirm("结束本次导航", "结束后可重新定位或选择目的地。") { session.stop(); showDestinations() }
    }
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
    routeProgress?.progress = update.accepted * 100 / total
    speech.speak(update.text)
}
private fun NativeIndoorActivity.showArrival(text: String) {
    val destinationId = navigation?.path?.endId
    screen("已到达目的地", colour = NaviStyle.GREEN) { selectedMap?.let { showMap(it.id) } ?: showMaps() }
    currentNode = nodes.firstOrNull { it.id == destinationId }
    body.label("✓", 84f).apply { gravity = Gravity.CENTER; setTextColor(NaviStyle.GREEN); setTextIsSelectable(false) }
    body.label(text, 27f).gravity = Gravity.CENTER
    body.label("路线识别进度 100%").gravity = Gravity.CENTER
    body.button("选择下一个目的地") { showDestinations() }
    body.button("返回地图", NaviStyle.GREEN) { selectedMap?.let { showMap(it.id) } ?: showMaps() }
    body.button("复制诊断记录", NaviStyle.TEAL, outlined = true) { copyDiagnostics() }
    speech.speak(text, force = true)
}

private fun NativeIndoorActivity.navigationDiagnostics() {
    toolbarAction("定位与路线诊断", R.drawable.ic_navi_diagnostics) {
        AlertDialog.Builder(this).setTitle("导航诊断")
            .setItems(arrayOf(if (debugText?.visibility == View.VISIBLE) "隐藏实时诊断" else "显示实时诊断", "复制诊断记录")) { _, which ->
                if (which == 1) copyDiagnostics()
                else debugText?.let { it.visibility = if (it.visibility == View.VISIBLE) View.GONE else View.VISIBLE }
            }.setNegativeButton("关闭", null).show()
    }
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
                obstacleText?.setTextColor(if (confirmed.isNotEmpty()) 0xFFFFB74D.toInt() else 0xFFCFD8DC.toInt())
                diagnostic("obstacle", mapOf("frame_id" to id, "message" to text, "detections" to observation.detections, "inference_ms" to observation.inferenceMs))
                if (confirmed.isNotEmpty()) speech.speak(text, obstacle = true)
            }, {
                obstacleConfirmation.clear(); obstacleLastAt = 0
                obstacleText?.text = "障碍识别暂不可用，请留意实际环境。"
            })
        }
    }
}
