package com.elabrador.mobilenavigation.archive;

import okhttp3.HttpUrl;
import okhttp3.MediaType;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.RequestBody;
import okhttp3.Response;
import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.attribute.FileTime;
import java.util.Arrays;
import java.util.Random;
import java.util.concurrent.TimeUnit;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import static org.junit.Assert.*;

/** Uses the actual Java uploader and Python platform. Fixtures are synthetic bytes, not D455 recordings. */
public class ArchiveUploaderIntegrationTest {
    private static final String TOKEN = "synthetic-archive-integration-token";
    private static final MediaType JSON = MediaType.get("application/json; charset=utf-8");
    private static final MediaType BINARY = MediaType.get("application/octet-stream");
    private final OkHttpClient http = new OkHttpClient.Builder()
            .connectTimeout(5, TimeUnit.SECONDS).readTimeout(10, TimeUnit.SECONDS).build();

    @Rule public TemporaryFolder temporary = new TemporaryFolder();

    @Test public void realUploaderResumesAfterPlatformRestartThenSealsAndDownloadsIdenticalArchive() throws Exception {
        File clientRoot = temporary.newFolder("client"), platformRoot = temporary.newFolder("platform");
        // More than two protocol blocks: successful upload proves that the client streamed multiple chunks.
        byte[] payload = new byte[ArchiveUploader.CHUNK_BYTES * 2 + 123457];
        new Random(20261109L).nextBytes(payload);

        try (ArchiveStore local = new ArchiveStore(clientRoot, System::nanoTime)) {
            String id = local.begin("synthetic_protocol_test", ArchiveStore.json(
                    "fixture", "synthetic opaque binary; not camera or codec validation",
                    "model_width", 384, "model_height", 288));
            assertTrue(local.blob(id, "synthetic_large_recording", "bin", payload,
                    ArchiveStore.json("fixture", true, "capture_elapsed_ms", 1000)));
            assertTrue(local.event(id, "distance_summary", ArchiveStore.json("front_m", 2.4)));
            local.flush();
            assertTrue("Initial capture did not drain", local.awaitIdle(10000));
            File sessionDir = local.sessionDirectory(id);
            JSONObject recording = ArchiveStore.readJson(new File(sessionDir, "manifest.json"));
            assertEquals("recording", recording.getString("state"));
            JSONObject large = findFile(recording.getJSONArray("files"), "synthetic_large_recording");
            String relative = large.getString("path");

            // Simulate a previous process having durably sent one block before disappearing.
            try (Platform first = startPlatform(platformRoot, "first.log")) {
                json(first.endpoint, "PUT", "/api/v1/sessions/" + id, recording);
                HttpUrl target = fileUrl(first.endpoint, id, relative).newBuilder()
                        .addQueryParameter("offset", "0")
                        .addQueryParameter("sha256", large.getString("sha256")).build();
                try (Response response = http.newCall(new Request.Builder().url(target)
                        .header("Authorization", "Bearer " + TOKEN)
                        .put(RequestBody.create(Arrays.copyOf(payload, ArchiveUploader.CHUNK_BYTES), BINARY)).build()).execute()) {
                    assertEquals(200, response.code());
                    JSONObject acknowledgement = new JSONObject(response.body().string());
                    assertEquals(ArchiveUploader.CHUNK_BYTES, acknowledgement.getLong("received_bytes"));
                    assertFalse(acknowledgement.getBoolean("complete"));
                }
            }

            // A fresh Python process loads its persisted SQLite index and partial-file offset.
            try (Platform restarted = startPlatform(platformRoot, "restarted.log")) {
                JSONObject progress = json(restarted.endpoint, "GET", "/api/v1/sessions/" + id + "/upload-status", null);
                assertEquals(ArchiveUploader.CHUNK_BYTES,
                        findFile(progress.getJSONArray("files"), "synthetic_large_recording").getLong("received_bytes"));

                ArchiveUploader uploader = new ArchiveUploader(local);
                uploader.upload(id, restarted.endpoint, TOKEN);
                JSONObject firstUpload = ArchiveStore.readJson(new File(sessionDir, "upload-state.json"));
                assertEquals("receiving", firstUpload.getString("state"));
                assertEquals(firstUpload.getLong("total_bytes"), firstUpload.getLong("uploaded_bytes"));
                progress = json(restarted.endpoint, "GET", "/api/v1/sessions/" + id + "/upload-status", null);
                assertEquals("recording", progress.getString("archive_state"));
                assertEquals("uploading", progress.getString("state"));
                assertTrue(findFile(progress.getJSONArray("files"), "synthetic_large_recording").getBoolean("complete"));
                assertArrayEquals(payload, download(restarted.endpoint, id, relative));

                // Capture keeps running after the first upload, adding immutable files to this same session.
                byte[] depthFixture = new byte[]{0, 0, 1, 0, (byte) 255, 1, (byte) 255, (byte) 255};
                assertTrue(local.blob(id, "synthetic_depth", "bin", depthFixture,
                        ArchiveStore.json("inference_id", "synthetic-inference-2", "format", "le:uint16")));
                assertTrue(local.event(id, "navigation_ended", ArchiveStore.json("reason", "synthetic_test_complete")));
                local.finish("synthetic_test_complete");
                assertTrue("Final capture did not drain", local.awaitIdle(10000));

                uploader.upload(id, restarted.endpoint, TOKEN);
                JSONObject sealed = ArchiveStore.readJson(new File(sessionDir, "manifest.json"));
                JSONObject saved = json(restarted.endpoint, "GET", "/api/v1/sessions/" + id, null);
                assertEquals("completed", sealed.getString("state"));
                assertEquals("complete", saved.getJSONObject("upload").getString("state"));
                JSONObject remoteManifest = saved.getJSONObject("manifest");
                assertEquals(id, remoteManifest.getString("session_id"));
                assertEquals("completed", remoteManifest.getString("state"));
                assertEquals(sealed.getLong("started_at_ms"), remoteManifest.getLong("started_at_ms"));
                assertEquals(sealed.getJSONObject("metadata").getString("fixture"),
                        remoteManifest.getJSONObject("metadata").getString("fixture"));
                assertEquals(sealed.getJSONArray("files").length(), remoteManifest.getJSONArray("files").length());

                JSONArray files = sealed.getJSONArray("files");
                for (int index = 0; index < files.length(); index++) {
                    JSONObject entry = files.getJSONObject(index);
                    File localFile = new File(sessionDir, entry.getString("path"));
                    File remoteFile = new File(platformRoot, "sessions/" + id + "/files/" + entry.getString("path"));
                    assertEquals("Stored length mismatch: " + entry.getString("path"), entry.getLong("size"), remoteFile.length());
                    assertEquals(entry.getString("sha256"), ArchiveStore.sha256(remoteFile));
                    assertArrayEquals("Downloaded bytes differ: " + entry.getString("path"),
                            Files.readAllBytes(localFile.toPath()), download(restarted.endpoint, id, entry.getString("path")));
                }

                JSONObject events = json(restarted.endpoint, "GET", "/api/v1/sessions/" + id + "/events?offset=0&limit=100", null);
                assertTrue(events.toString().contains("distance_summary"));
                assertTrue(events.toString().contains("navigation_ended"));

                // Uploading again must not append, replace, or modify previously confirmed files.
                File storedLarge = new File(platformRoot, "sessions/" + id + "/files/" + relative);
                FileTime savedAt = Files.getLastModifiedTime(storedLarge.toPath());
                uploader.upload(id, restarted.endpoint, TOKEN);
                assertEquals(savedAt, Files.getLastModifiedTime(storedLarge.toPath()));
                assertEquals(files.length(), json(restarted.endpoint, "GET", "/api/v1/sessions/" + id + "/upload-status", null)
                        .getJSONArray("files").length());
                assertEquals("complete", ArchiveStore.readJson(new File(sessionDir, "upload-state.json")).getString("state"));
            }
        }
    }

    private static JSONObject findFile(JSONArray files, String type) throws Exception {
        for (int index = 0; index < files.length(); index++) {
            JSONObject entry = files.getJSONObject(index);
            if (type.equals(entry.optString("type"))) return entry;
        }
        throw new AssertionError("Missing file of type " + type);
    }

    private JSONObject json(String endpoint, String method, String path, JSONObject value) throws Exception {
        RequestBody body = value == null ? null : RequestBody.create(value.toString(), JSON);
        try (Response response = http.newCall(new Request.Builder().url(endpoint + path)
                .header("Authorization", "Bearer " + TOKEN).method(method, body).build()).execute()) {
            String content = response.body() == null ? "" : response.body().string();
            assertEquals("Unexpected response to " + method + " " + path + ": " + content, 200, response.code());
            return new JSONObject(content);
        }
    }

    private byte[] download(String endpoint, String id, String relative) throws Exception {
        try (Response response = http.newCall(new Request.Builder().url(fileUrl(endpoint, id, relative))
                .header("Authorization", "Bearer " + TOKEN).build()).execute()) {
            assertEquals(200, response.code());
            return response.body().bytes();
        }
    }

    private static HttpUrl fileUrl(String endpoint, String id, String relative) {
        HttpUrl.Builder url = HttpUrl.get(endpoint).newBuilder().addPathSegments("api/v1/sessions")
                .addPathSegment(id).addPathSegment("files");
        for (String component : relative.split("/")) url.addPathSegment(component);
        return url.build();
    }

    private Platform startPlatform(File data, String logName) throws Exception {
        File script = locateServer();
        String pythonRoot = System.getenv("ROBOGUIDE_PYTHON_ROOT");
        File python = new File(pythonRoot == null
                ? "/Users/vvv/.cache/codex-runtimes/codex-primary-runtime/dependencies/python"
                : pythonRoot, "bin/python3");
        String executable = System.getProperty("navigation.archive.python", python.isFile() ? python.getAbsolutePath() : "python3");
        File log = new File(temporary.getRoot(), logName);
        ProcessBuilder builder = new ProcessBuilder(executable, script.getAbsolutePath(),
                "--data-dir", data.getAbsolutePath(), "--host", "127.0.0.1", "--port", "0")
                .redirectErrorStream(true).redirectOutput(log);
        builder.environment().put("NAV_ARCHIVE_TOKEN", TOKEN);
        Process process = builder.start();
        Pattern address = Pattern.compile("Navigation archive platform: (http://127\\.0\\.0\\.1:[0-9]+)/");
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(10);
        while (System.nanoTime() < deadline && process.isAlive()) {
            if (log.isFile()) {
                Matcher match = address.matcher(new String(Files.readAllBytes(log.toPath()), StandardCharsets.UTF_8));
                if (match.find()) return new Platform(process, match.group(1));
            }
            Thread.sleep(25);
        }
        process.destroy();
        throw new IOException("Local platform failed to start: " + (log.isFile()
                ? new String(Files.readAllBytes(log.toPath()), StandardCharsets.UTF_8) : "no process output"));
    }

    private static File locateServer() throws IOException {
        String configured = System.getProperty("navigation.archive.server");
        if (configured != null && new File(configured).isFile()) return new File(configured);
        for (File directory = new File(System.getProperty("user.dir")).getAbsoluteFile(); directory != null; directory = directory.getParentFile()) {
            for (String relative : new String[]{"platform/archive_server.py", "source/platform/archive_server.py"}) {
                File candidate = new File(directory, relative);
                if (candidate.isFile()) return candidate;
            }
        }
        throw new IOException("Cannot locate source/platform/archive_server.py");
    }

    private static final class Platform implements AutoCloseable {
        final Process process;
        final String endpoint;
        Platform(Process process, String endpoint) { this.process = process; this.endpoint = endpoint; }
        @Override public void close() throws Exception {
            process.destroy();
            if (!process.waitFor(5, TimeUnit.SECONDS)) { process.destroyForcibly(); process.waitFor(5, TimeUnit.SECONDS); }
        }
    }
}
