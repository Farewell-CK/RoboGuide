package com.seaway.guideassistant.naviindoor.core

enum class NavigationPhase { orienting, navigating, arrived, stopped }
data class NavigationUpdate(val text: String, val target: Int, val accepted: Int,
                            val phase: NavigationPhase, val decision: MatchDecision? = null,
                            val scores: Map<Int, Double> = emptyMap(), val limit: Int = target)

class NavigationSession(path: IndoorPath, val destination: String) {
    val path = path.copy(waypoints = inferTurns(path.waypoints)).also { it.validate() }
    var phase = NavigationPhase.orienting; private set
    var target = 0; private set
    private val tracker = WaypointTracker()
    private var manualMovement = 0
    fun confirmPosition() { manualMovement++; tracker.interrupt() }
    fun interrupt() = tracker.interrupt()
    fun stop() { phase = NavigationPhase.stopped; tracker.interrupt() }
    fun orient(heading: Double?): NavigationUpdate {
        if (phase != NavigationPhase.orienting) return update(guidance())
        if (departureAligned(heading, path.waypoints.first().heading)) {
            phase = NavigationPhase.navigating
            return update("出发方向已对齐，开始导航，请保持手机朝前。")
        }
        val delta = heading?.let { relativeHeading(it, path.waypoints.first().heading) }
        return update(if (delta == null) "等待罗盘，请保持手机朝前并远离磁性物体。"
                      else (if (delta > 0) "请向右调整" else "请向左调整") + "朝向约 " + kotlin.math.abs(delta).toInt() + " 度。")
    }
    fun observe(embedding: List<Double>, capturedAt: Long, steps: Int, heading: Double?): NavigationUpdate {
        if (phase != NavigationPhase.navigating) return update(guidance())
        val points = path.waypoints
        val point = points[target]
        val scores = points.associate { it.sequence to cosine(embedding, it.embedding) }
        val limit = forwardLimit(points, target)
        val turned = target > 0 && turnConfirmed(point.turn, points[target - 1].heading, point.heading, heading)
        val decision = tracker.observe(target, scores, waypointThreshold(points, target), capturedAt, limit,
                                       steps + manualMovement, turned)
        if (!decision.accepted) return update(if (decision.reason == MatchReason.waitingMovement) decision.message else guidance(), decision, scores, limit)
        target = decision.candidate!! + 1
        if (target == points.size) {
            phase = NavigationPhase.arrived
            return update("已到达" + destination + "，本次导航完成。请停下。", decision, scores, limit)
        }
        return update(guidance(), decision, scores, limit)
    }
    fun guidance(): String {
        if (phase == NavigationPhase.arrived) return "已到达" + destination + "，本次导航完成。请停下。"
        if (phase == NavigationPhase.stopped) return "导航已结束。"
        val point = path.waypoints[target]
        if (point.turn != Turn.straight) return (if (point.turn == Turn.left) "前方左转" else "前方右转") + "，请在拐角核对位置并转向，等待画面确认。"
        if (target == path.waypoints.lastIndex) return "接近终点，请将镜头对准终点画面，等待到达确认。"
        return "请沿当前路线直行，保持镜头朝向前方。"
    }
    private fun update(text: String, decision: MatchDecision? = null, scores: Map<Int, Double> = emptyMap(), limit: Int = target) =
        NavigationUpdate(text, target, target, phase, decision, scores, limit)
}

/** Directed route graph: reverse travel requires a recording in that direction. */
fun planRoute(paths: List<IndoorPath>, startId: String, endId: String): IndoorPath? {
    if (startId == endId) return null
    data class Entry(val node: String, val distance: Int, val segments: List<IndoorPath>)
    val queue = java.util.PriorityQueue<Entry>(compareBy { it.distance })
    val visited = mutableSetOf<String>()
    queue.add(Entry(startId, 0, emptyList()))
    while (queue.isNotEmpty()) {
        val item = queue.remove()
        if (!visited.add(item.node)) continue
        if (item.node == endId) {
            val first = item.segments.first()
            val points = item.segments.flatMap { inferTurns(it.waypoints) }.mapIndexed { index, w -> w.copy(sequence = index) }
            return first.copy(name = item.segments.joinToString(" → ") { it.name }, endId = endId, waypoints = points)
        }
        paths.filter { it.startId == item.node && it.waypoints.size >= 2 }.forEach {
            queue.add(Entry(it.endId, item.distance + it.waypoints.size, item.segments + it))
        }
    }
    return null
}
