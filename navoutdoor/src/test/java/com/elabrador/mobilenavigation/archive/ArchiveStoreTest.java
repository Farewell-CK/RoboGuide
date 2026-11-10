package com.elabrador.mobilenavigation.archive;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.zip.ZipFile;

import static org.junit.Assert.*;

public class ArchiveStoreTest {
    private static String read(java.nio.file.Path path) throws Exception {return new String(Files.readAllBytes(path),StandardCharsets.UTF_8);}
    private File directory() throws Exception { return Files.createTempDirectory("nav-archive-test").toFile(); }

    @Test public void completedArchivePreservesBytesAndAssociationsAcrossSessions() throws Exception {
        try (ArchiveStore store = new ArchiveStore(directory(), System::nanoTime)) {
            String first = store.begin("navigation", ArchiveStore.json("model_width", 384, "model_height", 288));
            assertEquals(first, store.begin("duplicate", new JSONObject()));
            byte[] bytes = {1, 2, 3, 4};
            assertTrue(store.event(first, "pose", ArchiveStore.json("x", 1.5, "capture_ms", 50)));
            assertTrue(store.blob(first, "semantic_mask", "bin", bytes, ArchiveStore.json("frame", 9)));
            bytes[0] = 99;
            store.finish("arrived");
            String second = store.begin("navigation", new JSONObject());
            assertNotEquals(first, second);
            assertFalse(store.event(first, "late_cloud_response", new JSONObject()));
            assertFalse(store.blob(first, "late_jpeg", "jpg", new byte[]{1}, new JSONObject()));
            assertTrue(store.event(second, "new_route", new JSONObject()));
            store.finish("cancelled");
            assertTrue(store.awaitIdle(10000));
            File firstDir = store.sessionDirectory(first);
            JSONObject manifest = ArchiveStore.readJson(new File(firstDir, "manifest.json"));
            assertEquals("completed", manifest.getString("state"));
            assertTrue(manifest.getBoolean("capture_complete"));
            JSONArray files = manifest.getJSONArray("files");
            int artifactCount = 0;
            for (int i = 0; i < files.length(); i++) {
                JSONObject entry = files.getJSONObject(i);
                File file = new File(firstDir, entry.getString("path"));
                assertEquals(file.length(), entry.getLong("size"));
                assertEquals(ArchiveStore.sha256(file), entry.getString("sha256"));
                if (entry.getString("type").equals("semantic_mask")) {
                    assertArrayEquals(new byte[]{1,2,3,4}, Files.readAllBytes(file.toPath())); artifactCount++;
                }
            }
            assertEquals(1, artifactCount);
            try (ZipFile zip = new ZipFile(store.exportZip(first))) {
                assertNotNull(zip.getEntry("manifest.json"));
                assertNull(zip.getEntry("upload-state.json"));
                assertEquals(files.length() + 1, zip.size());
            }
            String secondEvents = read(new File(store.sessionDirectory(second), "events-00000.jsonl").toPath());
            assertTrue(secondEvents.contains("new_route"));
            assertFalse(secondEvents.contains("late_cloud"));
        }
    }

    @Test public void credentialsAreExcludedButUsageAndPayloadAreRetained() throws Exception {
        try (ArchiveStore store = new ArchiveStore(directory(), System::nanoTime)) {
            String id = store.begin("navigation", ArchiveStore.json("API_KEY", "private-1", "model", "qwen3-vl-plus"));
            store.event(id, "cloud", ArchiveStore.json("Authorization", "private-2", "usage",
                    ArchiveStore.json("completion_tokens", 42, "access_token", "private-3"),
                    "items", new JSONArray().put(ArchiveStore.json("token", "private-4", "sector", "front"))));
            store.finish("done"); assertTrue(store.awaitIdle(10000));
            String manifest = read(new File(store.sessionDirectory(id), "manifest.json").toPath());
            String events = read(new File(store.sessionDirectory(id), "events-00000.jsonl").toPath());
            assertFalse(manifest.contains("private-1"));
            assertFalse(events.contains("private-"));
            assertTrue(events.contains("completion_tokens")); assertTrue(events.contains("front"));
        }
    }

    @Test public void sealedFileIsAdoptedAndRequiredMissingVideoIsVisible() throws Exception {
        try(ArchiveStore store=new ArchiveStore(directory(),System::nanoTime)) {
            String id=store.begin("camera_test",new JSONObject());
            store.expect("navigation_video");
            File staging=store.stagingFile(id,"bin");Files.write(staging.toPath(),new byte[]{4,5,6});
            assertTrue(store.file(id,"cloud.visual.image","bin",staging,ArchiveStore.json("inference_id","sample")));
            store.finish("user_stop");assertTrue(store.awaitIdle(10000));
            JSONObject manifest=ArchiveStore.readJson(new File(store.sessionDirectory(id),"manifest.json"));
            assertEquals("incomplete",manifest.getString("state"));
            assertEquals("navigation_video",manifest.getJSONArray("missing_required_types").getString(0));
            assertEquals(1,manifest.getJSONObject("type_counts").getInt("cloud.visual.image"));
            assertFalse(staging.exists());
        }
    }

    @Test public void oversizedBlobMarksSessionIncompleteAndLifecycleStillCloses() throws Exception {
        try (ArchiveStore store = new ArchiveStore(directory(), System::nanoTime)) {
            String id = store.begin("navigation", new JSONObject());
            assertFalse(store.blob(id, "point_cloud", "bin", new byte[ArchiveStore.MAX_RECORD_BYTES + 1], new JSONObject()));
            assertTrue(store.event(id, "still_recording", new JSONObject()));
            store.finish("done"); assertTrue(store.awaitIdle(10000));
            JSONObject manifest = ArchiveStore.readJson(new File(store.sessionDirectory(id), "manifest.json"));
            assertEquals("incomplete", manifest.getString("state"));
            assertEquals(1, manifest.getLong("dropped_records"));
            assertFalse(manifest.getBoolean("capture_complete"));
        }
    }

    @Test public void interruptedRecordingRecoversDurableArtifactsAndTruncatesTornTail() throws Exception {
        File root = directory(), dir = new File(root, "nav-interrupted");
        assertTrue(new File(dir, "artifacts/cloud").mkdirs());
        ArchiveStore.atomicJson(new File(dir, "manifest.json"), ArchiveStore.json("session_id", dir.getName(), "state", "recording"));
        Files.write(new File(dir, "events-00000.jsonl").toPath(), "{\"type\":\"pose\"}\n{\"type\":\"torn".getBytes(StandardCharsets.UTF_8));
        Files.write(new File(dir, "artifacts/cloud/0001.jpg").toPath(), new byte[]{7, 8});
        Files.write(new File(dir, "artifacts/cloud/0002.jpg.partial").toPath(), new byte[]{9});
        try (ArchiveStore store = new ArchiveStore(root, System::nanoTime)) {
            JSONObject manifest = ArchiveStore.readJson(new File(dir, "manifest.json"));
            assertEquals("interrupted", manifest.getString("state"));
            assertFalse(manifest.getBoolean("capture_complete"));
            assertEquals(5, manifest.getJSONArray("files").length());
            assertEquals("{\"type\":\"pose\"}\n", read(new File(dir, "events-00000.jsonl").toPath()));
            assertFalse(new File(dir, "artifacts/cloud/0002.jpg.partial").exists());
            assertEquals(1, store.sessions().length());
        }
    }

    @Test public void uploaderRejectsTamperedLocalBytesBeforeSending() throws Exception {
        try (ArchiveStore store = new ArchiveStore(directory(), System::nanoTime)) {
            String id = store.begin("navigation", new JSONObject());
            store.blob(id, "cloud", "jpg", new byte[]{1,2}, new JSONObject());
            store.finish("done"); assertTrue(store.awaitIdle(10000));
            JSONObject manifest = ArchiveStore.readJson(new File(store.sessionDirectory(id), "manifest.json"));
            String path = manifest.getJSONArray("files").getJSONObject(0).getString("path");
            Files.write(new File(store.sessionDirectory(id), path).toPath(), new byte[]{3,4});
            try { new ArchiveUploader(store).upload(id, "https://invalid.invalid", ""); fail("Tampering must fail"); }
            catch (java.io.IOException expected) { assertTrue(expected.getMessage().contains("checksum")); }
            assertEquals("failed", ArchiveStore.readJson(new File(store.sessionDirectory(id), "upload-state.json")).getString("state"));
        }
    }

    @Test public void uploaderRejectsRemotePlaintextAndEmbeddedCredentials() {
        assertEquals("https://example.test/base", ArchiveUploader.validateEndpoint("https://example.test/base/", false));
        assertEquals("http://127.0.0.1:8080", ArchiveUploader.validateEndpoint("http://127.0.0.1:8080", true));
        for (String url : new String[]{"http://example.test", "https://user:password@example.test", "https://example.test?token=secret"}) {
            try { ArchiveUploader.validateEndpoint(url, true); fail("Unsafe endpoint must fail"); }
            catch (IllegalArgumentException expected) { }
        }
    }

    @Test public void reportsExplainNotReadyAndPreserveInputAndIncompleteProcessing()throws Exception{
        try(ArchiveStore store=new ArchiveStore(directory(),System::nanoTime)){
            String id=store.begin("navigation",ArchiveStore.json("model_width",384));
            store.blob(id,"runtime_configuration","json","{\"firmware\":\"fixture\"}".getBytes(StandardCharsets.UTF_8),new JSONObject());
            store.event(id,"navigation.start_requested",ArchiveStore.json("origin_input","我的位置","destination_input","测试楼"));
            store.event(id,"vins_pose",ArchiveStore.json("pose",ArchiveStore.json("initialized",false)));
            store.event(id,"processing_closed_before_result",ArchiveStore.json("processing",ArchiveStore.json("type","semantic_inference","id","fixture")));
            store.finish("ended");assertTrue(store.awaitIdle(10000));
            File dir=store.sessionDirectory(id);JSONObject report=ArchiveStore.readJson(new File(dir,ArchiveReport.SUMMARY));
            assertEquals("not_observed_initialized",report.getJSONObject("modules").getJSONObject("vins").getString("status"));
            assertEquals(1,report.getJSONArray("unfinished_at_end_first_100").length());
            assertEquals("测试楼",report.getJSONObject("last_navigation_start_event").getJSONObject("data").getString("destination_input"));
            try(ZipFile zip=new ZipFile(store.exportZip(id))){assertNotNull(zip.getEntry(ArchiveReport.README));assertNotNull(zip.getEntry(ArchiveReport.SUMMARY));assertNotNull(zip.getEntry(ArchiveReport.DICTIONARY));}
        }
    }

    @Test public void annotationBeforeAndAfterFinishIsAppendOnlyAndPreservesObservationHashes()throws Exception{
        try(ArchiveStore store=new ArchiveStore(directory(),System::nanoTime)){
            String id=store.begin("navigation",new JSONObject());assertTrue(store.awaitIdle(10000));
            JSONObject note=ArchiveStore.json("scene_notes","测试场景","relative_seconds",JSONObject.NULL,"time_source","captured_while_recording");
            store.saveAnnotation(id,note);store.finish("done");assertTrue(store.awaitIdle(10000));
            File dir=store.sessionDirectory(id);JSONObject before=ArchiveStore.readJson(new File(dir,"manifest.json"));
            store.saveAnnotation(id,ArchiveStore.json("arrival_confirmation","arrived","manual_takeover","no","relative_seconds",2.5,"time_source","manual"));
            JSONArray annotations=store.annotations(id);assertEquals(2,annotations.length());assertEquals("unconfirmed",annotations.getJSONObject(0).getString("arrival_confirmation"));
            assertEquals("arrived",annotations.getJSONObject(1).getString("arrival_confirmation"));assertEquals(2,annotations.getJSONObject(1).getInt("revision"));
            for(int i=0;i<before.getJSONArray("files").length();i++){
                JSONObject file=before.getJSONArray("files").getJSONObject(i);assertEquals(file.getString("sha256"),ArchiveStore.sha256(new File(dir,file.getString("path"))));
            }
            JSONObject after=ArchiveStore.readJson(new File(dir,"manifest.json"));assertEquals(before.getJSONArray("files").length()+1,after.getJSONArray("files").length());
            assertEquals("local",ArchiveStore.readJson(new File(dir,"upload-state.json")).getString("state"));
            try(ZipFile zip=new ZipFile(store.exportZip(id))){assertEquals(after.getJSONArray("files").length()+1,zip.size());}
        }
    }

    @Test public void deletingIncompleteSessionRemovesOnlyItsLocalFilesAndCache() throws Exception {
        File root=directory();
        try(ArchiveStore store=new ArchiveStore(root,System::nanoTime)){
            String missing=store.begin("deletion_fixture",new JSONObject());
            store.blob(missing,"fixture","bin",new byte[]{1,2,3},new JSONObject());
            store.problem(missing,"fixture_missing",new JSONObject());
            store.finish("fixture_done");
            String keep=store.begin("keep_fixture",new JSONObject());
            store.event(keep,"keep",ArchiveStore.json("value",42));store.finish("done");
            assertTrue(store.awaitIdle(10000));
            File keepManifest=new File(store.sessionDirectory(keep),"manifest.json");
            String keepHash=ArchiveStore.sha256(keepManifest);
            File cached=store.exportZip(missing),partial=new File(cached+".partial");
            Files.write(partial.toPath(),new byte[]{9});
            File downloaded=new File(root,"downloaded-copy.zip");Files.copy(cached.toPath(),downloaded.toPath());
            String downloadedHash=ArchiveStore.sha256(downloaded);
            store.deleteSession(missing);
            assertFalse(new File(root,missing).exists());assertFalse(cached.exists());assertFalse(partial.exists());
            assertEquals(1,store.sessions().length());
            assertEquals(keepHash,ArchiveStore.sha256(keepManifest));
            assertEquals(downloadedHash,ArchiveStore.sha256(downloaded));
        }
    }

    @Test public void deletingRecordingSessionIsRefusedWithoutChangingIt() throws Exception {
        try(ArchiveStore store=new ArchiveStore(directory(),System::nanoTime)){
            String id=store.begin("active_fixture",new JSONObject());assertTrue(store.awaitIdle(10000));
            assertThrows(java.io.IOException.class,()->store.deleteSession(id));
            assertTrue(store.event(id,"still_recording",new JSONObject()));
            assertTrue(store.sessionDirectory(id).exists());
            assertEquals(id,store.activeId());
        }
    }

    @Test public void deletionWaitsForEntireExportCopyAndConcurrentExportsAreRefused() throws Exception {
        try(ArchiveStore store=new ArchiveStore(directory(),System::nanoTime)){
            String id=store.begin("copy_fixture",new JSONObject());store.finish("done");assertTrue(store.awaitIdle(10000));
            java.util.concurrent.CountDownLatch copying=new java.util.concurrent.CountDownLatch(1),release=new java.util.concurrent.CountDownLatch(1);
            java.util.concurrent.ExecutorService worker=java.util.concurrent.Executors.newSingleThreadExecutor();
            try {
                java.util.concurrent.Future<?> future=worker.submit(()->{
                    try{store.exportZipTo(id,new java.io.OutputStream(){
                        @Override public void write(int value)throws java.io.IOException{write(new byte[]{(byte)value},0,1);}
                        @Override public void write(byte[] bytes,int offset,int count)throws java.io.IOException{
                            copying.countDown();
                            try{if(!release.await(5,java.util.concurrent.TimeUnit.SECONDS))throw new java.io.IOException("Fixture copy timed out");}
                            catch(InterruptedException error){throw new java.io.IOException(error);}
                        }
                    });}catch(Exception error){throw new RuntimeException(error);}
                });
                assertTrue(copying.await(5,java.util.concurrent.TimeUnit.SECONDS));
                assertTrue(store.sessionBusy(id));
                assertThrows(java.io.IOException.class,()->store.deleteSession(id));
                assertThrows(java.io.IOException.class,()->store.exportZip(id));
                assertTrue(store.sessionDirectory(id).exists());
                release.countDown();future.get(5,java.util.concurrent.TimeUnit.SECONDS);
                assertFalse(store.sessionBusy(id));store.deleteSession(id);
                assertEquals(0,store.sessions().length());
            }finally{release.countDown();worker.shutdownNow();}
        }
    }

    @Test public void deletionDoesNotFollowSymlinksOrAcceptTraversal() throws Exception {
        File outside=directory();File untouched=new File(outside,"keep.bin");Files.write(untouched.toPath(),new byte[]{7,8});
        try(ArchiveStore store=new ArchiveStore(directory(),System::nanoTime)){
            String id=store.begin("links_fixture",new JSONObject());store.finish("done");assertTrue(store.awaitIdle(10000));
            Files.createSymbolicLink(new File(store.sessionDirectory(id),"outside-link").toPath(),outside.toPath());
            store.deleteSession(id);assertArrayEquals(new byte[]{7,8},Files.readAllBytes(untouched.toPath()));
            ArchiveStore.atomicJson(new File(outside,"manifest.json"),ArchiveStore.json("state","completed"));
            File linked=new File(store.root,"nav-linked");Files.createSymbolicLink(linked.toPath(),outside.toPath());
            assertThrows(java.io.IOException.class,()->store.deleteSession("nav-linked"));
            assertThrows(IllegalArgumentException.class,()->store.deleteSession("../"+outside.getName()));
            assertTrue(linked.exists());assertTrue(untouched.exists());
        }
    }
}
