package com.elabrador.mobilenavigation;

import java.io.BufferedInputStream;
import java.io.DataInputStream;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Random;
import org.junit.Test;
import static org.junit.Assert.*;

/**
 * Real D455f cloud + measured pose replay for confidence-only ground handling.
 * DCR2 has no OctoMap leaves. The separate grid check is an EMPTY-TREE API
 * contract, not real nonempty mapping, native fusion, NPU accuracy or speed.
 */
public class SourceGroundDeviceReplayTest {
    // Semantic metadata indices 15, 11, 23 in the native tree's BGR contract.
    private static final int[] WALKABLE={0xe823f4,0x606060,0x8080c8};

    private static final class Recorded {
        final String name;
        final SemanticPointCloud.Data cloud;
        final VinsMono.Pose pose;
        final FrameEvidence evidence;
        final long submitted;
        final int poses;

        Recorded(String name,SemanticPointCloud.Data cloud,VinsMono.Pose pose,
                 FrameEvidence evidence,long submitted,int poses) {
            this.name=name;this.cloud=cloud;this.pose=pose;this.evidence=evidence;
            this.submitted=submitted;this.poses=poses;
        }
    }

    private static List<String> recordings() {
        List<String> files=new ArrayList<>();files.add("device-capture-v10.bin");
        for(int i=0;i<5;i++)files.add("device-office-"+i+".bin");
        for(int i=0;i<6;i++)files.add("device-down-"+i+".bin");
        for(int i=0;i<2;i++)files.add("device-overred-"+i+".bin");
        return files;
    }

    private Recorded read(String name) throws Exception {
        InputStream resource=getClass().getResourceAsStream("/"+name);
        assertNotNull("Missing original D455f recording "+name,resource);
        try(DataInputStream input=new DataInputStream(new BufferedInputStream(resource))) {
            assertEquals(name+" DCR2 header",0x44435232,input.readInt());
            int count=input.readInt();assertTrue(name+" point count",count>0 && count<1000000);
            float[] xyz=new float[count*3],confidence=new float[count];int[] colors=new int[count];
            for(int i=0;i<xyz.length;i++)xyz[i]=input.readFloat();
            for(int i=0;i<count;i++)colors[i]=input.readInt();
            for(int i=0;i<count;i++)confidence[i]=input.readFloat();
            double time=input.readDouble();long generation=input.readLong();
            long capture=input.readLong(),submitted=input.readLong();
            FrameEvidence evidence=new FrameEvidence(time,generation,capture);
            VinsPoseHistory history=new VinsPoseHistory();int poses=input.readInt();
            assertTrue(name+" recorded pose history",poses>1 && poses<=10000);
            for(int p=0;p<poses;p++) {
                double[] value=new double[12],rotation=new double[9],translation=new double[3];
                for(int i=0;i<7;i++)value[i]=input.readDouble();
                value[10]=input.readBoolean()?1:0;value[11]=input.readDouble();
                for(int i=0;i<9;i++)rotation[i]=input.readDouble();
                for(int i=0;i<3;i++)translation[i]=input.readDouble();
                history.add(new VinsMono.Pose(value,rotation,translation));
            }
            // Match the frame timestamp using actual recorded bracketing poses;
            // no nearest/current pose or synthetic replacement is permitted.
            VinsMono.Pose pose=history.at(time);
            assertNotNull(name+" must have a measured/interpolated frame pose",pose);
            assertTrue(name+" pose initialized",pose.initialized);
            assertEquals(name+" acquisition/pose synchronization",time,pose.timestamp,0);
            int pathSize=input.readInt();assertTrue(pathSize>=0 && pathSize<100000);
            for(int i=0;i<pathSize*2;i++)assertTrue(name+" original path coordinate",Float.isFinite(input.readFloat()));
            assertEquals(name+" must consume the actual recording",-1,input.read());
            return new Recorded(name,new SemanticPointCloud.Data(xyz,colors,confidence),pose,evidence,submitted,poses);
        }
    }

    private static boolean walkable(int color) {
        for(int value:WALKABLE)if(value==color)return true;
        return false;
    }

    private static void sameBits(String message,float[] expected,float[] actual) {
        assertEquals(message+" length",expected.length,actual.length);
        for(int i=0;i<expected.length;i++)assertEquals(message+" index="+i,
                Float.floatToRawIntBits(expected[i]),Float.floatToRawIntBits(actual[i]));
    }

    @Test public void recordedGroundBoostNeverChangesGeometryClassesOrNonwalkableConfidence() throws Exception {
        int frames=0,totalPoints=0,totalWalkable=0,totalNonwalkable=0,totalBoosted=0,validPlanes=0;
        for(String name:recordings()) {
            Recorded frame=read(name);
            float[] xyz=frame.cloud.xyz.clone(),confidence=frame.cloud.confidence.clone();
            int[] colors=frame.cloud.semanticRgb.clone();
            float[] cameraToWorld=frame.pose.cameraToWorld(),transformBefore=cameraToWorld.clone();
            assertEquals(16,cameraToWorld.length);
            for(float value:cameraToWorld)assertTrue(name+" finite actual camera transform",Float.isFinite(value));
            assertTrue(name+" actual ego yaw",Float.isFinite(frame.pose.egoRightAxisYawRadians()));
            for(int i=0;i<colors.length;i++) {
                int p=i*3;
                for(int j=0;j<3;j++)assertTrue(name+" finite recorded point",Float.isFinite(xyz[p+j]));
                for(int row=0;row<3;row++) {
                    double world=cameraToWorld[row*4]*xyz[p]+cameraToWorld[row*4+1]*xyz[p+1]
                            +cameraToWorld[row*4+2]*xyz[p+2]+cameraToWorld[row*4+3];
                    assertTrue(name+" finite world-transformed coordinate",Double.isFinite(world));
                }
                assertTrue(name+" recorded confidence",Float.isFinite(confidence[i]) && confidence[i]>=0);
            }
            SourceGroundConfidence.Result result=SourceGroundConfidence.apply(
                    frame.cloud,cameraToWorld,WALKABLE,new Random(0x445535L+frames));
            sameBits(name+" raw xyz must remain byte-for-byte unchanged",xyz,frame.cloud.xyz);
            sameBits(name+" measured transform must remain unchanged",transformBefore,cameraToWorld);
            assertArrayEquals(name+" original class colors",colors,frame.cloud.semanticRgb);
            int changed=0,walkable=0,nonwalkable=0;
            for(int i=0;i<colors.length;i++) {
                float actual=frame.cloud.confidence[i];
                assertTrue(name+" boosted confidence must remain finite",Float.isFinite(actual) && actual>=0);
                if(walkable(colors[i])) {
                    walkable++;
                    assertTrue(name+" only unchanged or source x10 confidence is permitted at "+i,
                            Float.floatToRawIntBits(actual)==Float.floatToRawIntBits(confidence[i])
                            || Float.floatToRawIntBits(actual)==Float.floatToRawIntBits(confidence[i]*10f));
                } else {
                    nonwalkable++;
                    assertEquals(name+" nonwalkable confidence at "+i,
                            Float.floatToRawIntBits(confidence[i]),Float.floatToRawIntBits(actual));
                }
                if(Float.floatToRawIntBits(actual)!=Float.floatToRawIntBits(confidence[i]))changed++;
            }
            assertTrue(name+" boost count",result.boostedPoints>=changed && result.boostedPoints<=walkable);
            assertTrue(name+" candidate count",result.candidatePoints>=0 && result.candidatePoints<=colors.length);
            assertTrue(name+" inlier count",result.inliers>=0 && result.inliers<=result.candidatePoints);
            if(!result.planeValid) {
                assertEquals(name+" rejected plane cannot boost",0,result.boostedPoints);
                sameBits(name+" rejected plane preserves all confidence",confidence,frame.cloud.confidence);
            }
            frames++;totalPoints+=colors.length;totalWalkable+=walkable;totalNonwalkable+=nonwalkable;
            totalBoosted+=result.boostedPoints;if(result.planeValid)validPlanes++;
            System.out.println("D455F_SOURCE_BOOST_REPLAY file="+name+" points="+colors.length+
                    " walkable="+walkable+" nonwalkable="+nonwalkable+" candidates="+result.candidatePoints+
                    " plane_valid="+result.planeValid+" inliers="+result.inliers+" boosted="+result.boostedPoints+
                    " changed_confidence="+changed+" camera_seconds="+frame.evidence.cameraSeconds+
                    " capture_elapsed="+frame.evidence.captureElapsedMillis+" recorded_age_ms="+frame.evidence.ageMillis(frame.submitted)+
                    " pose_seconds="+frame.pose.timestamp+" pose_xyz="+frame.pose.x+","+frame.pose.y+","+frame.pose.z+
                    " ego_yaw="+frame.pose.egoRightAxisYawRadians()+" recorded_poses="+frame.poses+
                    " xyz_unchanged=true classes_unchanged=true nonwalkable_confidence_unchanged=true"+
                    " scope=real_cloud_boost_and_pose_only no_native_tree=true no_speed_measurement=true");
        }
        assertEquals(14,frames);assertTrue(totalPoints>150000);assertTrue(totalWalkable>0);assertTrue(totalNonwalkable>0);
        System.out.println("D455F_SOURCE_BOOST_REPLAY_TOTAL frames="+frames+" points="+totalPoints+
                " valid_planes="+validPlanes+" boosted="+totalBoosted+" walkable="+totalWalkable+
                " nonwalkable="+totalNonwalkable+" real_nonempty_mapping_verified=false");
    }

    @Test public void recordedPoseAndEvidenceSurviveResultWrappingWithoutInventingNativeLeavesOrFreshness() throws Exception {
        int frames=0;
        for(String name:recordings()) {
            Recorded frame=read(name);FrameEvidence evidence=frame.evidence;
            double cameraSeconds=evidence.cameraSeconds;long generation=evidence.generation,capture=evidence.captureElapsedMillis;
            long age=evidence.ageMillis(frame.submitted);boolean fresh=evidence.fresh(frame.submitted);
            // DCR2 stores semantic cloud points, not occupancy-fused OctoMap
            // leaves. Native ARM/Android tree insertion cannot run on this host.
            // Check only the explicit empty-tree contract at the REAL pose.
            MapTransform.Grid raw=MapTransform.octree2localprmapSource(new float[0],
                    (float)frame.pose.x,(float)frame.pose.y,(float)frame.pose.z,0,Collections.<Integer,Float>emptyMap());
            for(int value:MapTransform.sourceHeightEdges(raw.height))assertEquals(name+" unknown height gradient",0,value);
            MapTransform.Grid grid=MapTransform.sourcePlanningGrid(new float[0],
                    (float)frame.pose.x,(float)frame.pose.y,(float)frame.pose.z,
                    frame.pose.egoRightAxisYawRadians(),Collections.<Integer,Float>emptyMap());
            assertEquals(0,grid.known);assertEquals(6400,grid.cost.length);
            assertEquals(6400,grid.displayCost.length);assertEquals(6400,grid.height.length);
            for(int i=0;i<6400;i++) {
                assertEquals(name+" empty-tree planning cost",50,grid.cost[i]);
                assertEquals(name+" empty-tree display remains unknown",-1,grid.displayCost[i]);
                assertEquals(name+" empty-tree height remains unknown",-1,(int)grid.height[i]);
            }
            SemanticSegmenter.Result result=new SemanticSegmenter.Result(false,"DCR2 empty-tree contract",0f,
                    0L,"HOST_CONTRACT_NO_MODEL",50f,50f,50f,Float.NaN,frame.cloud.confidence.length,
                    0,grid.known,grid.groundHeight,0,grid.cost,frame.pose)
                    .withEvidence(evidence).withDisplayCostGrid(grid.displayCost).withMapPose(frame.pose);
            assertSame(name+" result must retain acquisition evidence object",evidence,result.evidence);
            assertSame(name+" result must retain matched measured pose",frame.pose,result.mapPose);
            assertSame(name+" separate display map",grid.displayCost,result.displayCostGrid);
            assertEquals(cameraSeconds,result.evidence.cameraSeconds,0);
            assertEquals(generation,result.evidence.generation);assertEquals(capture,result.evidence.captureElapsedMillis);
            assertEquals(age,result.evidence.ageMillis(frame.submitted));assertEquals(fresh,result.evidence.fresh(frame.submitted));
            assertEquals(1500,FrameEvidence.MAX_AGE_MILLIS);
            assertTrue(result.evidence.fresh(capture+1499));assertFalse(result.evidence.fresh(capture+1500));
            assertFalse(result.evidence.fresh(capture-1));
            frames++;
        }
        assertEquals(14,frames);
        System.out.println("D455F_SOURCE_EMPTY_TREE_CONTRACT frames="+frames+
                " recorded_pose=true original_evidence_preserved=true freshness_limit_ms="+FrameEvidence.MAX_AGE_MILLIS+
                " planning_cost=50 display_cost=-1 height=-1 native_fusion_executed=false"+
                " real_nonempty_mapping_verified=false no_speed_measurement=true");
    }
}
