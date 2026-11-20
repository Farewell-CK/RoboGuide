package com.elabrador.mobilenavigation.archive;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.IOException;
import java.io.RandomAccessFile;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.TimeUnit;

import okhttp3.HttpUrl;
import okhttp3.MediaType;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.RequestBody;
import okhttp3.Response;
import okio.BufferedSink;

/** Sequential, idempotent, bounded-memory uploads; the server is authoritative for offsets. */
final class ArchiveUploader {
    static final int CHUNK_BYTES = 4 * 1024 * 1024;
    private static final MediaType JSON = MediaType.get("application/json; charset=utf-8");
    private static final MediaType BINARY = MediaType.get("application/octet-stream");
    private final OkHttpClient http = new OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS).readTimeout(60, TimeUnit.SECONDS)
            .writeTimeout(60, TimeUnit.SECONDS).callTimeout(120, TimeUnit.SECONDS)
            .followRedirects(false).followSslRedirects(false).build();
    private final ArchiveStore store;

    ArchiveUploader(ArchiveStore store) { this.store = store; }

    static String validateEndpoint(String endpoint, boolean allowLoopbackHttp) {
        if (endpoint == null || endpoint.trim().isEmpty()) return "";
        HttpUrl url = HttpUrl.parse(endpoint.trim());
        if (url == null || !url.username().isEmpty() || !url.password().isEmpty()
                || url.query() != null || url.fragment() != null) throw new IllegalArgumentException("Invalid upload endpoint");
        boolean loopback = url.host().equals("localhost") || url.host().equals("127.0.0.1") || url.host().equals("::1")
                || url.host().equals("10.0.2.2");
        if (!url.isHttps() && !(allowLoopbackHttp && loopback))
            throw new IllegalArgumentException("Use HTTPS; debug HTTP is allowed only for a local server");
        String normalized = url.toString();
        while (normalized.endsWith("/")) normalized = normalized.substring(0, normalized.length() - 1);
        return normalized;
    }

    void upload(String id, String endpoint, String token) throws Exception {
        try(ArchiveStore.SessionLease ignored=store.readSession(id)){uploadLocked(id,endpoint,token);}
    }

    private void uploadLocked(String id,String endpoint,String token) throws Exception {
        File dir = store.sessionDirectory(id);
        try {
            JSONObject manifest = ArchiveStore.readJson(new File(dir, "manifest.json"));
            boolean recording="recording".equals(manifest.optString("state"));
            if (!id.equals(manifest.getString("session_id"))) throw new IOException("Session identity mismatch");
            JSONArray files = manifest.getJSONArray("files");
            long total = 0;
            for (int i = 0; i < files.length(); i++) {
                JSONObject entry = files.getJSONObject(i);
                File file = ArchiveStore.checkedFile(dir, entry.getString("path"));
                if (!file.isFile() || file.length() != entry.getLong("size")
                        || !ArchiveStore.sha256(file).equals(entry.getString("sha256")))
                    throw new IOException("Local archive checksum mismatch: " + entry.getString("path"));
                total += file.length();
            }
            save(dir, "uploading", "", 0, total);
            HttpUrl session = HttpUrl.get(endpoint).newBuilder().addPathSegments("api/v1/sessions").addPathSegment(id).build();
            request(session, "PUT", RequestBody.create(manifest.toString(), JSON), token);
            JSONObject status = request(session.newBuilder().addPathSegment("upload-status").build(), "GET", null, token);
            Map<String, JSONObject> offsets = new HashMap<>();
            JSONArray received = status.optJSONArray("files");
            if (received != null) for (int i = 0; i < received.length(); i++) {
                JSONObject entry = received.getJSONObject(i); offsets.put(entry.getString("path"), entry);
            }
            long uploaded = 0;
            for (int i = 0; i < files.length(); i++) {
                JSONObject entry = files.getJSONObject(i);
                String path = entry.getString("path"), checksum = entry.getString("sha256");
                File file = ArchiveStore.checkedFile(dir, path);
                JSONObject offsetEntry = offsets.get(path);
                long offset = offsetEntry == null ? 0 : offsetEntry.optLong("received_bytes", 0);
                if (offset < 0 || offset > file.length()) throw new IOException("Invalid server upload offset");
                if (offsetEntry != null && (!checksum.equals(offsetEntry.optString("sha256"))
                        || file.length() != offsetEntry.optLong("size", -1))) throw new IOException("Remote archive differs from local archive");
                uploaded += offset;
                // Empty files also need to be finalized by the server.
                boolean empty = file.length() == 0 && (offsetEntry == null || !offsetEntry.optBoolean("complete"));
                while (offset < file.length() || empty) {
                    int length = (int) Math.min(CHUNK_BYTES, file.length() - offset);
                    HttpUrl.Builder target = session.newBuilder().addPathSegment("files");
                    for (String component : path.split("/")) target.addPathSegment(component);
                    target.addQueryParameter("offset", Long.toString(offset)).addQueryParameter("sha256", checksum);
                    JSONObject result = request(target.build(), "PUT", segmentBody(file, offset, length), token);
                    long accepted = result.getLong("received_bytes");
                    if (accepted != offset + length) throw new IOException("Unexpected server acknowledgement");
                    offset = accepted; uploaded += length; empty = false;
                    save(dir, "uploading", "", uploaded, total);
                }
            }
            if(recording){save(dir,"receiving","",total,total);return;}
            JSONObject result = request(session.newBuilder().addPathSegment("complete").build(), "POST", RequestBody.create("{}", JSON), token);
            if (!"complete".equals(result.optString("state"))) throw new IOException("Server has not confirmed archive completion");
            save(dir, "complete", "", total, total);
        } catch (Exception error) {
            save(dir, "failed", error.getMessage() == null ? "Upload failed" : error.getMessage(), -1, -1);
            throw error;
        }
    }

    private JSONObject request(HttpUrl url, String method, RequestBody body, String token) throws Exception {
        IOException last = null;
        for (int attempt = 0; attempt < 4; attempt++) {
            if (Thread.currentThread().isInterrupted()) throw new IOException("Upload interrupted");
            Request.Builder request = new Request.Builder().url(url).method(method, body).header("Accept", "application/json");
            if (token != null && !token.isEmpty()) request.header("Authorization", "Bearer " + token);
            try (Response response = http.newCall(request.build()).execute()) {
                if (!response.isSuccessful()) {
                    int code = response.code();
                    if (code != 429 && code < 500) throw new UploadRejected("Upload rejected (HTTP " + code + ")");
                    throw new IOException("Upload temporarily unavailable (HTTP " + code + ")");
                }
                if (response.body() == null) throw new IOException("Empty upload response");
                byte[] content;
                try (java.io.InputStream input = response.body().byteStream(); java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream()) {
                    byte[] buffer = new byte[8192]; int count;
                    while ((count = input.read(buffer)) != -1) {
                        if (out.size() + count > 32 * 1024 * 1024) throw new UploadRejected("Upload response exceeds limit");
                        out.write(buffer, 0, count);
                    }
                    content = out.toByteArray();
                }
                return new JSONObject(new String(content, java.nio.charset.StandardCharsets.UTF_8));
            } catch (UploadRejected rejected) { throw rejected; }
            catch (IOException error) { last = error; }
            if (attempt < 3) TimeUnit.MILLISECONDS.sleep(500L << attempt);
        }
        throw last == null ? new IOException("Upload failed") : last;
    }

    static RequestBody segmentBody(File file, long offset, int length) {
        return new RequestBody() {
            @Override public MediaType contentType() { return BINARY; }
            @Override public long contentLength() { return length; }
            @Override public void writeTo(BufferedSink sink) throws IOException {
                try (RandomAccessFile input = new RandomAccessFile(file, "r")) {
                    input.seek(offset);
                    byte[] buffer = new byte[65536]; int remaining = length;
                    while (remaining > 0) {
                        int count = input.read(buffer, 0, Math.min(buffer.length, remaining));
                        if (count < 0) throw new IOException("Local file shortened during upload");
                        sink.write(buffer, 0, count); remaining -= count;
                    }
                }
            }
        };
    }

    private static void save(File dir, String state, String error, long uploaded, long total) throws IOException {
        JSONObject status;
        try { status = ArchiveStore.readJson(new File(dir, "upload-state.json")); }
        catch (Exception ignored) { status = new JSONObject(); }
        try {
            status.put("state", state).put("error", error).put("updated_at_ms", System.currentTimeMillis());
            if (uploaded >= 0) status.put("uploaded_bytes", uploaded);
            if (total >= 0) status.put("total_bytes", total);
            ArchiveStore.atomicJson(new File(dir, "upload-state.json"), status);
        } catch (Exception problem) { throw new IOException("Cannot persist upload progress", problem); }
    }

    private static final class UploadRejected extends IOException {
        UploadRejected(String message) { super(message); }
    }
}
