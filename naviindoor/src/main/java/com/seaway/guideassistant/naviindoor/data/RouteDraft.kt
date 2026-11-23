package com.seaway.guideassistant.naviindoor.data

import com.seaway.guideassistant.naviindoor.core.*
import java.io.File
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.time.Instant
import java.util.UUID

data class RawSample(val id: String, val filename: String, val heading: Double, val turn: Turn,
                     val timestamp: String, val embedding: List<Double>? = null)
data class DraftManifest(val id: String, val name: String, val startId: String, val endId: String,
                         val ownerId: String, val database: String, val createdAt: String,
                         val samples: MutableList<RawSample> = mutableListOf())
/** Raw camera frames survive loss of connectivity and interrupted feature uploads. */
class RouteDraft(val directory: File, val manifest: DraftManifest) {
    @Synchronized fun save() {
        directory.mkdirs()
        val temp = File(directory, "manifest.tmp")
        temp.writeText(NaviJson.gson.toJson(manifest), Charsets.UTF_8)
        Files.move(temp.toPath(), File(directory, "manifest.json").toPath(), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE)
    }
    @Synchronized fun append(bytes: ByteArray, heading: Double, explicitTurn: Turn? = null) {
        require(heading.isFinite() && bytes.isNotEmpty())
        val change = manifest.samples.lastOrNull()?.let { relativeHeading(it.heading, heading) } ?: 0.0
        val turn = explicitTurn ?: if (kotlin.math.abs(change) < 45) Turn.straight else if (change > 0) Turn.right else Turn.left
        val id = UUID.randomUUID().toString()
        val filename = id + ".jpg"
        File(directory, filename).writeBytes(bytes)
        manifest.samples.add(RawSample(id, filename, normalizeHeading(heading), turn, Instant.now().toString()))
        save()
    }
    fun process(api: NaviApi, profile: Profile, progress: (Int, Int) -> Unit): IndoorPath {
        require(manifest.ownerId == profile.id && manifest.database == api.connection.database.trimEnd('/')) { "草稿属于其他账号或项目，请切换回录制时的连接" }
        for (i in manifest.samples.indices) {
            val raw = manifest.samples[i]
            if (raw.embedding == null) {
                val embedding = api.encode(File(directory, raw.filename).readBytes(), recording = true)
                synchronized(this) { manifest.samples[i] = raw.copy(embedding = embedding); save() }
            }
            progress(i + 1, manifest.samples.size)
        }
        val waypoints = manifest.samples.mapIndexed { i, raw ->
            Waypoint(raw.id, i, raw.embedding!!, raw.heading, raw.turn, timestamp = raw.timestamp,
                headingChange = if (i == 0) 0.0 else relativeHeading(manifest.samples[i - 1].heading, raw.heading))
        }
        return IndoorPath(manifest.id, manifest.name, manifest.startId, manifest.endId, waypoints, manifest.createdAt).also { it.validate() }
    }
    companion object {
        fun create(base: File, name: String, startId: String, endId: String, profile: Profile, database: String): RouteDraft {
            val id = UUID.randomUUID().toString()
            return RouteDraft(File(base, id), DraftManifest(id, name, startId, endId, profile.id, database.trimEnd('/'), Instant.now().toString())).also { it.save() }
        }
        fun load(directory: File): RouteDraft = RouteDraft(directory,
            NaviJson.gson.fromJson(File(directory, "manifest.json").readText(Charsets.UTF_8), DraftManifest::class.java))
    }
}
