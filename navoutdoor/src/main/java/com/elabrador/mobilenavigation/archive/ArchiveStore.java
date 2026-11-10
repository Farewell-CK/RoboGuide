package com.elabrador.mobilenavigation.archive;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.SimpleFileVisitor;
import java.nio.file.FileVisitResult;
import java.nio.file.attribute.BasicFileAttributes;
import java.security.MessageDigest;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.Iterator;
import java.util.List;
import java.util.Locale;
import java.util.UUID;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.locks.Lock;
import java.util.concurrent.locks.ReentrantReadWriteLock;
import java.util.concurrent.locks.ReentrantLock;
import java.util.concurrent.atomic.AtomicLong;
import java.util.function.LongSupplier;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;

/** File-backed sessions; producer calls never wait for disk or network. */
final class ArchiveStore implements AutoCloseable {
    static final int MAX_RECORD_BYTES = 16 * 1024 * 1024;
    static final long MAX_QUEUED_BYTES = 24L * 1024 * 1024;
    static final int MAX_QUEUED_RECORDS = 4096;
    private static final int EVENT_SEGMENT_BYTES = 1024 * 1024;
    final File root;
    private final Object lock = new Object();
    private final ArrayDeque<Work> queue = new ArrayDeque<>();
    private final LongSupplier elapsedNanos;
    private final Thread writer;
    private final ConcurrentHashMap<String,ReentrantReadWriteLock> sessionLocks=new ConcurrentHashMap<>();
    private final ConcurrentHashMap<String,ReentrantLock> exportLocks=new ConcurrentHashMap<>();
    private long queuedBytes;
    private int queuedRecords;
    private boolean working, closed;
    private Session active;
    private volatile String lastError = "";

    private static final class Work {
        final Runnable action;
        final int bytes;
        final boolean record;
        Work(Runnable action, int bytes, boolean record) {
            this.action = action; this.bytes = bytes; this.record = record;
        }
    }

    private static final class Session {
        final String id;
        final File dir;
        final JSONObject manifest;
        final JSONArray files = new JSONArray();
        final java.util.Set<String> requiredTypes=new java.util.HashSet<>();
        final AtomicLong dropped = new AtomicLong();
        final AtomicLong errors = new AtomicLong();
        OutputStream events;
        FileOutputStream eventFile;
        File currentEventFile;
        int eventSegment;
        long eventBytes, eventCount, blobCount, sequence, lastSync;
        Session(String id, File dir, JSONObject manifest) {
            this.id = id; this.dir = dir; this.manifest = manifest;
        }
    }

    ArchiveStore(File root, LongSupplier elapsedNanos) throws IOException {
        this.root = root;
        this.elapsedNanos = elapsedNanos;
        if (!root.isDirectory() && !root.mkdirs()) throw new IOException("Cannot create archive directory");
        recover();
        writer = new Thread(this::writeLoop, "navigation-archive-writer");
        writer.setDaemon(true);
        writer.start();
    }

    String begin(String reason, JSONObject metadata) {
        synchronized (lock) {
            if (closed) return null;
            if (active != null) return active.id;
            String id = "nav-" + System.currentTimeMillis() + "-" + UUID.randomUUID();
            JSONObject manifest = json("schema_version", 1, "session_id", id,
                    "state", "recording", "started_at_ms", System.currentTimeMillis(),
                    "started_elapsed_ns",elapsedNanos.getAsLong(),
                    "reason", reason, "metadata", sanitized(metadata == null ? new JSONObject() : metadata),
                    "exclusions", new JSONArray().put("continuous_raw_rgb").put("continuous_raw_depth").put("continuous_aligned_depth"));
            Session session = new Session(id, new File(root, id), manifest);
            active = session;
            addControl(() -> open(session));
            return id;
        }
    }

    String activeId() { synchronized (lock) { return active == null ? null : active.id; } }
    String activeReason(){synchronized(lock){return active==null?null:active.manifest.optString("reason");}}
    long activeStartedAt(){synchronized(lock){return active==null?0:active.manifest.optLong("started_at_ms");}}

    void finish(String reason) {
        synchronized (lock) {
            if (active == null) return;
            Session session = active;
            active = null;
            addControl(() -> seal(session, reason));
        }
    }

    boolean event(String requiredSessionId, String type, JSONObject payload) {
        synchronized (lock) {
            Session session = active;
            if (session == null || (requiredSessionId != null && !session.id.equals(requiredSessionId))) return false;
            try {
                JSONObject event = json("session_id", session.id, "type", type,
                        "wall_time_ms", System.currentTimeMillis(), "elapsed_time_ns", elapsedNanos.getAsLong(),
                        "data", sanitize(payload == null ? new JSONObject() : payload));
                byte[] encoded = (event.toString() + "\n").getBytes(StandardCharsets.UTF_8);
                return addRecord(session, encoded.length, () -> writeEvent(session, encoded));
            } catch (Exception error) { fail(session, error); return false; }
        }
    }

    boolean blob(String requiredSessionId, String type, String extension, byte[] bytes, JSONObject metadata) {
        if (bytes == null) return false;
        synchronized (lock) {
            Session session = active;
            if (session == null || (requiredSessionId != null && !session.id.equals(requiredSessionId))) return false;
            if (bytes.length > MAX_RECORD_BYTES || !hasCapacity(bytes.length)) {
                session.dropped.incrementAndGet(); return false;
            }
            if (!safeLabel(type) || extension == null || !extension.matches("[A-Za-z0-9_-]{1,16}")) {
                fail(session, new IOException("Invalid artifact type or extension")); return false;
            }
            try {
                byte[] owned = bytes.clone();
                JSONObject data = (JSONObject) sanitize(metadata == null ? new JSONObject() : metadata);
                long sequence = ++session.sequence;
                long wall = System.currentTimeMillis(), elapsed = elapsedNanos.getAsLong();
                return addRecord(session, owned.length, () -> writeBlob(session, type, extension, owned,
                        data, sequence, wall, elapsed));
            } catch (Exception error) { fail(session, error); return false; }
        }
    }

    File stagingFile(String id, String extension) throws IOException {
        File dir=new File(root,id+"/staging");
        if(!id.matches("nav-[A-Za-z0-9_-]{1,100}"))throw new IOException("Invalid session id");
        if(!dir.isDirectory()&&!dir.mkdirs())throw new IOException("Cannot create recording staging directory");
        return new File(dir,UUID.randomUUID()+"."+extension+".pending");
    }

    boolean file(String id,String type,String extension,File source,JSONObject metadata) {
        synchronized(lock) {
            Session session=active;
            if(session==null||!session.id.equals(id))return false;
            if(!safeLabel(type)||!extension.matches("[A-Za-z0-9_-]{1,16}"))return false;
            try {
                if(!source.getCanonicalPath().startsWith(new File(session.dir,"staging").getCanonicalPath()+File.separator))
                    throw new IOException("Artifact is outside recording staging directory");
                JSONObject owned=(JSONObject)sanitize(metadata);long sequence=++session.sequence;
                long wall=System.currentTimeMillis(),elapsed=elapsedNanos.getAsLong();
                return addRecord(session,0,()->{
                    try {
                        File dir=new File(session.dir,"artifacts/"+type);if(!dir.isDirectory()&&!dir.mkdirs())throw new IOException("Cannot create artifact directory");
                        File destination=new File(dir,String.format(Locale.US,"%08d.%s",sequence,extension));
                        if(!source.renameTo(destination))throw new IOException("Cannot adopt sealed recording");
                        try(FileOutputStream sync=new FileOutputStream(destination,true)){sync.getFD().sync();}
                        JSONObject entry=fileEntry(session.dir,destination,type);addSummary(entry,owned);session.files.put(entry);session.blobCount++;
                        JSONObject record=json("session_id",session.id,"type","artifact","wall_time_ms",wall,"elapsed_time_ns",elapsed,
                            "data",json("artifact_type",type,"path",entry.optString("path"),"size",destination.length(),"sha256",entry.optString("sha256"),"metadata",owned));
                        writeEvent(session,(record.toString()+"\n").getBytes(StandardCharsets.UTF_8));checkpoint(session);
                    }catch(Exception error){fail(session,error);}
                });
            }catch(Exception error){fail(session,error);return false;}
        }
    }
    void expect(String type) {synchronized(lock){if(active!=null)active.requiredTypes.add(type);}}

    void problem(String id,String type,JSONObject details) {
        synchronized(lock) {
            if(active==null||!active.id.equals(id))return;
            active.errors.incrementAndGet();event(id,type,details);
        }
    }
    void flush() {
        synchronized(lock) {
            Session s=active;if(s==null)return;
            addControl(()->{try {closeEvents(s);openEvents(s);checkpoint(s);}catch(Exception error){fail(s,error);}});
        }
    }

    private static boolean safeLabel(String value) {
        return value != null && value.matches("[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}") && !value.contains("..");
    }

    private boolean hasCapacity(int size) {
        return !closed && size <= MAX_RECORD_BYTES && queuedRecords < MAX_QUEUED_RECORDS
                && queuedBytes + size <= MAX_QUEUED_BYTES;
    }

    private boolean addRecord(Session session, int bytes, Runnable task) {
        if (!hasCapacity(bytes)) { session.dropped.incrementAndGet(); return false; }
        queue.addLast(new Work(task, bytes, true));
        queuedBytes += bytes; queuedRecords++;
        lock.notifyAll();
        return true;
    }

    private void addControl(Runnable task) {
        queue.addLast(new Work(task, 0, false));
        lock.notifyAll();
    }

    private void writeLoop() {
        while (true) {
            Work work;
            synchronized (lock) {
                while (queue.isEmpty() && !closed) {
                    try { lock.wait(); } catch (InterruptedException ignored) { }
                }
                if (queue.isEmpty() && closed) return;
                work = queue.removeFirst(); working = true;
            }
            try { work.action.run(); }
            catch (Exception error) { lastError = error.toString(); }
            finally {
                synchronized (lock) {
                    if (work.record) { queuedBytes -= work.bytes; queuedRecords--; }
                    working = false; lock.notifyAll();
                }
            }
        }
    }

    private void open(Session session) {
        try {
            if (!session.dir.isDirectory() && !session.dir.mkdirs()) throw new IOException("Cannot create session");
            atomicJson(new File(session.dir, "manifest.json"), session.manifest);
            openEvents(session);
        } catch (Exception error) { fail(session, error); }
    }

    private void openEvents(Session session) throws IOException {
        session.currentEventFile = new File(session.dir,
                String.format(Locale.US, "events-%05d.jsonl", session.eventSegment++));
        session.eventFile = new FileOutputStream(session.currentEventFile);
        session.events = new BufferedOutputStream(session.eventFile, 65536);
        session.eventBytes = 0; session.lastSync = System.nanoTime();
    }

    private void writeEvent(Session session, byte[] bytes) {
        try {
            if (session.events == null) throw new IOException("Session writer is unavailable");
            if (session.eventBytes > 0 && session.eventBytes + bytes.length > EVENT_SEGMENT_BYTES) {
                closeEvents(session); openEvents(session);
            }
            session.events.write(bytes); session.eventBytes += bytes.length; session.eventCount++;
            if (System.nanoTime() - session.lastSync >= TimeUnit.SECONDS.toNanos(1)) {
                session.events.flush(); session.eventFile.getFD().sync(); session.lastSync = System.nanoTime();
            }
        } catch (Exception error) { fail(session, error); }
    }

    private void closeEvents(Session session) throws Exception {
        if (session.events == null) return;
        session.events.flush(); session.eventFile.getFD().sync(); session.events.close();
        session.events = null; session.eventFile = null;
        session.files.put(fileEntry(session.dir, session.currentEventFile, "events"));
        checkpoint(session);
    }

    private void writeBlob(Session session, String type, String extension, byte[] bytes,
                           JSONObject metadata, long sequence, long wall, long elapsed) {
        try {
            File dir = new File(session.dir, "artifacts/" + type);
            if (!dir.isDirectory() && !dir.mkdirs()) throw new IOException("Cannot create artifact directory");
            File destination = new File(dir, String.format(Locale.US, "%08d.%s", sequence, extension));
            File partial = new File(destination.getPath() + ".partial");
            try (FileOutputStream output = new FileOutputStream(partial)) {
                output.write(bytes); output.getFD().sync();
            }
            if (!partial.renameTo(destination)) throw new IOException("Cannot finalize artifact");
            JSONObject entry = fileEntry(session.dir, destination, type);
            addSummary(entry,metadata); session.files.put(entry); session.blobCount++;
            JSONObject record = json("session_id", session.id, "type", "artifact", "wall_time_ms", wall,
                    "elapsed_time_ns", elapsed, "data", json("artifact_type", type,
                    "path", entry.optString("path"), "size", bytes.length,
                    "sha256", entry.optString("sha256"), "metadata", metadata));
            writeEvent(session, (record.toString() + "\n").getBytes(StandardCharsets.UTF_8));
            checkpoint(session);
        } catch (Exception error) { fail(session, error); }
    }

    private static void addSummary(JSONObject entry,JSONObject metadata) {
        JSONObject summary=new JSONObject();
        for(String key:new String[]{"inference_id","image_id","generation","sequence","scope","full_native_tree","format","width","height","stride_bytes","depth_unit_m","start_elapsed_ns","last_pts_us","camera_seconds","capture_elapsed_ms","point_count","sha256","base_sha256","delta_format"}) {
            if(metadata.has(key))try{summary.put(key,metadata.get(key));}catch(Exception ignored){}
        }
        try{entry.put("metadata",summary);}catch(Exception ignored){}
    }

    private void checkpoint(Session session) throws IOException {
        try {
            session.manifest.put("files", session.files);
            JSONObject counts=new JSONObject();
            for(int i=0;i<session.files.length();i++){String type=session.files.getJSONObject(i).optString("type");counts.put(type,counts.optInt(type)+1);}
            session.manifest.put("type_counts",counts);
            session.manifest.put("events_count", session.eventCount);
            session.manifest.put("artifacts_count", session.blobCount);
            session.manifest.put("dropped_records", session.dropped.get());
            session.manifest.put("write_errors", session.errors.get());
            atomicJson(new File(session.dir, "manifest.json"), session.manifest);
        } catch (Exception error) { throw new IOException(error); }
    }

    private void seal(Session session, String reason) {
        try {
            byte[] ending = (json("session_id", session.id, "type", "session_end",
                    "wall_time_ms", System.currentTimeMillis(), "elapsed_time_ns", elapsedNanos.getAsLong(),
                    "data", json("reason", reason, "dropped_records", session.dropped.get(),
                    "write_errors", session.errors.get())).toString() + "\n").getBytes(StandardCharsets.UTF_8);
            writeEvent(session, ending);
            closeEvents(session);
            JSONArray missing=new JSONArray();
            for(String type:session.requiredTypes){boolean found=false;for(int i=0;i<session.files.length();i++)if(type.equals(session.files.getJSONObject(i).optString("type")))found=true;if(!found)missing.put(type);}
            session.manifest.put("missing_required_types",missing);
            if(missing.length()>0){session.errors.incrementAndGet();lastError="Required recording data absent: "+missing;}
            session.manifest.put("ended_at_ms", System.currentTimeMillis());
            session.manifest.put("end_reason", reason);
            session.manifest.put("state", session.dropped.get() == 0 && session.errors.get() == 0 ? "completed" : "incomplete");
            session.manifest.put("capture_complete", session.dropped.get() == 0 && session.errors.get() == 0);
            if (session.errors.get() > 0) session.manifest.put("last_error", lastError);
            addReports(session);
        } catch (Exception error) {
            fail(session, error);
            try {
                session.manifest.put("state", "incomplete").put("capture_complete", false)
                        .put("ended_at_ms", System.currentTimeMillis()).put("last_error", lastError);
                checkpoint(session);
            } catch (Exception ignored) { }
        }
    }

    private void addReports(Session session)throws Exception{
        session.manifest.put("write_errors",session.errors.get()).put("dropped_records",session.dropped.get())
            .put("events_count",session.eventCount).put("artifacts_count",session.blobCount);
        ArchiveReport.generate(session.dir,session.manifest);
        for(String[] item:new String[][]{{ArchiveReport.README,"archive_readme"},{ArchiveReport.SUMMARY,"capture_summary"},{ArchiveReport.DICTIONARY,"data_dictionary"}})
            session.files.put(fileEntry(session.dir,new File(session.dir,item[0]),item[1]));
        checkpoint(session);
    }

    private void fail(Session session, Exception error) {
        session.errors.incrementAndGet(); lastError = error.toString();
    }

    /** Recover bytes already durable, discard partial artifacts and truncate a torn JSONL tail. */
    private void recover() throws IOException {
        File[] directories = root.listFiles(File::isDirectory);
        if (directories == null) return;
        for (File dir : directories) {
            if (!dir.getName().startsWith("nav-")) continue;
            File manifestFile = new File(dir, "manifest.json");
            JSONObject manifest;
            try { manifest = readJson(manifestFile); }
            catch (Exception error) {
                manifest = json("schema_version", 1, "session_id", dir.getName(), "started_at_ms", dir.lastModified());
            }
            String state = manifest.optString("state", "recording");
            if (!state.equals("recording")) continue;
            try {
                JSONArray recoveredFiles = new JSONArray();
                java.util.Map<String,JSONObject> known=new java.util.HashMap<>();
                JSONArray old=manifest.optJSONArray("files");if(old!=null)for(int i=0;i<old.length();i++){JSONObject entry=old.optJSONObject(i);if(entry!=null)known.put(entry.optString("path"),entry);}
                for (File file : descendants(dir)) {
                    String relative = relative(dir, file);
                    if (relative.startsWith("staging/") || relative.endsWith(".partial") || relative.endsWith(".tmp") || relative.endsWith(".pending")) { file.delete(); continue; }
                    if (!archivedFile(relative)) continue;
                    if (relative.endsWith(".jsonl")) trimJsonl(file);
                    if(relative.equals(ArchiveReport.README)||relative.equals(ArchiveReport.SUMMARY)||relative.equals(ArchiveReport.DICTIONARY))continue;
                    String[] parts=relative.split("/");String type=relative.endsWith(".jsonl")?"events":parts.length>2&&parts[0].equals("artifacts")?parts[1]:"recovered_artifact";
                    JSONObject entry=fileEntry(dir,file,type),previous=known.get(relative);
                    if(previous!=null&&entry.optLong("size")==previous.optLong("size")&&entry.optString("sha256").equals(previous.optString("sha256")))entry=previous;
                    recoveredFiles.put(entry);
                }
                manifest.put("files", recoveredFiles).put("state", "interrupted").put("capture_complete", false)
                        .put("end_reason", "process_interrupted").put("ended_at_ms", System.currentTimeMillis());
                long maxRevision=manifest.optLong("experiment_annotation_revision");
                for(int i=0;i<recoveredFiles.length();i++){JSONObject entry=recoveredFiles.getJSONObject(i);if("experiment_annotation".equals(entry.optString("type")))maxRevision=Math.max(maxRevision,readJson(checkedFile(dir,entry.getString("path"))).optLong("revision"));}
                if(maxRevision>0)manifest.put("experiment_annotation_revision",maxRevision);
                ArchiveReport.generate(dir,manifest);
                for(String[] item:new String[][]{{ArchiveReport.README,"archive_readme"},{ArchiveReport.SUMMARY,"capture_summary"},{ArchiveReport.DICTIONARY,"data_dictionary"}})
                    recoveredFiles.put(fileEntry(dir,new File(dir,item[0]),item[1]));
                atomicJson(manifestFile, manifest);
            } catch (Exception error) { lastError = "Recovery failed: " + error; }
        }
    }

    private static void trimJsonl(File file) throws IOException {
        try (java.io.RandomAccessFile io = new java.io.RandomAccessFile(file, "rw")) {
            long size = io.length();
            if (size == 0) return;
            io.seek(size - 1);
            if (io.read() == '\n') return;
            long cursor = size - 1;
            while (cursor >= 0) { io.seek(cursor); if (io.read() == '\n') break; cursor--; }
            io.setLength(cursor + 1); io.getFD().sync();
        }
    }

    JSONArray sessions() {
        JSONArray summaries = new JSONArray();
        File[] dirs = root.listFiles(File::isDirectory);
        if (dirs == null) return summaries;
        java.util.Arrays.sort(dirs, Comparator.comparing(File::getName).reversed());
        for (File dir : dirs) {
            if (!dir.getName().startsWith("nav-")) continue;
            try {
                JSONObject result = readJson(new File(dir, "manifest.json"));
                long bytes = 0;
                for (File f : descendants(dir)) if (archivedFile(relative(dir, f))) bytes += f.length();
                result.put("total_bytes", bytes).put("directory", dir.getAbsolutePath());
                JSONObject status;
                try { status = readJson(new File(dir, "upload-state.json")); }
                catch (Exception ignored) { status = new JSONObject(); }
                result.put("upload_state", status.optString("state", "local"));
                result.put("upload_error", status.optString("error", ""));
                summaries.put(result);
            } catch (Exception ignored) { }
        }
        return summaries;
    }

    File sessionDirectory(String id) {
        if (id == null || !id.matches("nav-[A-Za-z0-9_-]{1,100}")) throw new IllegalArgumentException("Invalid session id");
        File directory = new File(root, id);
        if (!directory.isDirectory()) throw new IllegalArgumentException("Unknown session");
        return directory;
    }

    static final class SessionLease implements AutoCloseable {
        private final Lock held;
        SessionLease(Lock held){this.held=held;}
        @Override public void close(){held.unlock();}
    }

    SessionLease readSession(String id) throws IOException {
        Lock read=sessionLocks.computeIfAbsent(id,key->new ReentrantReadWriteLock()).readLock();
        if(!read.tryLock())throw new IOException("这条记录正在删除，请稍后重试");
        try{sessionDirectory(id);return new SessionLease(read);}
        catch(RuntimeException error){read.unlock();throw error;}
    }

    boolean sessionBusy(String id){
        ReentrantReadWriteLock access=sessionLocks.get(id);
        return access!=null&&(access.isWriteLocked()||access.getReadLockCount()>0);
    }

    JSONArray annotations(String id)throws Exception{
        try(SessionLease ignored=readSession(id)){
            File directory=sessionDirectory(id);JSONArray result=new JSONArray();
            JSONObject manifest=readJson(new File(directory,"manifest.json"));
            JSONArray files=manifest.optJSONArray("files");if(files==null)return result;
            for(int i=0;i<files.length();i++){
                JSONObject entry=files.getJSONObject(i);
                if("experiment_annotation".equals(entry.optString("type")))result.put(readJson(checkedFile(directory,entry.getString("path"))));
            }
            java.util.List<JSONObject> ordered=new java.util.ArrayList<>();for(int i=0;i<result.length();i++)ordered.add(result.getJSONObject(i));
            ordered.sort(java.util.Comparator.comparingLong((JSONObject value)->value.optLong("revision")).thenComparingLong(value->value.optLong("recorded_at_ms")));
            return new JSONArray(ordered);
        }
    }

    void saveAnnotation(String id,JSONObject supplied)throws Exception{
        sessionDirectory(id);
        Lock write=sessionLocks.computeIfAbsent(id,key->new ReentrantReadWriteLock()).writeLock();
        if(!write.tryLock())throw new IOException("这条记录正在上传、导出或删除，请稍后再保存标注");
        try{
            JSONObject annotation=validatedAnnotation(id,supplied);
            final Session captured;
            synchronized(lock){captured=active!=null&&id.equals(active.id)?active:null;}
            java.util.concurrent.FutureTask<Void> task=new java.util.concurrent.FutureTask<>(()->{
                File directory=sessionDirectory(id);Session target=captured;
                JSONObject manifest=target==null?readJson(new File(directory,"manifest.json")):target.manifest;
                JSONArray files=target==null?manifest.getJSONArray("files"):target.files;
                long revision=manifest.optLong("experiment_annotation_revision")+1;
                annotation.put("revision",revision).put("recorded_at_ms",System.currentTimeMillis()).put("recorded_elapsed_ns",elapsedNanos.getAsLong());
                if("captured_while_recording".equals(annotation.optString("time_source"))&&annotation.isNull("relative_seconds")){
                    if(target!=null)annotation.put("relative_seconds",Math.max(0,(elapsedNanos.getAsLong()-manifest.optLong("started_elapsed_ns"))/1e9));
                    else annotation.put("time_source","unspecified");
                }
                File dir=new File(directory,"artifacts/experiment_annotation");if(!dir.isDirectory()&&!dir.mkdirs())throw new IOException("Cannot create annotation directory");
                File file=new File(dir,String.format(Locale.US,"revision-%08d-%s.json",revision,UUID.randomUUID()));
                atomicJson(file,annotation);
                JSONObject entry=fileEntry(directory,file,"experiment_annotation");entry.put("metadata",json("revision",revision));files.put(entry);
                manifest.put("experiment_annotation_revision",revision);
                if(target!=null){target.blobCount++;checkpoint(target);}
                else{
                    manifest.put("files",files).put("artifacts_count",manifest.optLong("artifacts_count")+1);
                    JSONObject counts=manifest.optJSONObject("type_counts");if(counts==null)counts=new JSONObject();
                    counts.put("experiment_annotation",counts.optLong("experiment_annotation")+1);manifest.put("type_counts",counts);
                    atomicJson(new File(directory,"manifest.json"),manifest);
                }
                // A saved revision needs upload even when an older version was complete.
                atomicJson(new File(directory,"upload-state.json"),json("state","local","error","","updated_at_ms",System.currentTimeMillis()));
                return null;
            });
            synchronized(lock){
                if(closed)throw new IOException("Archive is closed");
                // If finish won the race after capture, seal is already queued;
                // retry against the sealed manifest instead of appending to an old Session.
                if(captured!=null&&active!=captured)throw new IOException("导航正在结束，请稍后保存标注");
                addControl(task);
            }
            try{task.get();}catch(java.util.concurrent.ExecutionException error){throw new IOException(error.getCause().getMessage(),error.getCause());}
        }finally{write.unlock();}
    }

    private static JSONObject validatedAnnotation(String id,JSONObject input)throws Exception{
        JSONObject out=new JSONObject();
        for(String field:new String[]{"experiment_id","scene_notes","failure_reason","notes"}){
            String value=input.optString(field,"").trim();if(value.length()>4000)throw new IOException("标注文字过长");
            out.put(field,field.equals("experiment_id")&&value.isEmpty()?id:value);
        }
        for(String field:new String[]{"arrival_confirmation","manual_takeover"}){
            String value=input.optString(field,"unconfirmed");
            String allowed=field.equals("arrival_confirmation")?"unconfirmed,arrived,not_arrived":"unconfirmed,yes,no";
            if(!java.util.Arrays.asList(allowed.split(",")).contains(value))throw new IOException("Invalid confirmation value");out.put(field,value);
        }
        Object seconds=input.opt("relative_seconds");
        if(seconds==null||seconds==JSONObject.NULL)out.put("relative_seconds",JSONObject.NULL);
        else{double value=input.getDouble("relative_seconds");if(!Double.isFinite(value)||value<0)throw new IOException("发生时间应为非负秒数");out.put("relative_seconds",value);}
        String source=input.optString("time_source","unspecified");
        if(!java.util.Arrays.asList("manual","captured_while_recording","unspecified").contains(source))throw new IOException("Invalid annotation time source");
        return out.put("time_source",source).put("session_id",id).put("schema_version",1);
    }

    /** Deletes only app-private files. Exported SAF documents and remote copies are separate. */
    void deleteSession(String id) throws Exception {
        File dir=sessionDirectory(id);
        Lock write=sessionLocks.computeIfAbsent(id,key->new ReentrantReadWriteLock()).writeLock();
        if(!write.tryLock())throw new IOException("这条记录正在上传或导出，请等待完成");
        try {
            if(id.equals(activeId())||"recording".equals(readJson(new File(dir,"manifest.json")).optString("state")))
                throw new IOException("这条记录正在录制，请先结束记录");
            if(Files.isSymbolicLink(dir.toPath())||!dir.getCanonicalFile().getParentFile().equals(root.getCanonicalFile()))
                throw new IOException("Invalid session directory");
            File exports=new File(root,"exports");
            if(exports.exists()&&(Files.isSymbolicLink(exports.toPath())||!exports.getCanonicalFile().getParentFile().equals(root.getCanonicalFile())))
                throw new IOException("Invalid export cache directory");
            Files.deleteIfExists(new File(exports,id+".zip").toPath());
            Files.deleteIfExists(new File(exports,id+".zip.partial").toPath());
            // Default walk does not follow symlinks, including links inside artifacts.
            Files.walkFileTree(dir.toPath(),new SimpleFileVisitor<Path>(){
                @Override public FileVisitResult visitFile(Path file,BasicFileAttributes attrs)throws IOException{
                    Files.delete(file);return FileVisitResult.CONTINUE;
                }
                @Override public FileVisitResult postVisitDirectory(Path directory,IOException error)throws IOException{
                    if(error!=null)throw error;
                    Files.delete(directory);return FileVisitResult.CONTINUE;
                }
            });
        }finally{write.unlock();}
    }

    void exportZipTo(String id,OutputStream destination) throws Exception {
        try(SessionLease ignored=readSession(id);SessionLease exporting=exportLease(id)){
            File zip=exportZipLocked(id);
            try(FileInputStream input=new FileInputStream(zip)){
                byte[] buffer=new byte[65536];int count;
                while((count=input.read(buffer))!=-1)destination.write(buffer,0,count);
            }
        }
    }

    File exportZip(String id) throws Exception {
        try(SessionLease ignored=readSession(id);SessionLease exporting=exportLease(id)){return exportZipLocked(id);}
    }

    private SessionLease exportLease(String id)throws IOException{
        Lock export=exportLocks.computeIfAbsent(id,key->new ReentrantLock());
        if(!export.tryLock())throw new IOException("这条记录正在导出，请等待完成");
        return new SessionLease(export);
    }

    private File exportZipLocked(String id) throws Exception {
        File dir = sessionDirectory(id);
        JSONObject manifest = readJson(new File(dir, "manifest.json"));
        if ("recording".equals(manifest.optString("state"))) throw new IOException("Session is still recording");
        File exports = new File(root, "exports");
        if (!exports.isDirectory() && !exports.mkdirs()) throw new IOException("Cannot create export directory");
        File zip = new File(exports, id + ".zip"), partial = new File(zip.getPath() + ".partial");
        try (FileOutputStream output = new FileOutputStream(partial); ZipOutputStream archive = new ZipOutputStream(output)) {
            addZip(archive, new File(dir, "manifest.json"), "manifest.json");
            JSONArray files = manifest.getJSONArray("files");
            for (int i = 0; i < files.length(); i++) {
                String path = files.getJSONObject(i).getString("path");
                addZip(archive, checkedFile(dir, path), path);
            }
            archive.finish(); archive.flush(); output.getFD().sync();
        }
        if (!partial.renameTo(zip)) throw new IOException("Cannot finalize export");
        return zip;
    }

    private static void addZip(ZipOutputStream out, File file, String path) throws IOException {
        out.putNextEntry(new ZipEntry(path));
        try (FileInputStream input = new FileInputStream(file)) {
            byte[] buffer = new byte[65536]; int read;
            while ((read = input.read(buffer)) != -1) out.write(buffer, 0, read);
        }
        out.closeEntry();
    }

    boolean awaitIdle(long timeoutMillis) throws InterruptedException {
        long deadline = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(timeoutMillis);
        synchronized (lock) {
            while (working || !queue.isEmpty()) {
                long remaining = deadline - System.nanoTime();
                if (remaining <= 0) return false;
                TimeUnit.NANOSECONDS.timedWait(lock, remaining);
            }
            return true;
        }
    }

    @Override public void close() throws Exception {
        finish("recorder_closed");
        synchronized (lock) { closed = true; lock.notifyAll(); }
        writer.join(10000);
    }

    static JSONObject json(Object... pairs) {
        if (pairs.length % 2 != 0) throw new IllegalArgumentException("Key/value pairs required");
        JSONObject result = new JSONObject();
        try {
            for (int i = 0; i < pairs.length; i += 2) {
                Object value = pairs[i + 1];
                if (value instanceof Double && !Double.isFinite((Double) value)) value = JSONObject.NULL;
                if (value instanceof Float && !Float.isFinite((Float) value)) value = JSONObject.NULL;
                result.put(String.valueOf(pairs[i]), value == null ? JSONObject.NULL : value);
            }
        } catch (Exception error) { throw new IllegalArgumentException(error); }
        return result;
    }

    static Object sanitize(Object value) throws Exception {
        if (value instanceof JSONObject) {
            JSONObject output = new JSONObject(), input = (JSONObject) value;
            Iterator<String> keys = input.keys();
            while (keys.hasNext()) {
                String key = keys.next();
                String normalized = key.toLowerCase(Locale.US).replace("-", "_");
                if (normalized.matches("authorization|proxy_authorization|api_?key|access_?token|refresh_?token|bearer|token|password|secret|cookie|set_cookie")) continue;
                output.put(key, sanitize(input.get(key)));
            }
            return output;
        }
        if (value instanceof JSONArray) {
            JSONArray input = (JSONArray) value, output = new JSONArray();
            for (int i = 0; i < input.length(); i++) output.put(sanitize(input.get(i)));
            return output;
        }
        return value;
    }

    static Object sanitized(Object value) {
        try { return sanitize(value); }
        catch (Exception error) { throw new IllegalArgumentException("Invalid archive metadata", error); }
    }

    static JSONObject readJson(File file) throws Exception {
        return new JSONObject(new String(Files.readAllBytes(file.toPath()), StandardCharsets.UTF_8));
    }

    static void atomicJson(File file, JSONObject value) throws IOException {
        File temp = new File(file.getPath() + ".tmp");
        try (FileOutputStream out = new FileOutputStream(temp)) {
            out.write(value.toString().getBytes(StandardCharsets.UTF_8)); out.getFD().sync();
        }
        if (!temp.renameTo(file)) throw new IOException("Cannot persist " + file.getName());
    }

    static JSONObject fileEntry(File dir, File file, String type) throws Exception {
        return json("path", relative(dir, file), "size", file.length(), "sha256", sha256(file), "type", type);
    }

    static String sha256(File file) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        try (FileInputStream input = new FileInputStream(file)) {
            byte[] buffer = new byte[65536]; int read;
            while ((read = input.read(buffer)) != -1) digest.update(buffer, 0, read);
        }
        StringBuilder result = new StringBuilder(64);
        for (byte b : digest.digest()) result.append(String.format(Locale.US, "%02x", b & 255));
        return result.toString();
    }

    static File checkedFile(File root, String relative) throws IOException {
        if (relative == null || relative.isEmpty() || relative.startsWith("/") || relative.contains("\\")) throw new IOException("Invalid file path");
        File file = new File(root, relative);
        if (!file.getCanonicalPath().startsWith(root.getCanonicalPath() + File.separator)) throw new IOException("Invalid file path");
        return file;
    }

    static String relative(File root, File file) { return root.toPath().relativize(file.toPath()).toString().replace(File.separatorChar, '/'); }

    private static boolean archivedFile(String path) {
        return path.equals(ArchiveReport.README)||path.equals(ArchiveReport.SUMMARY)||path.equals(ArchiveReport.DICTIONARY)||path.matches("events-[0-9]+\\.jsonl") || (path.startsWith("artifacts/") && !path.endsWith(".partial") && !path.endsWith(".tmp"));
    }

    private static List<File> descendants(File directory) {
        List<File> result = new ArrayList<>();
        File[] files = directory.listFiles();
        if (files == null) return result;
        for (File file : files) {
            if (file.isDirectory()) result.addAll(descendants(file));
            else if (file.isFile()) result.add(file);
        }
        return result;
    }
}
