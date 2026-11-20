package com.elabrador.mobilenavigation;

import com.elabrador.mobilenavigation.archive.NavigationArchive;
import org.json.JSONObject;

/** Read-only observations of the configuration actually used by navigation. */
public final class RuntimeRecordingConfiguration {
    private static final Object lock=new Object();
    private static final JSONObject observed=new JSONObject();
    private RuntimeRecordingConfiguration(){}

    /** Returns owned JSON; unavailable observations are explicit, never guessed. */
    public static JSONObject snapshot(){
        return snapshotAt(System.currentTimeMillis(),android.os.SystemClock.elapsedRealtimeNanos());
    }

    static JSONObject snapshotAt(long unixMs,long elapsedNs){
        synchronized(lock){
            JSONObject result=copy(observed);
            put(result,"schema_version",1);
            put(result,"snapshot_unix_ms",unixMs);
            put(result,"snapshot_elapsed_ns",elapsedNs);
            for(String section:new String[]{"camera","camera_state","vins","camera_exposure","navigation"})
                if(!result.has(section))put(result,section,json("available",false,"reason","not_observed_in_this_process"));
            put(result,"phone_positioning",PhonePoseTracker.recordingConfiguration());
            put(result,"local_planner",LocalPlanner.recordingConfiguration());
            put(result,"route_follower",RouteFollower.recordingConfiguration());
            put(result,"semantic_pipeline",SemanticSegmenter.recordingConfiguration());
            put(result,"interpretation","Observed values may predate this navigation start; use each observed_at_unix_ms/elapsed_ns and camera_state. Unsupported device metadata stays unavailable.");
            return result;
        }
    }

    static void observe(String section,JSONObject value){
        try {
            JSONObject owned=remember(section,value,System.currentTimeMillis(),android.os.SystemClock.elapsedRealtimeNanos());
            // Never hold the cache lock while entering the archive facade.
            String session=NavigationArchive.activeSessionId();
            if(session!=null&&!NavigationArchive.isClosing(session))
                NavigationArchive.eventFor(session,"runtime.configuration",json("section",section,"configuration",owned));
        }catch(Exception ignored){/* Observation must not change navigation. */}
    }

    static JSONObject remember(String section,JSONObject value,long unixMs,long elapsedNs){
        JSONObject owned=copy(value);
        put(owned,"observed_at_unix_ms",unixMs);put(owned,"observed_at_elapsed_ns",elapsedNs);
        synchronized(lock){put(observed,section,copy(owned));}
        return owned;
    }

    static JSONObject json(Object... values){return NavigationArchive.json(values);}
    static void put(JSONObject target,String key,Object value){try{target.put(key,value==null?JSONObject.NULL:value);}catch(Exception ignored){}}
    private static JSONObject copy(JSONObject value){try{return new JSONObject(value.toString());}catch(Exception ignored){return new JSONObject();}}
}
