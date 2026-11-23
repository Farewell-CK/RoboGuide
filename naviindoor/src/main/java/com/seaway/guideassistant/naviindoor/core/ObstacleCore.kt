package com.seaway.guideassistant.naviindoor.core

import kotlin.math.*

data class ObstacleDetection(val classId: Int, val label: String, val position: String, val level: String,
                             val confidence: Double, val occupancy: Double, val box: List<Double>) {
    fun validate() {
        require(box.size == 4 && box.all { it.isFinite() && it in 0.0..1.0 } && box[0] < box[2] && box[1] < box[3])
        require(confidence.isFinite() && confidence in 0.0..1.0 && occupancy.isFinite() && occupancy > 0 && occupancy <= 1)
        require(label.length in 1..24 && level in listOf("notice", "caution") && position in listOf("左前方", "前方", "右前方"))
    }
    val immediate get() = label == "行人" && level == "caution" && confidence >= .85 && occupancy >= .06 && box[0] < .65 && box[2] > .35
    val message get(): String {
        val prefix = position + "识别到" + label
        return when (label) {
            "门" -> prefix + "，请观察开闭状态。"
            "楼梯", "台阶" -> prefix + "，请停下确认。"
            else -> prefix + if (level == "caution") "，请停下观察。" else "，请留意周围。"
        }
    }
    fun matches(other: ObstacleDetection): Boolean {
        if (label != other.label || classId != other.classId) return false
        if (min(occupancy, other.occupancy) / max(occupancy, other.occupancy) < .5 ||
            abs((box[0] + box[2] - other.box[0] - other.box[2]) / 2) > .15) return false
        val intersection = max(0.0, min(box[2], other.box[2]) - max(box[0], other.box[0])) *
            max(0.0, min(box[3], other.box[3]) - max(box[1], other.box[1]))
        val union = (box[2] - box[0]) * (box[3] - box[1]) + (other.box[2] - other.box[0]) * (other.box[3] - other.box[1]) - intersection
        return union > 0 && intersection / union >= .25
    }
}
data class ObstacleObservation(val frameId: String, val message: String, val detections: List<ObstacleDetection>, val inferenceMs: Int)
class ObstacleConfirmation {
    private var previous = emptyList<Pair<ObstacleDetection, Int>>()
    private var capturedAt: Long? = null
    fun clear() { previous = emptyList(); capturedAt = null }
    fun update(current: List<ObstacleDetection>, time: Long): List<ObstacleDetection> {
        if (capturedAt?.let { time - it !in 1..2000 } == true) clear()
        capturedAt = time
        val available = previous.toMutableList()
        previous = current.map { detection ->
            val match = available.indexOfFirst { detection.matches(it.first) }
            detection to if (match < 0) 1 else min(2, available.removeAt(match).second + 1)
        }
        return previous.filter { it.second >= 2 || it.first.immediate }.map { it.first }
    }
}
