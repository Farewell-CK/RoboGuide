package com.elabrador.mobilenavigation.archive;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.SystemClock;
import android.util.Log;
import com.elabrador.mobilenavigation.BuildConfig;
import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.OutputStream;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Navigation archive facade. Initialize once from Application. A session is one navigation task.
 * Producers retain their capture timestamps/frame ids in payload; wall/monotonic record times
 * are added automatically. blob() takes an owned snapshot before returning. Only executed-inference depth pairs and compressed RGB are
 * recorded; continuous raw streams are excluded. API credentials are private preferences, never archive content.
 */
public final class NavigationArchive {
    private static final String TAG = "NavigationArchive";
    private static volatile ArchiveStore store;
    private static SharedPreferences preferences;
    private static volatile String endpoint = "", token = "";
    private static volatile boolean autoUpload;
    private static volatile String closingId;
    private static JSONObject applicationConfiguration=new JSONObject();
    private static final List<Runnable> beforeFinish = new ArrayList<>();
    private static final java.util.concurrent.ConcurrentHashMap<String,JSONObject> pendingProcessing=new java.util.concurrent.ConcurrentHashMap<>();
    private static final Set<String> pendingUploads = new HashSet<>();
    private static final Set<String> pendingDeletes = new HashSet<>();
    private static final java.util.concurrent.ScheduledExecutorService uploads = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread thread = new Thread(r, "navigation-archive-upload"); thread.setDaemon(true); return thread;
    });

    private NavigationArchive() { }

    public static synchronized void initialize(Context context) {
        if (store != null) return;
        try {
            Context application = context.getApplicationContext();
            applicationConfiguration=applicationConfiguration(application);
            preferences = application.getSharedPreferences("navigation_archive_upload", Context.MODE_PRIVATE);
            try {endpoint = ArchiveUploader.validateEndpoint(preferences.getString("endpoint", ""), BuildConfig.DEBUG);}
            catch(IllegalArgumentException invalid){endpoint="";Log.w(TAG,"Invalid saved endpoint; local recording remains available");}
            token = preferences.getString("token", "");
            store = new ArchiveStore(new File(application.getFilesDir(), "navigation-archives"), SystemClock::elapsedRealtimeNanos);
            autoUpload=preferences.getBoolean("auto_upload",false);
            uploads.scheduleWithFixedDelay(()->{
                if(!autoUpload||endpoint.isEmpty())return;
                ArchiveStore local=store;if(local==null)return;
                local.flush();
                JSONArray sessions=local.sessions();
                for(int i=0;i<sessions.length();i++) {
                    JSONObject entry=sessions.optJSONObject(i);
                    if(entry!=null&&!"complete".equals(entry.optString("upload_state")))upload(entry.optString("session_id"));
                }
            },15,15,java.util.concurrent.TimeUnit.SECONDS);
        } catch (Exception error) { Log.e(TAG, "Navigation archive unavailable", error); }
    }

    public static synchronized String begin(String reason, JSONObject metadata) {
        ArchiveStore local = store;
        if(local==null)return null;
        boolean fresh=local.activeId()==null;
        String id=local.begin(reason,metadata);
        if(fresh&&id!=null){
            try{
                JSONObject configuration=json("application",applicationConfiguration,"navigation",com.elabrador.mobilenavigation.RuntimeRecordingConfiguration.snapshot(),"recording",metadata);
                local.blob(id,"runtime_configuration","json",configuration.toString(2).getBytes(java.nio.charset.StandardCharsets.UTF_8),json("phase","navigation_start"));
                local.event(id,"experiment.created",json("experiment_id",id,"scene_notes",JSONObject.NULL,"arrival_confirmation","unconfirmed","manual_takeover","unconfirmed"));
            }catch(Exception error){local.problem(id,"runtime_configuration_failed",json("error",error.toString()));}
        }
        return id;
    }

    public static String currentReason(){ArchiveStore local=store;return local==null?null:local.activeReason();}
    public static long activeSessionStartedAtMillis(){ArchiveStore local=store;return local==null?0:local.activeStartedAt();}
    public static JSONArray experimentAnnotations(String id)throws Exception{ArchiveStore local=store;if(local==null)throw new IllegalStateException("Archive unavailable");return local.annotations(id);}
    public static void saveExperimentAnnotation(String id,JSONObject annotation)throws Exception{
        ArchiveStore local=store;if(local==null)throw new IllegalStateException("Archive unavailable");
        synchronized(NavigationArchive.class){if(pendingUploads.contains(id)||pendingDeletes.contains(id))throw new IllegalStateException("记录正在上传或删除，请等待完成");}
        local.saveAnnotation(id,annotation);
    }

    private static JSONObject applicationConfiguration(Context app){
        JSONObject output=json("manufacturer",android.os.Build.MANUFACTURER,"model",android.os.Build.MODEL,"device",android.os.Build.DEVICE,
            "android_release",android.os.Build.VERSION.RELEASE,"android_sdk",android.os.Build.VERSION.SDK_INT,"supported_abis",new JSONArray(java.util.Arrays.asList(android.os.Build.SUPPORTED_ABIS)),
            "package_name",app.getPackageName(),"source_version",BuildConfig.NAV_SOURCE_VERSION);
        try{
            android.content.pm.PackageInfo pkg=app.getPackageManager().getPackageInfo(app.getPackageName(),0);
            output.put("version_name",pkg.versionName).put("version_code",pkg.getLongVersionCode()).put("last_update_unix_ms",pkg.lastUpdateTime);
        }catch(Exception error){try{output.put("package_info_unavailable",error.getClass().getSimpleName());}catch(Exception ignored){}}
        try(java.io.InputStream input=app.getAssets().open("archive/build_identity.json");java.io.ByteArrayOutputStream bytes=new java.io.ByteArrayOutputStream()){
            byte[] buffer=new byte[8192];int count;while((count=input.read(buffer))!=-1)bytes.write(buffer,0,count);
            output.put("build_identity",new JSONObject(bytes.toString("UTF-8")));
        }catch(Exception error){try{output.put("build_identity_unavailable",error.getClass().getSimpleName());}catch(Exception ignored){}}
        return output;
    }

    /** Hooks execute before the session closes, so batched sensors/final snapshots retain its id. */
    public static synchronized void registerBeforeFinish(Runnable callback) {
        if (callback != null && !beforeFinish.contains(callback)) beforeFinish.add(callback);
    }

    public static synchronized void finish(String reason) {
        ArchiveStore local = store;
        if (local == null || local.activeId() == null) return;
        closingId=local.activeId();
        for (Runnable callback : beforeFinish) {
            try { callback.run(); }
            catch (Exception error) { local.event(null, "finish_hook_error", json("error", error.toString())); }
        }
        for(java.util.Map.Entry<String,JSONObject> entry:pendingProcessing.entrySet()) {
            if(closingId.equals(entry.getValue().optString("session_id"))) {
                local.event(closingId,"processing_closed_before_result",json("processing",entry.getValue(),"status","not_finished_before_session_end","reason",reason));
                pendingProcessing.remove(entry.getKey());
            }
        }
        local.finish(reason);
        closingId=null;
    }

    public static void trackProcessing(String session,String id,String type) {
        if(session!=null&&id!=null&&!isClosing(session)&&session.equals(activeSessionId()))
            pendingProcessing.put(session+":"+id,json("session_id",session,"id",id,"type",type));
    }
    public static void completeProcessing(String session,String id) {if(session!=null&&id!=null)pendingProcessing.remove(session+":"+id);}

    public static boolean isClosing(String id){return id!=null&&id.equals(closingId);}

    public static String activeSessionId() { ArchiveStore local = store; return local == null ? null : local.activeId(); }

    public static boolean event(String type, JSONObject payload) {
        ArchiveStore local = store; return local != null && local.event(null, type, payload);
    }

    public static boolean eventFor(String sessionId, String type, JSONObject payload) {
        ArchiveStore local = store;
        return sessionId != null && local != null && local.event(sessionId, type, payload);
    }

    public static boolean blob(String type, String extension, byte[] bytes, JSONObject metadata) {
        ArchiveStore local = store; return local != null && local.blob(null, type, extension, bytes, metadata);
    }

    public static boolean blobFor(String sessionId, String type, String extension, byte[] bytes, JSONObject metadata) {
        ArchiveStore local = store;
        return sessionId != null && local != null && local.blob(sessionId, type, extension, bytes, metadata);
    }

    public static JSONObject json(Object... pairs) { return ArchiveStore.json(pairs); }

    public static JSONArray sessions() {
        ArchiveStore local=store;if(local==null)return new JSONArray();
        JSONArray result=local.sessions();
        synchronized(NavigationArchive.class){
            for(int i=0;i<result.length();i++){
                JSONObject entry=result.optJSONObject(i);if(entry==null)continue;
                String id=entry.optString("session_id");
                try{entry.put("local_operation_busy",local.sessionBusy(id)||pendingUploads.contains(id)||pendingDeletes.contains(id));}
                catch(Exception ignored){}
            }
        }
        return result;
    }

    public static File sessionDirectory(String sessionId) {
        ArchiveStore local = store;
        if (local == null) throw new IllegalStateException("Archive is not initialized");
        return local.sessionDirectory(sessionId);
    }

    /** May read large files; call on an IO executor. Upload status/credentials are excluded. */
    public static File exportZip(String sessionId) throws Exception {
        ArchiveStore local = store;
        if (local == null) throw new IllegalStateException("Archive is not initialized");
        return local.exportZip(sessionId);
    }

    /** Holds the session lease through the complete SAF copy, not just ZIP creation. */
    public static void exportZipTo(String sessionId,OutputStream destination) throws Exception {
        ArchiveStore local=store;
        if(local==null)throw new IllegalStateException("Archive is not initialized");
        local.exportZipTo(sessionId,destination);
    }

    public static void deleteLocalSession(String sessionId) throws Exception {
        ArchiveStore local=store;
        if(local==null)throw new IllegalStateException("Archive is not initialized");
        synchronized(NavigationArchive.class){
            if(sessionId==null)throw new IllegalArgumentException("请选择要删除的记录");
            if(sessionId.equals(local.activeId())||isClosing(sessionId))throw new IllegalStateException("这条记录正在录制，请先结束记录");
            if(pendingUploads.contains(sessionId)||!pendingDeletes.add(sessionId))throw new IllegalStateException("这条记录正在上传或删除，请等待完成");
        }
        try{local.deleteSession(sessionId);}
        finally{synchronized(NavigationArchive.class){pendingDeletes.remove(sessionId);}}
    }

    public static File stagingFileFor(String id,String extension) throws Exception {
        if(store==null)throw new IllegalStateException("Archive unavailable");return store.stagingFile(id,extension);
    }
    public static boolean fileFor(String id,String type,String extension,File file,JSONObject metadata) {
        ArchiveStore local=store;return local!=null&&local.file(id,type,extension,file,metadata);
    }
    public static void expectArtifact(String type){ArchiveStore local=store;if(local!=null)local.expect(type);}
    public static void problemFor(String id,String type,JSONObject details) {
        ArchiveStore local=store;if(local!=null)local.problem(id,type,details);
    }
    public static void configureAutoUpload(boolean value){autoUpload=value;if(preferences!=null)preferences.edit().putBoolean("auto_upload",value).apply();}
    public static boolean autoUploadEnabled(){return autoUpload;}

    public static boolean awaitIdle(long timeoutMillis) throws InterruptedException {
        ArchiveStore local = store; return local == null || local.awaitIdle(timeoutMillis);
    }

    public static synchronized void configureUpload(String url, String bearerToken) {
        String safeEndpoint = ArchiveUploader.validateEndpoint(url, BuildConfig.DEBUG);
        String safeToken = bearerToken == null ? token : bearerToken.trim();
        if (safeToken.contains("\n") || safeToken.contains("\r")) throw new IllegalArgumentException("Invalid authentication token");
        endpoint = safeEndpoint; token = safeToken;
        if (preferences != null) preferences.edit().putString("endpoint", endpoint).putString("token", token).apply();
    }

    public static String uploadEndpoint() { return endpoint; }
    public static boolean hasUploadToken() { return !token.isEmpty(); }

    /** Queues a local session snapshot; retries/offsets are durable and server-confirmed. */
    public static synchronized boolean upload(String sessionId) {
        ArchiveStore local = store;
        if (local == null || endpoint.isEmpty() || sessionId == null || pendingUploads.contains(sessionId)||pendingDeletes.contains(sessionId)) return false;
        String target = endpoint, credential = token;
        pendingUploads.add(sessionId);
        uploads.execute(() -> {
            try {
                local.flush();
                local.awaitIdle(10000);
                new ArchiveUploader(local).upload(sessionId, target, credential);
            } catch (Exception error) { Log.e(TAG, "Navigation archive upload failed", error); }
            finally { synchronized (NavigationArchive.class) { pendingUploads.remove(sessionId); } }
        });
        return true;
    }

    private static final class TimeUnitCompat {
        static void pause() throws InterruptedException { Thread.sleep(50); }
    }
}
