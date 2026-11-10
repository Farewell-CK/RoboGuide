package com.elabrador.mobilenavigation;

import com.elabrador.mobilenavigation.archive.NavigationArchive;
import org.json.JSONObject;
import java.io.*;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.util.Objects;

/** Called only at map-worker boundaries; final seal waits for the short snapshot operation. */
final class FullMapRecorder {
    private static File previous;private static String session;private static long generation=-1,sequence;private static JSONObject lastMetadata;
    private static boolean forceCheckpoint;
    static synchronized void record(long handle,FrameEvidence evidence) {
        if(!NavigationRecording.accepts(evidence))return;
        String id=evidence.recordingSessionId;
        if(!Objects.equals(id,session)){
            discard(previous);previous=null;session=id;generation=-1;sequence=0;lastMetadata=null;forceCheckpoint=true;
        }
        if(generation!=evidence.generation){finish();generation=evidence.generation;sequence=0;forceCheckpoint=true;}
        File next=null,artifact=null;
        try {
            next=NavigationArchive.stagingFileFor(id,"navmap");
            File occupancy=new File(next.getPath()+".ot.tmp"),semantics=new File(occupancy.getPath()+".sem");int leaves;
            try {
                leaves=NativeMapArchive.nativeSaveVerified(handle,occupancy.getAbsolutePath());
                MapBundle.write(occupancy,semantics,next);
            } finally {Files.deleteIfExists(occupancy.toPath());Files.deleteIfExists(semantics.toPath());}
            long nextSequence=sequence+1;
            JSONObject meta=NavigationRecording.evidence(evidence);NavigationRecording.put(meta,"sequence",nextSequence);
            NavigationRecording.put(meta,"scope","complete_current_native_tree");NavigationRecording.put(meta,"historical_crop_radius_m",10);
            NavigationRecording.put(meta,"format","zip:occupancy.ot+semantics.bin; NSEM0001; topology/occupancy and full semantic fields verified");
            NavigationRecording.put(meta,"leaf_count",leaves);NavigationRecording.put(meta,"sha256",hash(next));
            // Verification confirms this private snapshot, not admission or persistence of its chain.
            NavigationArchive.eventFor(id,"octomap_full_snapshot_verified",meta);
            String type,extension;
            if(previous==null||forceCheckpoint||nextSequence%10==0) {
                type="octomap_full_checkpoint";extension="navmap";
                NavigationRecording.put(meta,"checkpoint_reason",previous==null?"first_snapshot":forceCheckpoint?"generation_or_chain_gap":"periodic");
                artifact=NavigationArchive.stagingFileFor(id,extension);Files.copy(next.toPath(),artifact.toPath());
            } else {
                type="octomap_full_delta";extension="gz";
                artifact=NavigationArchive.stagingFileFor(id,extension);SnapshotPatch.create(previous,next,artifact);
                NavigationRecording.put(meta,"base_sha256",hash(previous));NavigationRecording.put(meta,"delta_format","gzip:DataOutput big-endian magic NDT1,target_length_i64,(offset_i64,length_i32,bytes)*,offset=-1");
            }
            if(!NavigationArchive.fileFor(id,type,extension,artifact,meta)){
                forceCheckpoint=true;
                NavigationArchive.problemFor(id,"octomap_full_chain_gap",NavigationRecording.j(
                        "reason","artifact_not_admitted","attempted_sequence",nextSequence,"artifact_type",type,
                        "generation",evidence.generation,"inference_id",evidence.inferenceId,"next_requires_checkpoint",true));
                return;
            }
            // fileFor owns artifact after admission. Keep the old baseline until that succeeds.
            artifact=null;
            discard(previous);previous=next;next=null;lastMetadata=meta;sequence=nextSequence;forceCheckpoint=false;
            NavigationArchive.eventFor(id,"octomap_full_chain_admitted",NavigationRecording.j(
                    "sequence",sequence,"generation",evidence.generation,"inference_id",evidence.inferenceId,
                    "artifact_type",type,"sha256",meta.optString("sha256"),
                    "persistence_confirmation","artifact_event_and_manifest"));
        }catch(Exception|LinkageError error) {
            forceCheckpoint=true;
            NavigationArchive.problemFor(id,"octomap_full_export_failed",NavigationRecording.j("error",error.toString(),"generation",evidence.generation,"inference_id",evidence.inferenceId));
        }finally{
            // Only private or rejected files remain here; admitted files may be moving on the writer.
            discard(artifact);discard(next);
        }
    }
    static synchronized void finish() {
        if(previous!=null&&session!=null) {
            if(!NavigationArchive.fileFor(session,"octomap_full_final_snapshot","navmap",previous,lastMetadata==null?NavigationRecording.j():lastMetadata)){
                forceCheckpoint=true;
                NavigationArchive.problemFor(session,"octomap_final_snapshot_failed",NavigationRecording.j("reason","artifact_not_admitted"));
            }else previous=null;
        }
    }
    private static void discard(File file){
        if(file==null)return;
        try{Files.deleteIfExists(file.toPath());}
        catch(Exception error){NavigationArchive.problemFor(session,"octomap_staging_cleanup_failed",NavigationRecording.j("file",file.getName(),"error",error.toString()));}
    }
    private static String hash(File file)throws Exception {
        MessageDigest d=MessageDigest.getInstance("SHA-256");try(InputStream in=new FileInputStream(file)){byte[] b=new byte[65536];int n;while((n=in.read(b))!=-1)d.update(b,0,n);}
        StringBuilder out=new StringBuilder();for(byte b:d.digest())out.append(String.format(java.util.Locale.US,"%02x",b&255));return out.toString();
    }
}
