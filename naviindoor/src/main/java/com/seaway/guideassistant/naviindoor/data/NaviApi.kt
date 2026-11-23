package com.seaway.guideassistant.naviindoor.data

import com.google.gson.*
import com.seaway.guideassistant.naviindoor.core.*
import okhttp3.*
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.Closeable
import java.time.Instant
import java.util.concurrent.TimeUnit

data class Connection(val vision: String = "", val database: String = "", val publicKey: String = "") {
    fun validate() {
        listOf(vision, database).forEach {
            val url = it.toHttpUrlOrNull()
            require(url != null && url.username.isEmpty() && url.password.isEmpty() && url.query == null && url.fragment == null) { "请填写完整的 http:// 或 https:// 地址，不要附带账号、查询或片段" }
        }
        require(publicKey.isNotBlank() && !publicKey.contains('\n')) { "请填写 Supabase 客户端公开密钥" }
        require(!publicKey.startsWith("sb_secret_")) { "手机端只能使用公开客户端密钥" }
        val role = runCatching { JsonParser.parseString(String(java.util.Base64.getUrlDecoder().decode(publicKey.split('.')[1]))).asJsonObject.string("role") }.getOrNull()
        require(role != "service_role") { "手机端不能保存 Supabase 管理服务密钥" }
    }
}
data class AuthSession(val accessToken: String, val refreshToken: String, val expiresAt: Long, val userId: String, val email: String)
interface SessionStore { var session: AuthSession? }
data class Profile(val id: String, val email: String, val role: String, val organizationId: String?) { val admin get() = role == "admin" }
data class IndoorMap(val id: String, val name: String, val imageUrl: String = "")
data class MapNode(val id: String, val mapId: String, val name: String, val x: Double, val y: Double, val heading: Double?)
class ApiFailure(val status: Int, message: String): Exception(message)

object NaviJson {
    val gson = Gson()
    fun vector(value: JsonElement?): List<Double> {
        require(value != null && !value.isJsonNull) { "服务器未返回图片特征" }
        val parsed = if (value.isJsonPrimitive && value.asJsonPrimitive.isString) JsonParser.parseString(value.asString) else value
        return parsed.asJsonArray.map { it.asDouble }.also { require(usableEmbedding(it)) { "服务器未返回有效的768维图片特征" } }
    }
    fun waypoint(row: JsonObject): Waypoint = Waypoint(
        row.get("id").asString, row.get("sequence_number").asInt, vector(row.get("embedding")), row.get("heading").asDouble,
        Turn.valueOf(row.string("turn_type", "straight")), row.string("landmark_description"), row.string("timestamp"),
        row.get("heading_change")?.takeUnless { it.isJsonNull }?.asDouble ?: 0.0,
        row.get("is_decision_point")?.takeUnless { it.isJsonNull }?.asBoolean ?: false,
    )
    fun obstacle(row: JsonObject): ObstacleObservation {
        require(row.get("policy_version").asInt == 2 && row.string("profile") == "indoor")
        require(!row.get("distance_available").asBoolean && !row.get("automatic_detour").asBoolean)
        require(row.string("level") in listOf("unknown", "notice", "caution") && row.string("message").length <= 160)
        val detections = row.getAsJsonArray("detections").map { item ->
            val d = item.asJsonObject
            require(d.get("distance_m") == null || d.get("distance_m").isJsonNull)
            ObstacleDetection(d.get("class_id").asInt, d.string("label"), d.string("position"), d.string("level"),
                d.get("confidence").asDouble, d.get("occupancy").asDouble, d.getAsJsonArray("box").map { it.asDouble }).also { it.validate() }
        }
        require(detections.size <= 4 && (row.string("level") == "unknown") == detections.isEmpty())
        return ObstacleObservation(row.string("frame_id"), row.string("message"), detections, row.get("inference_ms")?.asInt ?: 0)
    }
}
fun JsonObject.string(key: String, default: String = ""): String = get(key)?.takeUnless { it.isJsonNull }?.asString ?: default
fun obj(vararg entries: Pair<String, Any?>): JsonObject = NaviJson.gson.toJsonTree(mapOf(*entries)).asJsonObject

/** Blocking HTTP runs only on the Activity's bounded worker executors. */
class NaviApi(val connection: Connection, private val store: SessionStore,
              private val client: OkHttpClient = OkHttpClient.Builder().connectTimeout(12, TimeUnit.SECONDS)
                  .readTimeout(90, TimeUnit.SECONDS).callTimeout(95, TimeUnit.SECONDS).build()): Closeable {
    private val json = "application/json; charset=utf-8".toMediaType()
    private val obstacleClient = client.newBuilder().callTimeout(2800, TimeUnit.MILLISECONDS).build()
    private val refreshLock = Any()
    @Volatile private var closed = false
    private fun url(base: String, path: String, query: Map<String, String> = emptyMap()): HttpUrl {
        val builder = (base.trimEnd('/') + path).toHttpUrlOrNull()?.newBuilder() ?: error("连接地址无效")
        query.forEach { (key, value) -> builder.addQueryParameter(key, value) }
        return builder.build()
    }
    private fun execute(request: Request, maxBytes: Long = 16 * 1024 * 1024L, http: OkHttpClient = client): ByteArray {
        check(!closed) { "请求已取消" }
        http.newCall(request).execute().use { response ->
            val source = response.body?.source() ?: error("服务器返回空响应")
            source.request(maxBytes + 1)
            require(source.buffer.size <= maxBytes) { "服务器响应过大" }
            val bytes = source.readByteArray()
            if (!response.isSuccessful) {
                val error = runCatching { JsonParser.parseString(bytes.toString(Charsets.UTF_8)).asJsonObject }.getOrNull()
                val reason = error?.string("msg")?.ifBlank { error.string("message") }?.ifBlank { error.string("error_description") }
                throw ApiFailure(response.code, "请求失败（HTTP " + response.code + "）：" + (reason?.take(200) ?: "请检查服务地址、登录状态与权限"))
            }
            return bytes
        }
    }
    private fun parsed(request: Request): JsonElement {
        val bytes = execute(request)
        return if (bytes.isEmpty()) JsonNull.INSTANCE else JsonParser.parseString(bytes.toString(Charsets.UTF_8))
    }
    private fun authRequest(path: String, body: JsonObject, query: Map<String, String> = emptyMap()): JsonObject =
        parsed(Request.Builder().url(url(connection.database, "/auth/v1/" + path, query)).header("apikey", connection.publicKey)
            .post(body.toString().toRequestBody(json)).build()).asJsonObject
    private fun saveSession(row: JsonObject): AuthSession {
        check(!closed) { "连接已切换，请重新登录" }
        val user = row.getAsJsonObject("user")
        return AuthSession(row.string("access_token"), row.string("refresh_token"),
            row.get("expires_at")?.asLong ?: (Instant.now().epochSecond + row.get("expires_in").asLong),
            user.string("id"), user.string("email")).also {
            require(it.accessToken.isNotBlank() && it.refreshToken.isNotBlank() && it.userId.isNotBlank())
            store.session = it
        }
    }
    fun signIn(email: String, password: String): AuthSession = saveSession(authRequest("token", obj("email" to email.trim(), "password" to password), mapOf("grant_type" to "password")))
    fun signUp(email: String, password: String): Boolean {
        val row = authRequest("signup", obj("email" to email.trim(), "password" to password, "data" to mapOf("role" to "user")))
        if (row.string("access_token").isNotBlank()) { saveSession(row); return true }
        return false
    }
    private fun token(): String = synchronized(refreshLock) {
        val session = store.session ?: error("请先登录 NAVI")
        if (session.expiresAt - Instant.now().epochSecond > 60) return@synchronized session.accessToken
        try { saveSession(authRequest("token", obj("refresh_token" to session.refreshToken), mapOf("grant_type" to "refresh_token"))).accessToken }
        catch (e: ApiFailure) { if (e.status in listOf(400, 401, 403)) store.session = null; throw e }
    }
    fun signOut() {
        try { parsed(dbRequest("/auth/v1/logout").post(ByteArray(0).toRequestBody()).build()) }
        finally { store.session = null }
    }
    fun recover(email: String) { authRequest("recover", obj("email" to email.trim()), mapOf("redirect_to" to "com.example.indoornavigation://auth/callback")) }
    fun updatePassword(password: String) {
        parsed(dbRequest("/auth/v1/user").put(obj("password" to password).toString().toRequestBody(json)).build())
    }
    fun acceptCallback(access: String, refresh: String) {
        val user = parsed(Request.Builder().url(url(connection.database, "/auth/v1/user")).header("apikey", connection.publicKey).header("Authorization", "Bearer " + access).build()).asJsonObject
        store.session = AuthSession(access, refresh, 0, user.string("id"), user.string("email"))
        token()
    }
    private fun dbRequest(path: String, query: Map<String, String> = emptyMap()) = Request.Builder().url(url(connection.database, path, query))
        .header("apikey", connection.publicKey).header("Authorization", "Bearer " + token())
    fun rows(table: String, query: Map<String, String> = emptyMap()): List<JsonObject> {
        val all = mutableListOf<JsonObject>()
        while (true) {
            val page = parsed(dbRequest("/rest/v1/" + table, query + mapOf("limit" to "500", "offset" to all.size.toString())).build()).asJsonArray.map { it.asJsonObject }
            all.addAll(page)
            if (page.size < 500) return all
            require(all.size < 20_000) { "记录数量过大，请按地图筛选" }
        }
    }
    fun write(table: String, value: JsonElement, query: Map<String, String> = emptyMap(), upsert: Boolean = false) {
        parsed(dbRequest("/rest/v1/" + table, query).header("Prefer", if (upsert) "resolution=merge-duplicates,return=minimal" else "return=minimal")
            .post(value.toString().toRequestBody(json)).build())
    }
    fun update(table: String, value: JsonObject, query: Map<String, String>) {
        parsed(dbRequest("/rest/v1/" + table, query).patch(value.toString().toRequestBody(json)).build())
    }
    fun delete(table: String, query: Map<String, String>) { parsed(dbRequest("/rest/v1/" + table, query).delete().build()) }
    fun profile(): Profile {
        val session = store.session ?: error("请先登录")
        val row = rows("profiles", mapOf("id" to "eq." + session.userId, "select" to "id,role,organization_id")).firstOrNull()
        return Profile(session.userId, session.email, row?.string("role", "user") ?: "user", row?.string("organization_id")?.ifBlank { null })
    }
    fun maps(profile: Profile): List<IndoorMap> {
        val query = mutableMapOf("select" to "id,name", "order" to "created_at.desc")
        if (!profile.admin) query["or"] = "(is_public.eq.true,user_id.eq." + profile.id + ")"
        return rows("maps", query).map { IndoorMap(it.string("id"), it.string("name")) }
    }
    fun mapDetails(id: String): Pair<IndoorMap, List<MapNode>> {
        val row = rows("maps", mapOf("id" to "eq." + id, "select" to "id,name,image_url,map_nodes(*)")).single()
        val nodes = row.getAsJsonArray("map_nodes").map { it.asJsonObject }.map {
            MapNode(it.string("id"), id, it.string("name"), it.get("x_position").asDouble, it.get("y_position").asDouble,
                it.get("reference_direction")?.takeUnless(JsonElement::isJsonNull)?.asDouble)
        }
        return IndoorMap(id, row.string("name"), row.string("image_url")) to nodes
    }
    fun references(nodes: List<MapNode>): List<LocationReference> {
        if (nodes.isEmpty()) return emptyList()
        return nodes.chunked(30).flatMap { group ->
            rows("place_embeddings", mapOf("select" to "id,node_id,place_name,embedding", "node_id" to "in.(" + group.joinToString(",") { it.id } + ")"))
        }.mapNotNull { row -> runCatching { LocationReference(row.string("id"), row.string("node_id"), row.string("place_name"), NaviJson.vector(row.get("embedding"))) }.getOrNull() }
    }
    fun paths(nodes: List<MapNode>, profile: Profile): List<IndoorPath> {
        if (nodes.isEmpty()) return emptyList()
        val scope = if (profile.organizationId != null) "organization_id" to "eq." + profile.organizationId else "user_id" to "eq." + profile.id
        val nodeIds = nodes.map { it.id }.toSet()
        val headers = rows("navigation_paths", mapOf(scope)).filter { it.string("start_location_id") in nodeIds && it.string("end_location_id") in nodeIds }
        val points = headers.chunked(20).flatMap { group ->
            rows("path_waypoints", mapOf("path_id" to "in.(" + group.joinToString(",") { it.string("id") } + ")", "order" to "sequence_number.asc"))
        }.groupBy { it.string("path_id") }
        return headers.mapNotNull { row -> runCatching {
            IndoorPath(row.string("id"), row.string("name"), row.string("start_location_id"), row.string("end_location_id"),
                (points[row.string("id")] ?: emptyList()).map(NaviJson::waypoint).sortedBy { it.sequence }, row.string("created_at")).also { it.validate() }
        }.getOrNull() }
    }
    fun savePath(path: IndoorPath, profile: Profile) {
        require(profile.admin); path.validate()
        val now = Instant.now().toString()
        write("navigation_paths", obj("id" to path.id, "name" to path.name, "start_location_id" to path.startId, "end_location_id" to path.endId,
            "user_id" to profile.id, "organization_id" to profile.organizationId, "estimated_distance" to 0, "estimated_steps" to 0,
            "created_at" to path.createdAt.ifBlank { now }, "updated_at" to now), upsert = true)
        val values = path.waypoints.map { w -> obj("id" to w.id, "path_id" to path.id, "sequence_number" to w.sequence,
            "embedding" to w.embedding, "heading" to w.heading, "heading_change" to w.headingChange, "turn_type" to w.turn.name,
            "is_decision_point" to w.decision, "landmark_description" to w.description, "distance_from_previous" to null,
            "timestamp" to w.timestamp, "people_detected" to false, "people_count" to 0, "people_confidence_scores" to emptyList<Double>()) }
        values.chunked(30).forEach { write("path_waypoints", NaviJson.gson.toJsonTree(it), upsert = true) }
        val saved = rows("path_waypoints", mapOf("path_id" to "eq." + path.id, "select" to "sequence_number", "order" to "sequence_number.asc"))
        require(saved.map { it.get("sequence_number").asInt } == path.waypoints.indices.toList()) { "上传尚未完整，请保留草稿并重试" }
    }
    fun encode(bytes: ByteArray, recording: Boolean = false): List<Double> {
        val body = MultipartBody.Builder().setType(MultipartBody.FORM).addFormDataPart("image", "image.jpg", bytes.toRequestBody("image/jpeg".toMediaType())).build()
        val result = parsed(Request.Builder().url(url(connection.vision, if (recording) "/encode/preprocessed" else "/encode/navigation")).post(body).build())
        return NaviJson.vector(result.asJsonObject.get("embedding"))
    }
    fun obstacle(bytes: ByteArray, frameId: String): ObstacleObservation {
        val body = MultipartBody.Builder().setType(MultipartBody.FORM).addFormDataPart("frame_id", frameId)
            .addFormDataPart("file", "obstacle.jpg", bytes.toRequestBody("image/jpeg".toMediaType())).build()
        val response = execute(Request.Builder().url(url(connection.vision, "/obstacles/analyze")).post(body).build(), 65_536, obstacleClient)
        return NaviJson.obstacle(JsonParser.parseString(response.toString(Charsets.UTF_8)).asJsonObject).also { require(it.frameId == frameId) { "障碍画面编号不一致" } }
    }
    fun checkConnection() {
        parsed(Request.Builder().url(url(connection.vision, "/health")).build())
        parsed(Request.Builder().url(url(connection.database, "/auth/v1/settings")).header("apikey", connection.publicKey).build())
    }
    fun image(url: String): ByteArray {
        if (url.startsWith("data:image/")) return java.util.Base64.getDecoder().decode(url.substringAfter(','))
        val original = url.toHttpUrlOrNull() ?: error("图片地址无效")
        val storageIndex = original.encodedPath.indexOf("/storage/v1/")
        val actual = if (storageIndex >= 0) connection.database.trimEnd('/') + original.encodedPath.substring(storageIndex) else url
        return execute(Request.Builder().url(actual).build(), 24 * 1024 * 1024L)
    }
    fun referenceImage(id: String) = image(connection.database.trimEnd('/') + "/storage/v1/object/public/reference-images/" + id + "_reference.jpg")
    fun uploadReference(id: String, bytes: ByteArray) {
        parsed(dbRequest("/storage/v1/object/reference-images/" + id + "_reference.jpg").header("x-upsert", "true")
            .post(bytes.toRequestBody("image/jpeg".toMediaType())).build())
    }
    fun cancel() { client.dispatcher.cancelAll(); obstacleClient.dispatcher.cancelAll() }
    override fun close() { closed = true; cancel() }
}
