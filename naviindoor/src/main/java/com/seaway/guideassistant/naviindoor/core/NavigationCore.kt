package com.seaway.guideassistant.naviindoor.core

import kotlin.math.*

enum class Turn { straight, left, right }
data class Waypoint(
    val id: String, val sequence: Int, val embedding: List<Double>, val heading: Double,
    val turn: Turn = Turn.straight, val description: String = "", val timestamp: String = "",
    val headingChange: Double = 0.0, val decision: Boolean = turn != Turn.straight,
)
data class IndoorPath(
    val id: String, val name: String, val startId: String, val endId: String,
    val waypoints: List<Waypoint>, val createdAt: String = "",
) {
    fun validate() {
        require(waypoints.size >= 2) { "路线至少需要两个采样点" }
        waypoints.forEachIndexed { index, w ->
            require(w.sequence == index) { "路线采样序号不连续，请检查录制记录" }
            require(usableEmbedding(w.embedding) && w.heading.isFinite()) { "路线含无效特征或朝向" }
        }
    }
}
fun usableEmbedding(vector: List<Double>): Boolean =
    vector.size == 768 && vector.all(Double::isFinite) && vector.any { abs(it) > 1e-12 }
fun cosine(a: List<Double>, b: List<Double>): Double {
    if (!usableEmbedding(a) || !usableEmbedding(b)) return Double.NaN
    var dot = 0.0; var aa = 0.0; var bb = 0.0
    for (i in a.indices) { dot += a[i] * b[i]; aa += a[i] * a[i]; bb += b[i] * b[i] }
    return (dot / sqrt(aa * bb)).coerceIn(-1.0, 1.0)
}
fun normalizeHeading(degrees: Double): Double = ((degrees % 360) + 360) % 360
fun relativeHeading(current: Double, target: Double): Double = normalizeHeading(target - current + 180) - 180
fun departureAligned(current: Double?, target: Double): Boolean =
    current != null && current.isFinite() && target.isFinite() && abs(relativeHeading(current, target)) <= 5.0
fun turnConfirmed(turn: Turn, incoming: Double, target: Double, current: Double?): Boolean {
    if (turn == Turn.straight || current == null || !current.isFinite() || !incoming.isFinite() || !target.isFinite()) return false
    val rotation = relativeHeading(incoming, current)
    return (if (turn == Turn.left) rotation <= -25 else rotation >= 25) && abs(relativeHeading(current, target)) <= 35
}
/** Navigation-only inference: stored labels win; a compass spike is insufficient. */
fun inferTurns(source: List<Waypoint>): List<Waypoint> {
    val points = source.sortedBy { it.sequence }
    return points.mapIndexed { i, point ->
        if (i == 0 || i == points.lastIndex || point.turn != Turn.straight) return@mapIndexed point
        val before = points[i - 1]; val after = points[i + 1]
        if (before.sequence + 1 != point.sequence || point.sequence + 1 != after.sequence) return@mapIndexed point
        val change = relativeHeading(before.heading, point.heading)
        val continuation = relativeHeading(point.heading, after.heading)
        val sustained = relativeHeading(before.heading, after.heading)
        if (!change.isFinite() || abs(change) !in 45.0..135.0 || abs(continuation) > 25 ||
            abs(sustained) < 55 || sign(change) != sign(sustained) ||
            (i >= 2 && abs(relativeHeading(points[i - 2].heading, before.heading)) > 25)) point
        else point.copy(turn = if (change < 0) Turn.left else Turn.right, decision = true, headingChange = change)
    }
}
fun waypointThreshold(points: List<Waypoint>, index: Int): Double =
    if (index == points.lastIndex || points[index].turn != Turn.straight || points[index + 1].turn != Turn.straight) .8 else .7
fun forwardLimit(points: List<Waypoint>, target: Int): Int {
    if (target == 0 || waypointThreshold(points, target) >= .8) return target
    var limit = target
    for (i in target + 1..min(target + 2, points.lastIndex)) {
        if (waypointThreshold(points, i) >= .8) break
        limit = i
    }
    return limit
}
enum class MatchReason { belowThreshold, waitingMovement, confirming, accepted, invalid, repeatedFrame }
data class MatchDecision(val reason: MatchReason, val confirmations: Int = 0, val required: Int = 2, val candidate: Int? = null) {
    val accepted get() = reason == MatchReason.accepted
    val message get() = when (reason) {
        MatchReason.belowThreshold -> "画面尚未达标，请核对位置与拍摄方向"
        MatchReason.waitingMovement -> "近期移动记录已用完；若已到此处，可点“我已到位，重新识别”"
        MatchReason.confirming -> "关键画面确认 " + confirmations + " / " + required
        MatchReason.accepted -> "已确认第 " + (candidate!! + 1) + " 个参考点"
        MatchReason.invalid -> "比对数据无效，本次不推进"
        MatchReason.repeatedFrame -> "等待新画面"
    }
}
/** Ported from navigation_match_policy.dart; timestamps are monotonic milliseconds. */
class WaypointTracker {
    private var target: Int? = null
    private var observedAt: Long? = null
    private var eligibleAt: Long? = null
    private var confirmations = 0
    private var candidate: Int? = null
    private var lastMovement = 0
    private var movementAt: Long? = null
    private var movementThrough = -1
    private var acceptedThrough = -1
    fun interrupt() { confirmations = 0; candidate = null; eligibleAt = null }
    fun observe(target: Int, scores: Map<Int, Double>, threshold: Double, capturedAt: Long,
                forwardLimit: Int = target, movementCount: Int = 0, turnInPlace: Boolean = false): MatchDecision {
        val required = if (target == 0 || threshold >= .8) 2 else 1
        if (this.target != target) { this.target = target; interrupt() }
        if (observedAt?.let { capturedAt - it < 600 } == true)
            return MatchDecision(MatchReason.repeatedFrame, confirmations, required)
        observedAt = capturedAt
        if (!threshold.isFinite() || !scores.containsKey(target) || scores.values.any { !it.isFinite() }) {
            interrupt(); return MatchDecision(MatchReason.invalid, required = required)
        }
        if (target <= acceptedThrough) { interrupt(); return MatchDecision(MatchReason.repeatedFrame, required = required) }
        if (movementCount > lastMovement) {
            lastMovement = movementCount; movementAt = capturedAt; movementThrough = target + 3
        }
        val recent = movementAt?.let { capturedAt - it in 0..20_000 && target <= movementThrough } == true
        if (target > 0 && !recent && !turnInPlace) { interrupt(); return MatchDecision(MatchReason.waitingMovement, required = required) }
        var limit = if (target == 0 || threshold >= .8) target else forwardLimit.coerceIn(target, target + 2)
        if (target > 0 && !turnInPlace) limit = min(limit, movementThrough)
        val winner = (target..limit).filter { (scores[it] ?: -1.0) >= threshold }.maxByOrNull { scores[it]!! }
        if (winner == null) { interrupt(); return MatchDecision(MatchReason.belowThreshold, required = required) }
        if (eligibleAt?.let { capturedAt - it > 20_000 } != false) interrupt()
        eligibleAt = capturedAt
        candidate = min(candidate ?: winner, winner)
        confirmations++
        val accepted = confirmations >= required
        if (accepted) {
            acceptedThrough = candidate!!
            if (turnInPlace) { movementAt = capturedAt; movementThrough = candidate!! + 3 }
        }
        return MatchDecision(if (accepted) MatchReason.accepted else MatchReason.confirming, confirmations, required, candidate)
    }
}
data class LocationReference(val id: String, val nodeId: String, val name: String, val embedding: List<Double>)
data class LocationCandidate(val reference: LocationReference, val score: Double)
fun rankLocations(frame: List<Double>, references: List<LocationReference>): List<LocationCandidate> =
    references.map { LocationCandidate(it, cosine(frame, it.embedding)) }.filter { it.score.isFinite() }
        .groupBy { it.reference.nodeId }.map { (_, views) -> views.maxBy { it.score } }.sortedByDescending { it.score }.take(3)
/** A response from a previous page/session must never advance the current one. */
class SessionGate {
    @Volatile
    var generation: Int = 0; private set
    fun invalidate(): Int { generation++; return generation }
    fun accepts(token: Int) = token == generation
}
