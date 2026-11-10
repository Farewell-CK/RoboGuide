package com.elabrador.mobilenavigation;

import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.location.Location;
import android.os.BatteryManager;
import android.os.Debug;
import android.os.SystemClock;
import com.elabrador.mobilenavigation.archive.NavigationArchive;
import org.json.JSONArray;
import org.json.JSONObject;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.Arrays;
import java.util.Objects;

/** Observes existing navigation outputs; it never supplies inputs to navigation. */
public final class NavigationRecording {
    private static Context context;
    private static String session;
    private static long lastCloudMillis=-1,lastResourcesMillis=-1,lastHeadingMillis=-1,mapSequence,lastMapGeneration=-1;
    private static final ByteBuffer imu=ByteBuffer.allocate(100*44).order(ByteOrder.LITTLE_ENDIAN);
    private static final java.io.ByteArrayOutputStream featureBatch=new java.io.ByteArrayOutputStream();
    private static int featureFrames;
    private static final ArchiveBinary.LocalDelta delta=new ArchiveBinary.LocalDelta();
    private static float[] latestLeaves;
    private static JSONObject latestMapMetadata;
    private static volatile String modelSha256;
    private static boolean hashStarted;
    private NavigationRecording() {}

    public static synchronized void initialize(Context app) {
        context=app.getApplicationContext(); NavigationArchive.initialize(context);
        try {NativeMapArchive.initialize(context);}
        catch(Exception|LinkageError error){android.util.Log.w("NavigationRecording","Full map adapter unavailable",error);}
        if(!hashStarted) {
            hashStarted=true;
            Thread hash=new Thread(() -> {
                try(java.io.InputStream in=context.getAssets().open(BuildConfig.SEMANTIC_MODEL_ASSET)) {
                    java.security.MessageDigest digest=java.security.MessageDigest.getInstance("SHA-256");
                    byte[] block=new byte[65536];int n;while((n=in.read(block))!=-1)digest.update(block,0,n);
                    StringBuilder text=new StringBuilder();for(byte value:digest.digest())text.append(String.format(java.util.Locale.US,"%02x",value&255));
                    modelSha256=text.toString();
                    NavigationArchive.event("model_identity",j("asset",BuildConfig.SEMANTIC_MODEL_ASSET,"sha256",modelSha256));
                }catch(Exception error){android.util.Log.w("NavigationRecording","Model identity unavailable",error);}
            },"navigation-model-identity");hash.setDaemon(true);hash.start();
        }
    }
    public static void safe(Runnable action) {
        try {action.run();}
        catch(Exception error) {
            android.util.Log.w("NavigationRecording","Recording observation failed",error);
            NavigationArchive.problemFor(NavigationArchive.activeSessionId(),"recording_observation_error",j("error",error.toString()));
        }
    }
    public static JSONObject startMetadata() {
        return j("model",BuildConfig.SEMANTIC_MODEL_NAME,"model_width",BuildConfig.SEMANTIC_MODEL_WIDTH,
                "model_height",BuildConfig.SEMANTIC_MODEL_HEIGHT,"model_asset",BuildConfig.SEMANTIC_MODEL_ASSET,
                "model_sha256",modelSha256,"source_version",BuildConfig.NAV_SOURCE_VERSION,"raw_rgb",false,"raw_depth","per_executed_inference",
                "aligned_depth","per_executed_inference","inference_rgb_jpeg_quality",80,"video",j("width",640,"height",480,"target_fps",15,"target_bitrate",1000000,"segment_seconds",30),"point_cloud_interval_ms",10000,"point_cloud_ground_included",true,
                "octomap_export_scope","full_native_checkpoints_and_lossless_file_deltas","full_octomap_tree_export","roundtrip_checked_at_runtime");
    }
    private static synchronized String current() {
        String active=NavigationArchive.activeSessionId();
        if(active==null||NavigationArchive.isClosing(active))return null;
        if(!Objects.equals(active,session)) {
            session=active;imu.clear();featureBatch.reset();featureFrames=0;delta.clear();latestLeaves=null;latestMapMetadata=null;
            lastCloudMillis=lastResourcesMillis=lastHeadingMillis=-1;mapSequence=0;lastMapGeneration=-1;
            NavigationArchive.eventFor(active,"recording_configuration",startMetadata());

            if(context!=null) {
                for(String asset:new String[]{"source_port/semantic/dataconfig_mapillary.json","source_port/planning/dataconfig_mapillary_extend.json"}) {
                    try(java.io.InputStream in=context.getAssets().open(asset);java.io.ByteArrayOutputStream out=new java.io.ByteArrayOutputStream()) {
                        byte[] block=new byte[4096];int n;while((n=in.read(block))!=-1)out.write(block,0,n);
                        NavigationArchive.blobFor(active,"semantic_configuration","json",out.toByteArray(),j("asset",asset));
                    }catch(Exception error){NavigationArchive.eventFor(active,"recording_configuration_error",j("asset",asset,"message",error.toString()));}
                }
            }
        }
        return active;
    }
    static JSONObject j(Object... values){return NavigationArchive.json(values);}
    static JSONArray array(float[] values){JSONArray out=new JSONArray();if(values!=null)for(float v:values)out.put(Float.isFinite(v)?v:JSONObject.NULL);return out;}
    static JSONArray array(double[] values){JSONArray out=new JSONArray();if(values!=null)for(double v:values)out.put(Double.isFinite(v)?v:JSONObject.NULL);return out;}
    static JSONObject pose(VinsMono.Pose p) {
        if(p==null)return j("available",false);
        return j("timestamp_seconds",p.timestamp,"initialized",p.initialized,"position_m",array(new double[]{p.x,p.y,p.z}),
                "quaternion_xyzw",array(new double[]{p.qx,p.qy,p.qz,p.qw}),"camera_to_world_row_major",array(p.cameraToWorld()));
    }
    static JSONObject evidence(FrameEvidence e) {
        return e==null?j():j("inference_id",e.inferenceId,"camera_seconds",e.cameraSeconds,"generation",e.generation,
                "capture_elapsed_ms",e.captureElapsedMillis,"submitted_elapsed_ms",e.submittedElapsedMillis);
    }
    static boolean accepts(FrameEvidence e){return e!=null&&e.recordingSessionId!=null&&Objects.equals(e.recordingSessionId,current());}

    static synchronized void imu(int type,double rawMs,String domain,double mappedMs,float[] value) {
        String id=current();if(id==null||value.length<3)return;
        // stream int32 (1 gyro, 2 accel), hardware ms f64, mapped Unix ms f64,
        // Android elapsed ns int64, xyz float32, domain (0 hardware,1 system,2 global,-1 unknown).
        int domainCode="HARDWARE_CLOCK".equals(domain)?0:"SYSTEM_TIME".equals(domain)?1:"GLOBAL_TIME".equals(domain)?2:-1;
        imu.putInt(type).putDouble(rawMs).putDouble(mappedMs).putLong(SystemClock.elapsedRealtimeNanos())
                .putFloat(value[0]).putFloat(value[1]).putFloat(value[2]).putInt(domainCode);
        if(!imu.hasRemaining())flushImu();
    }
    private static void flushImu() {
        if(imu.position()==0||session==null)return;
        NavigationArchive.blobFor(session,"d455_imu","bin",Arrays.copyOf(imu.array(),imu.position()),
                j("format","le:i32_stream,f64_raw_ms,f64_unix_ms,i64_elapsed_ns,f32_x,f32_y,f32_z,i32_domain",
                        "domains",j("0","HARDWARE_CLOCK","1","SYSTEM_TIME","2","GLOBAL_TIME","-1","unknown"),
                        "record_bytes",44,"count",imu.position()/44,"gyro_unit","rad/s","accel_unit","m/s^2"));imu.clear();
    }
    static synchronized void heading(float degrees) {
        String id=current();if(id==null)return;long now=SystemClock.elapsedRealtime();
        if(lastHeadingMillis>=0&&now-lastHeadingMillis<100)return;lastHeadingMillis=now;
        NavigationArchive.eventFor(id,"phone_heading",j("degrees",degrees,"reference","magnetic_north"));
    }
    static void location(Location fix,String role) {
        String id=current();if(id==null)return;
        NavigationArchive.eventFor(id,"location",j("role",role,"latitude",fix.getLatitude(),"longitude",fix.getLongitude(),
                "coordinate_system","WGS84","accuracy_m",fix.hasAccuracy()?fix.getAccuracy():null,
                "altitude_m",fix.hasAltitude()?fix.getAltitude():null,"speed_mps",fix.hasSpeed()?fix.getSpeed():null,
                "provider",fix.getProvider(),"fix_unix_ms",fix.getTime(),"fix_elapsed_ns",fix.getElapsedRealtimeNanos()));
    }
    static synchronized void vins(VinsMono.Pose value,double[] features,long generation) {
        String id=current();if(id==null)return;
        NavigationArchive.eventFor(id,"vins_pose",j("generation",generation,"pose",pose(value),"feature_count",features.length/8));
        ByteBuffer header=ByteBuffer.allocate(20).order(ByteOrder.LITTLE_ENDIAN);
        header.putDouble(value==null?Double.NaN:value.timestamp).putLong(generation).putInt(features.length/8);
        featureBatch.write(header.array(),0,20);byte[] data=ArchiveBinary.doubles(features);featureBatch.write(data,0,data.length);
        if(++featureFrames>=10)flushFeatures();
    }
    private static void flushFeatures() {
        if(featureFrames==0)return;
        NavigationArchive.blobFor(session,"vins_features","bin",featureBatch.toByteArray(),
            j("format","le:repeated[f64_timestamp_seconds,i64_generation,i32_count,count*f64[8]]","fields","id,x,y,z,u,v,velocity_x,velocity_y","frame_count",featureFrames));
        featureBatch.reset();featureFrames=0;
    }

    static void semantic(int[] mask,float[] confidence,int width,int height,FrameEvidence e) {
        if(!accepts(e))return;JSONObject meta=evidence(e);
        put(meta,"width",width);put(meta,"height",height);put(meta,"format","uint8_label_id");
        NavigationArchive.blobFor(e.recordingSessionId,"semantic_labels","bin",ArchiveBinary.labels(mask),meta);
        JSONObject conf=evidence(e);put(conf,"width",width);put(conf,"height",height);put(conf,"format","le:float32_confidence");
        NavigationArchive.blobFor(e.recordingSessionId,"semantic_confidence","bin",ArchiveBinary.floats(confidence),conf);
    }
    static synchronized void cloud(SemanticPointCloud.Data cloud,VinsMono.Pose capture,FrameEvidence e) {
        if(!accepts(e))return;long now=SystemClock.elapsedRealtime();
        if(lastCloudMillis>=0&&now-lastCloudMillis<10000)return;
        JSONObject meta=evidence(e);put(meta,"capture_pose",pose(capture));put(meta,"point_count",cloud.confidence.length);
        put(meta,"coordinate_system","camera_optical_x_right_y_down_z_forward_m");put(meta,"ground_included",true);
        put(meta,"format","le:f32_x,f32_y,f32_z,u32_semantic_rgb,f32_confidence");put(meta,"record_bytes",20);
        if(NavigationArchive.blobFor(e.recordingSessionId,"semantic_point_cloud","bin",ArchiveBinary.cloud(cloud),meta))lastCloudMillis=now;
    }
    static synchronized void map(float[] leaves,SemanticSegmenter.Result result,VinsMono.Pose capture,long handle) {
        FrameEvidence e=result.evidence;if(!accepts(e))return;
        if(e.generation!=lastMapGeneration){delta.clear();lastMapGeneration=e.generation;mapSequence=0;}
        JSONObject meta=evidence(e);put(meta,"sequence",++mapSequence);put(meta,"scope","local_occupied_export");
        put(meta,"full_native_tree",false);put(meta,"capture_pose",pose(capture));put(meta,"projection_pose",pose(result.mapPose));
        put(meta,"native_total_leaf_count",result.octomapLeafCount);put(meta,"exported_leaf_count",leaves.length/8);
        put(meta,"format","le:u32_upsert_count,u32_scope_exit_count,upserts[f32 x8],scope_exits[f32 x3]");
        ArchiveBinary.LocalDelta.Change change=delta.update(leaves);
        put(meta,"upserts",change.upserts);put(meta,"scope_exits",change.exits);
        put(meta,"scope_exit_meaning","absent_from_local_export; NOT proof of native deletion or free space");
        // Periodic full scoped checkpoints recover the chain after a queue/storage gap.
        if(mapSequence==1||mapSequence%10==0)NavigationArchive.blobFor(e.recordingSessionId,"octomap_local_checkpoint","bin",ArchiveBinary.floats(leaves),leafMeta(meta));
        NavigationArchive.blobFor(e.recordingSessionId,"octomap_local_delta","bin",change.bytes,meta);
        latestLeaves=leaves.clone();latestMapMetadata=leafMeta(meta);
        if(result.localCostGrid!=null)grid("local_cost_grid",result.localCostGrid,e,result.mapPose);
        if(result.displayCostGrid!=null)grid("display_cost_grid",result.displayCostGrid,e,result.mapPose);
        NavigationArchive.eventFor(e.recordingSessionId,"semantic_map",j("evidence",evidence(e),"label",result.label,
                "not_walkable",result.isNotWalkable,"area_ratio",result.areaRatio,"known_cells",result.costGridKnownCount,
                "ground_height_m",result.groundHeight,"ground_cleared_cells",result.groundClearedCells));
    }
    private static JSONObject leafMeta(JSONObject source) {
        try {JSONObject out=new JSONObject(source.toString());put(out,"format","le:f32_x,f32_y,f32_z,f32_occupancy,f32_r,f32_g,f32_b,f32_semantic_confidence");return out;}
        catch(Exception error){return j("scope","local_occupied_export","full_native_tree",false);}
    }
    private static void grid(String kind,int[] values,FrameEvidence e,VinsMono.Pose pose) {
        JSONObject meta=evidence(e);put(meta,"width",80);put(meta,"height",80);put(meta,"resolution_m",0.2);
        put(meta,"origin_xy_m",array(new double[]{-7.9,-7.9}));put(meta,"coordinate_system","ego_camera_right_forward");
        put(meta,"map_pose",pose(pose));put(meta,"format","le:int32_row_major");
        NavigationArchive.blobFor(e.recordingSessionId,kind,"bin",ArchiveBinary.ints(values),meta);
    }
    static void plan(LocalPlanner.PathResult value,FrameEvidence e,long durationNs,boolean movementAuthorized) {
        if(!accepts(e))return;JSONArray path=new JSONArray();for(float[] point:value.worldPath)path.put(array(point));
        NavigationArchive.eventFor(e.recordingSessionId,"local_plan",j("evidence",evidence(e),"path_m",path,
                "path_coordinate_system","ego_grid_xy_m","planned",value.planned,"success",value.success,"blocked",value.blocked,
                "steering_degrees",value.steeringDegrees,"start_cost",value.startCost,"target_cost",value.targetCost,
                "obstacle_count",value.obstacleCount,"waiting_reason",value.waitingReason,"duration_ns",durationNs,
                "movement_authorized",movementAuthorized));
    }
    static void route(AmapRouteClient.RouteResult route) {
        String id=current();if(id==null||route==null)return;JSONArray points=new JSONArray(),steps=new JSONArray();
        for(AmapRouteClient.GeoPoint p:route.polyline)points.put(array(new double[]{p.latitude,p.longitude}));
        for(AmapRouteClient.RouteStep s:route.steps)steps.put(j("instruction",s.instruction,"distance_m",s.distanceMeters,"action",s.action));
        NavigationArchive.eventFor(id,"global_route",j("destination",route.destinationName,"destination_latitude",route.destinationLatitude,
                "destination_longitude",route.destinationLongitude,"coordinate_system","GCJ02","polyline",points,"steps",steps,
                "distance_m",route.distanceMeters,"duration_s",route.durationSeconds));
    }
    static synchronized void resources(VinsInputBuffer.Status input) {
        String id=current();if(id==null||context==null)return;long now=SystemClock.elapsedRealtime();
        if(lastResourcesMillis>=0&&now-lastResourcesMillis<5000)return;lastResourcesMillis=now;
        Runtime runtime=Runtime.getRuntime();Intent battery=context.registerReceiver(null,new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
        JSONObject data=j("heap_used_bytes",runtime.totalMemory()-runtime.freeMemory(),"heap_max_bytes",runtime.maxMemory(),
                "native_heap_bytes",Debug.getNativeHeapAllocatedSize(),"queued_vins_images",input.queuedImages,
                "dropped_vins_images",input.droppedImages,"camera_images",input.images,"gyro_samples",input.gyroscope,
                "accel_samples",input.accelerometer,"paired_images",input.pairedImages);
        if(battery!=null){put(data,"battery_level",battery.getIntExtra(BatteryManager.EXTRA_LEVEL,-1));put(data,"battery_scale",battery.getIntExtra(BatteryManager.EXTRA_SCALE,-1));put(data,"battery_temperature_tenths_c",battery.getIntExtra(BatteryManager.EXTRA_TEMPERATURE,-1));}
        NavigationArchive.eventFor(id,"resources",data);
    }
    public static void finishSensors() {
        NavigationVideoRecorder.finish();
        InferenceInputRecorder.finish();
        FullMapRecorder.finish();
        synchronized(NavigationRecording.class) {
        String id=NavigationArchive.activeSessionId();if(id==null||!Objects.equals(id,session))return;
        flushImu();flushFeatures();
        if(latestLeaves!=null)NavigationArchive.blobFor(id,"octomap_local_final_snapshot","bin",ArchiveBinary.floats(latestLeaves),latestMapMetadata);
        NavigationArchive.eventFor(id,"recording_scope_summary",j("full_octomap_tree_export","roundtrip_checked_at_runtime","raw_camera_streams_recorded",false,
                "point_cloud_interval_ms",10000,"point_cloud_ground_included",true,"model_sha256",modelSha256));
        latestLeaves=null;delta.clear();
        }
    }
    static void put(JSONObject object,String key,Object value){try{object.put(key,value==null?JSONObject.NULL:value);}catch(Exception ignored){}}
}
