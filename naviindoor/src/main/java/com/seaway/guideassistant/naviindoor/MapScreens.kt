package com.seaway.guideassistant.naviindoor

import android.graphics.*
import android.media.MediaMetadataRetriever
import android.widget.*
import androidx.appcompat.app.AlertDialog
import com.seaway.guideassistant.naviindoor.core.*
import com.seaway.guideassistant.naviindoor.data.*
import com.seaway.guideassistant.naviindoor.ui.IndoorMapView
import java.io.ByteArrayOutputStream
import java.time.Instant
import java.util.UUID

internal fun NativeIndoorActivity.showMaps(forNavigation: Boolean = false) {
    val user = profile ?: return showHome()
    screen(if (forNavigation) "选择导航地图" else "地图管理") { showHome() }
    toolbarAction("刷新地图", R.drawable.ic_navi_refresh) { showMaps(forNavigation) }
    toolbarAction("复制诊断记录", R.drawable.ic_navi_diagnostics) { copyDiagnostics() }
    body.label(if (forNavigation) "选择地图，开始定位" else "已有地图", 22f)
    body.label(if (forNavigation) "请选择所在楼层或区域的地图。" else "选择地图可查看地点、编辑节点与录制路线。", 15f)
    work("加载地图列表…", { api.maps(user) }) { maps ->
        if (maps.isEmpty()) body.label("暂无可访问的地图，请确认账号所属组织或由管理员上传。")
        maps.forEach { map ->
            val card = body.card()
            card.label(map.name, 20f)
            card.label("室内地图 · 地点与路线", 14f).setTextColor(0xFF757575.toInt())
            if (!forNavigation) card.button("地图详情 · 编辑节点与路线", NaviStyle.TEAL) { showMap(map.id) }
            card.button("开始定位与导航", NaviStyle.GREEN, outlined = !forNavigation) { showMap(map.id, true) }
        }
        if (user.admin && !forNavigation) body.button("＋ 上传新地图", NaviStyle.GREEN) {
            ask("地图名称") { name ->
                pick("image/*") { uri ->
                    work("读取并上传地图…", {
                        val source = contentResolver.openInputStream(uri)?.use { it.readBytes() } ?: error("无法读取图片")
                        val image = prepareImage(source, 4096)
                        val id = UUID.randomUUID().toString()
                        api.write("maps", obj("id" to id, "name" to name, "image_url" to "data:image/jpeg;base64," + java.util.Base64.getEncoder().encodeToString(image),
                            "is_public" to true, "user_id" to user.id, "created_at" to Instant.now().toString()))
                        id
                    }) { id -> showMap(id) }
                }
            }
        }
    }
}

internal fun NativeIndoorActivity.showMap(id: String, forNavigation: Boolean = false) {
    val user = profile ?: return showHome()
    screen("地图详情", colour = NaviStyle.TEAL) { showMaps(forNavigation) }
    toolbarAction("重新加载地图", R.drawable.ic_navi_refresh) { showMap(id, forNavigation) }
    toolbarAction("复制诊断记录", R.drawable.ic_navi_diagnostics) { copyDiagnostics() }
    var routes = emptyList<IndoorPath>()
    work("加载地图与地点…", {
        val details = api.mapDetails(id)
        val bytes = api.image(details.first.imageUrl)
        details to bytes
    }) { (details, imageBytes) ->
        if (selectedMap?.id != id) currentNode = null
        selectedMap = details.first; nodes = details.second; paths = emptyList()
        mapBitmap?.recycle()
        mapBitmap = BitmapFactory.decodeByteArray(imageBytes, 0, imageBytes.size)
        toolbarTitle.text = details.first.name
        body.label("双指缩放、拖动查看地图；地点与路线显示在地图上。", 14f).apply {
            setTextColor(NaviStyle.TEAL)
        }
        val mapView = IndoorMapView(this).apply { image = mapBitmap; nodes = this@showMap.nodes; currentId = currentNode?.id; contentDescription = "室内地图和点位" }
        body.addView(mapView, LinearLayout.LayoutParams(-1, dp(390)))
        val summary = body.label(nodes.size.toString() + " 个地点 · 路线正在加载", 15f)
        val navigate = body.button("定位并开始导航", NaviStyle.GREEN) { showLocalization() }.apply { isEnabled = false; alpha = .5f }
        currentNode?.let { origin -> body.button("从“" + origin.name + "”选择目的地") { showDestinations() } }
        if (user.admin) {
            body.button("添加地点（在地图上点选）", NaviStyle.BLUE) {
                status.text = "请点击地图上的实际位置，填写地点名称。"
                mapView.pick = { x, y ->
                    mapView.pick = null
                    ask("新地点名称") { name ->
                        val capturedHeading = sensors.heading
                        work("创建地点…", {
                            val nodeId = UUID.randomUUID().toString()
                            api.write("map_nodes", obj("id" to nodeId, "map_id" to id, "name" to name, "x_position" to x, "y_position" to y,
                                "reference_direction" to capturedHeading, "user_id" to user.id, "created_at" to Instant.now().toString()))
                            MapNode(nodeId, id, name, x, y, capturedHeading)
                        }) { node -> nodes = nodes + node; showNodeCapture(node) }
                    }
                }
            }
            body.button("录制新路线", NaviStyle.ORANGE) { chooseRecording() }
            body.button("本地路线草稿", NaviStyle.ORANGE, outlined = true) { showDrafts() }
        }
        body.label("地点", 21f)
        nodes.forEach { node ->
            body.button("● " + node.name, NaviStyle.TEAL, outlined = true) {
                if (!user.admin) { message("请先定位当前位置，再选择目的地。"); return@button }
                AlertDialog.Builder(this).setTitle(node.name).setItems(arrayOf("采集定位参考", "修改名称", "手动设为起点（测试）", "删除地点")) { _, which ->
                    when (which) {
                        0 -> showNodeCapture(node)
                        1 -> ask("地点名称", node.name) { name -> work("更新地点…", {
                            api.update("map_nodes", obj("name" to name), mapOf("id" to "eq." + node.id))
                            api.update("place_embeddings", obj("place_name" to name), mapOf("node_id" to "eq." + node.id))
                        }) { showMap(id) } }
                        2 -> { currentNode = node; showDestinations() }
                        3 -> confirm("删除地点", "删除“" + node.name + "”及关联定位参考和录制路线？") {
                            work("删除地点及关联数据…", {
                                routes.filter { it.startId == node.id || it.endId == node.id }.forEach {
                                    api.delete("path_waypoints", mapOf("path_id" to "eq." + it.id))
                                    api.delete("navigation_paths", mapOf("id" to "eq." + it.id))
                                }
                                api.delete("place_embeddings", mapOf("node_id" to "eq." + node.id))
                                api.delete("map_nodes", mapOf("id" to "eq." + node.id))
                            }) { if (currentNode?.id == node.id) currentNode = null; showMap(id) }
                        }
                    }
                }.show()
            }
        }
        body.label("已录制路线", 21f)
        val routeList = body.card()
        routeList.label("正在加载路线…", 15f)
        fun loadRoutes() {
            work("加载本地图路线…", { api.paths(details.second, user) }, failed = {
                routeList.removeAllViews(); routeList.label("路线加载失败，请点击下方按钮重试。", 15f)
                summary.text = nodes.size.toString() + " 个地点 · 路线尚未加载"
            }) { loaded ->
                routes = loaded; paths = loaded; mapView.routes = loaded
                summary.text = nodes.size.toString() + " 个地点 · " + loaded.size + " 条可用路线"
                navigate.isEnabled = nodes.isNotEmpty(); navigate.alpha = if (navigate.isEnabled) 1f else .5f
                routeList.removeAllViews()
                if (loaded.isEmpty()) routeList.label("暂无可用路线。可先添加地点并录制路线。", 15f)
                loaded.forEach { route ->
                    routeList.button(route.name + " · " + route.waypoints.size + "点", NaviStyle.TEAL, outlined = true) {
                        val text = route.waypoints.joinToString("\n") { "第" + (it.sequence + 1) + "点：" + it.turn.name + "，朝向 " + "%.0f°".format(it.heading) }
                        val dialog = AlertDialog.Builder(this).setTitle(route.name).setMessage(text).setPositiveButton("关闭", null)
                        if (user.admin) dialog.setNeutralButton("删除路线") { _, _ ->
                            confirm("删除路线", "确认删除“" + route.name + "”？此操作不会删除地点或其他路线。") {
                                work("删除路线…", { api.delete("path_waypoints", mapOf("path_id" to "eq." + route.id)); api.delete("navigation_paths", mapOf("id" to "eq." + route.id)) }) { showMap(id) }
                            }
                        }
                        dialog.show()
                    }
                }
                if (forNavigation && nodes.isNotEmpty()) showLocalization()
                else if (forNavigation) status.text = "此地图尚未添加地点，请先在地图管理中添加地点并采集定位参考。"
            }
        }
        body.button("重新加载路线", NaviStyle.TEAL, outlined = true) { loadRoutes() }
        loadRoutes()
    }
}

internal fun NativeIndoorActivity.showNodeCapture(node: MapNode) {
    val user = profile ?: return showHome()
    require(user.admin)
    screen("采集地点：" + node.name) { showMap(node.mapId) }
    resumeLive = { showNodeCapture(node) }
    body.label("站在地点附近，依次拍摄不同方向的参考画面；也可导入视频提取多角度参考。")
    headingText = body.label("等待罗盘", 14f)
    preview { status.text = "相机已准备好。" }
    fun upload(bytes: ByteArray): String {
        val embedding = api.encode(bytes)
        val id = UUID.randomUUID().toString()
        api.write("place_embeddings", obj("id" to id, "node_id" to node.id, "place_name" to node.name,
            "embedding" to NaviJson.gson.toJson(embedding), "user_id" to user.id, "organization_id" to user.organizationId,
            "created_at" to Instant.now().toString()))
        api.uploadReference(id, bytes)
        return id
    }
    body.button("拍摄并保存一张参考") {
        if (busy || frameBusy) return@button
        frameBusy = true
        val token = gate.generation
        camera.take({ bytes ->
            if (!gate.accepts(token)) return@take
            frameBusy = false
            work("提取定位参考并上传…", { upload(bytes) }) { status.text = "参考画面已保存，可转向后继续采集。" }
        }, { frameBusy = false; status.text = errorText(it) })
    }
    body.button("导入参考照片") {
        pick("image/*") { uri -> work("读取参考照片…", {
            val bytes = contentResolver.openInputStream(uri)?.use { it.readBytes() } ?: error("无法读取照片")
            upload(prepareImage(bytes, 1920))
        }) { status.text = "参考照片已保存。" } }
    }
    body.button("导入环视视频") {
        pick("video/*") { uri ->
            val token = gate.generation
            work("读取视频并提取参考（最多24帧）…", {
                val retriever = MediaMetadataRetriever()
                try {
                    retriever.setDataSource(this, uri)
                    val duration = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)?.toLongOrNull() ?: error("无法读取视频长度")
                    val count = minOf(24, maxOf(2, (duration / 1500).toInt()))
                    var uploaded = 0
                    for (i in 0 until count) {
                        check(gate.accepts(token)) { "采集已取消，已保存的参考可以保留" }
                        val bitmap = retriever.getFrameAtTime(duration * 1000 * i / count, MediaMetadataRetriever.OPTION_CLOSEST_SYNC) ?: continue
                        val bytes = ByteArrayOutputStream().use { stream -> bitmap.compress(Bitmap.CompressFormat.JPEG, 90, stream); bitmap.recycle(); stream.toByteArray() }
                        upload(bytes); uploaded++
                        runOnUiThread { if (gate.accepts(token)) status.text = "已保存参考 " + uploaded + " / " + count }
                    }
                    uploaded
                } finally { retriever.release() }
            }) { count -> status.text = "已导入 " + count + " 张参考画面。"; resumeLive = { showNodeCapture(node) } }
        }
    }
    body.button("将当前朝向设为地点参考方向") {
        val heading = sensors.heading ?: error("罗盘未就绪")
        confirm("更新参考方向", "将“" + node.name + "”的参考方向设为当前 " + "%.0f°".format(heading) + "？") {
            work("更新方向…", { api.update("map_nodes", obj("reference_direction" to heading), mapOf("id" to "eq." + node.id)) }) { status.text = "参考方向已更新。" }
        }
    }
}

internal fun prepareImage(bytes: ByteArray, maxEdge: Int): ByteArray {
    require(bytes.size <= 32 * 1024 * 1024) { "图片过大，请选择32MB以内的图片" }
    val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
    require(bounds.outWidth > 0 && bounds.outHeight > 0) { "图片无法解码" }
    var sample = 1
    while (maxOf(bounds.outWidth, bounds.outHeight) / sample > maxEdge * 2) sample *= 2
    val original = BitmapFactory.decodeByteArray(bytes, 0, bytes.size, BitmapFactory.Options().apply { inSampleSize = sample }) ?: error("图片无法解码")
    val exif = androidx.exifinterface.media.ExifInterface(java.io.ByteArrayInputStream(bytes))
    val transform = Matrix().apply {
        if (exif.isFlipped) postScale(-1f, 1f)
        postRotate(exif.rotationDegrees.toFloat())
    }
    val oriented = Bitmap.createBitmap(original, 0, 0, original.width, original.height, transform, true)
    val ratio = minOf(1.0, maxEdge.toDouble() / maxOf(oriented.width, oriented.height))
    val bitmap = if (ratio < 1) Bitmap.createScaledBitmap(oriented, (oriented.width * ratio).toInt().coerceAtLeast(1), (oriented.height * ratio).toInt().coerceAtLeast(1), true) else oriented
    return ByteArrayOutputStream().use { stream ->
        bitmap.compress(Bitmap.CompressFormat.JPEG, 90, stream)
        if (bitmap !== oriented) bitmap.recycle()
        if (oriented !== original) oriented.recycle()
        original.recycle()
        stream.toByteArray()
    }
}
