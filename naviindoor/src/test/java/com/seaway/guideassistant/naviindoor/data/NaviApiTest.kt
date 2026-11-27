package com.seaway.guideassistant.naviindoor.data

import com.google.gson.JsonParser
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.*
import org.junit.Assert.*
import java.time.Instant
import java.util.concurrent.*

class NaviApiTest {
    private lateinit var server: MockWebServer
    private lateinit var api: NaviApi
    private lateinit var store: SessionStore
    private fun vector() = List(768) { if(it == 0) 1.0 else 0.0 }
    @Before fun prepare() {
        server = MockWebServer(); server.start()
        store = object: SessionStore { override var session: AuthSession? = AuthSession("access", "refresh", Instant.now().epochSecond + 3600, "user", "test@example.invalid") }
        api = NaviApi(Connection(server.url("/").toString(),server.url("/").toString(),"public-test"),store)
    }
    @After fun close() { api.close(); server.shutdown() }
    private fun enqueue(body: String, status: Int = 200) { server.enqueue(MockResponse().setResponseCode(status).setBody(body)) }
    @Test fun loginUsesPasswordGrantAndSavesSession() {
        enqueue("""{"access_token":"new-access","refresh_token":"new-refresh","expires_in":3600,"user":{"id":"u1","email":"a@example.invalid"}}""")
        api.signIn("a@example.invalid", "test-password")
        val request=server.takeRequest(); assertTrue(request.path!!.contains("grant_type=password")); assertEquals("public-test",request.getHeader("apikey"))
        assertEquals("u1",store.session!!.userId)
    }
    @Test fun signupWithoutSessionDoesNotPretendLoginSucceeded() { enqueue("""{"id":"u1","email":"a@example.invalid"}"""); assertFalse(api.signUp("a@example.invalid","password")) }
    @Test fun signupCannotChooseAdminMetadata() { enqueue("{}"); api.signUp("a@example.invalid","password"); val text=server.takeRequest().body.readUtf8(); assertTrue(text.contains("\"role\":\"user\"")); assertFalse(text.contains("admin")) }
    @Test fun registerErrorsRetainHttpStatus() {
        enqueue("""{"msg":"Email rate limit exceeded"}""",429)
        val e=assertThrows(ApiFailure::class.java) { api.signUp("a@example.invalid","password") }
        assertEquals(429,e.status); assertTrue(e.message!!.contains("rate limit"))
    }
    @Test fun featureMultipartUsesExistingNavigationEndpoint() {
        enqueue(NaviJson.gson.toJson(mapOf("embedding" to vector())))
        assertEquals(768,api.encode(byteArrayOf(1,2,3)).size)
        val request=server.takeRequest(); assertEquals("/encode/navigation",request.path); assertTrue(request.body.readUtf8().contains("name=\"image\""))
    }
    @Test fun recordingUsesExistingRecordingEndpoint() { enqueue(NaviJson.gson.toJson(mapOf("embedding" to vector()))); api.encode(byteArrayOf(1),true); assertEquals("/encode/preprocessed",server.takeRequest().path) }
    @Test fun invalidFeaturesFailInsteadOfReturningZeroVector() { enqueue("""{"embedding":[0,0,0]}"""); assertThrows(IllegalArgumentException::class.java) { api.encode(byteArrayOf(1)) } }
    @Test fun legacyNumericArrayStringsAreAccepted() { val arr=NaviJson.gson.toJsonTree(vector()); assertEquals(vector(),NaviJson.vector(arr)); assertEquals(vector(),NaviJson.vector(NaviJson.gson.toJsonTree(arr.toString()))) }
    @Test fun mapListDoesNotDownloadAllEmbeddedPictures() {
        enqueue("[]"); api.maps(Profile("user","a","user",null)); val request=server.takeRequest()
        assertEquals("id,name",request.requestUrl!!.queryParameter("select")); assertTrue(request.requestUrl!!.queryParameter("or")!!.contains("is_public.eq.true"))
    }
    @Test fun refreshesExpiredSessionBeforeDatabaseRead() {
        store.session=store.session!!.copy(expiresAt=0)
        enqueue("""{"access_token":"renewed","refresh_token":"next","expires_in":3600,"user":{"id":"user","email":"a@example.invalid"}}"""); enqueue("[]")
        api.rows("profiles")
        assertTrue(server.takeRequest().path!!.contains("grant_type=refresh_token")); assertEquals("Bearer renewed",server.takeRequest().getHeader("Authorization"))
    }
    @Test fun invalidRefreshClearsSessionButNetworkFailuresDoNot() {
        store.session=store.session!!.copy(expiresAt=0); enqueue("{}",401)
        assertThrows(ApiFailure::class.java) { api.rows("profiles") }; assertNull(store.session)
    }
    @Test fun profileRoleComesFromServer() { enqueue("""[{"id":"user","role":"admin","organization_id":"org"}]"""); val p=api.profile(); assertTrue(p.admin); assertEquals("org",p.organizationId) }
    @Test fun noProfileDefaultsToOrdinaryUser() { enqueue("[]"); assertFalse(api.profile().admin) }
    @Test fun publicStorageImagesFollowConfiguredProxy() {
        enqueue("image"); api.image("https://old-project.supabase.co/storage/v1/object/public/reference-images/a.jpg")
        val request = server.takeRequest()
        assertEquals(server.hostName,request.requestUrl!!.host)
        assertEquals("/storage/v1/object/authenticated/reference-images/a.jpg", request.path)
        assertEquals("Bearer access", request.getHeader("Authorization"))
        assertEquals("public-test", request.getHeader("apikey"))
    }
    @Test fun privateReferenceImagesUseAuthenticatedDownload() {
        enqueue("reference"); assertArrayEquals("reference".toByteArray(), api.referenceImage("ref-id"))
        val request = server.takeRequest()
        assertEquals("/storage/v1/object/authenticated/reference-images/ref-id_reference.jpg", request.path)
        assertEquals("Bearer access", request.getHeader("Authorization"))
        assertEquals("public-test", request.getHeader("apikey"))
    }
    @Test fun privateMapImagesUseAuthenticatedDownload() {
        enqueue("map"); assertArrayEquals("map".toByteArray(), api.image("https://old-project.supabase.co/storage/v1/object/public/maps/a%20map.jpg"))
        val request = server.takeRequest()
        assertEquals("/storage/v1/object/authenticated/maps/a%20map.jpg", request.path)
        assertEquals("Bearer access", request.getHeader("Authorization"))
    }
    @Test fun genuinelyPublicMapImagesStillWork() {
        enqueue("{}", 403); enqueue("map")
        assertArrayEquals("map".toByteArray(), api.image("https://old-project.supabase.co/storage/v1/object/public/maps/a.jpg"))
        assertEquals("Bearer access", server.takeRequest().getHeader("Authorization"))
        val request = server.takeRequest()
        assertEquals("/storage/v1/object/public/maps/a.jpg", request.path)
        assertNull(request.getHeader("Authorization"))
    }
    @Test fun nonStorageImagesDoNotReceiveDatabaseCredentials() {
        enqueue("image"); api.image(server.url("/outside.jpg").toString())
        val request = server.takeRequest()
        assertNull(request.getHeader("Authorization")); assertNull(request.getHeader("apikey"))
    }
    @Test fun signedStorageImageRetainsQuery() {
        enqueue("image"); api.image("https://old-project.supabase.co/storage/v1/object/sign/maps/a.jpg?token=test-signed")
        val request = server.takeRequest()
        assertEquals("test-signed", request.requestUrl!!.queryParameter("token")); assertNull(request.getHeader("Authorization"))
    }
    @Test fun embeddedMapImageDoesNotNeedNetwork() { assertArrayEquals("map".toByteArray(),api.image("data:image/jpeg;base64,bWFw")); assertEquals(0,server.requestCount) }
    @Test fun obstacleResponseMustCorrespondToCurrentFrame() {
        enqueue("""{"policy_version":2,"profile":"indoor","distance_available":false,"automatic_detour":false,"level":"unknown","message":"请留意环境","frame_id":"old","detections":[],"inference_ms":20}""")
        assertThrows(IllegalArgumentException::class.java) { api.obstacle(byteArrayOf(1),"new") }
        val body=server.takeRequest().body.readUtf8(); assertTrue(body.contains("name=\"file\"")); assertTrue(body.contains("name=\"frame_id\""))
    }
    @Test fun rejectsObstacleServiceWithDistanceClaims() {
        val row=JsonParser.parseString("""{"policy_version":2,"profile":"indoor","distance_available":true,"automatic_detour":false,"level":"unknown","message":"安全","frame_id":"x","detections":[]}""").asJsonObject
        assertThrows(IllegalArgumentException::class.java) { NaviJson.obstacle(row) }
    }
    @Test fun queryParametersAreEncoded() { enqueue("[]"); api.rows("profiles",mapOf("email" to "eq.a+b@example.invalid")); assertEquals("eq.a+b@example.invalid",server.takeRequest().requestUrl!!.queryParameter("email")) }
    @Test fun healthChecksBothServices() { enqueue("{\"status\":\"healthy\"}"); enqueue("{}"); api.checkConnection(); assertEquals("/health",server.takeRequest().path); assertEquals("/auth/v1/settings",server.takeRequest().path) }
    @Test fun connectionRejectsEmbeddedCredentialsAndPrivateKey() {
        assertThrows(IllegalArgumentException::class.java) { Connection("https://u:p@example.com","https://example.com","public").validate() }
        assertThrows(IllegalArgumentException::class.java) { Connection("https://example.com","https://example.com","sb_secret_test").validate() }
    }
}
