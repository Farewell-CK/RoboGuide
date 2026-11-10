package com.elabrador.mobilenavigation;

import java.io.BufferedInputStream;
import java.io.DataInputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import org.junit.Test;
import static org.junit.Assert.*;

/** A current-pose view must preserve source costs and the old acquisition identity. */
public class SourceCurrentPoseProjectionTest {
    private static final int HARD=0x112233,LOW=0x445566,MEDIUM=0x778899;

    private static String text(DataInputStream input) throws Exception {
        int count=input.readInt();assertTrue(count>=0 && count<=4096);
        byte[] bytes=new byte[count];input.readFully(bytes);
        return new String(bytes,StandardCharsets.UTF_8);
    }

    @Test public void unchangedProjectionPosePreservesEveryCellOfAllOfficialSourceCases() throws Exception {
        InputStream resource=getClass().getResourceAsStream("/source-cost-reference.bin");assertNotNull(resource);
        try(DataInputStream input=new DataInputStream(new BufferedInputStream(resource))) {
            assertEquals(0x53434631,input.readInt());
            assertEquals("c758acdca0e9d05bb6f52337daefdce9b2b9983e",text(input));
            assertEquals(64,text(input).length());assertEquals(64,text(input).length());
            Map<Integer,Float> config=new HashMap<>();int labels=input.readInt();
            for(int i=0;i<labels;i++)config.put(input.readInt(),input.readFloat());
            int cases=input.readInt();assertTrue(cases>=90);
            for(int i=0;i<cases;i++) {
                String name=text(input);
                float x=input.readFloat(),y=input.readFloat(),z=input.readFloat(),yaw=input.readFloat();
                int leaves=input.readInt();float[] records=new float[leaves*8];
                for(int p=0;p<records.length;p++)records[p]=input.readFloat();
                byte[] originalOracle=new byte[12800];input.readFully(originalOracle);
                MapTransform.Grid old=MapTransform.sourcePlanningGrid(records,x,y,z,yaw,config);
                MapTransform.Grid current=MapTransform.sourcePlanningGrid(records,x,y,z,x,y,yaw,config);
                assertArrayEquals(name+" identical-pose planner",old.cost,current.cost);
                assertArrayEquals(name+" identical-pose display",old.displayCost,current.displayCost);
                assertArrayEquals(name+" identical-pose height",old.height,current.height);
                assertEquals(name+" identical-pose known count",old.known,current.known);
            }
            assertEquals(-1,input.read());
            System.out.println("SOURCE_CURRENT_POSE_IDENTITY official_cases="+cases+
                    " compared_grid_cells="+(cases*6400)+" acquisition_center_preserved=true");
        }
    }

    private static Map<Integer,Float> config() {
        Map<Integer,Float> result=new HashMap<>();
        result.put(HARD,1f);result.put(LOW,.25f);result.put(MEDIUM,.75f);return result;
    }

    private static float[] patches() {
        List<float[]> records=new ArrayList<>();
        float[][] centers={{1.2f,2.2f},{-3.2f,1.4f},{-1.8f,-2.6f}};
        int[] colors={HARD,LOW,MEDIUM};
        for(int i=0;i<centers.length;i++)for(int r=-2;r<=2;r++)for(int c=-2;c<=2;c++) {
            int color=colors[i];
            records.add(new float[]{centers[i][0]+c*.2f,centers[i][1]+r*.2f,-1.13f,
                    .9f,color&255,(color>>8)&255,(color>>16)&255,1});
        }
        float[] result=new float[records.size()*8];
        for(int i=0;i<records.size();i++)System.arraycopy(records.get(i),0,result,i*8,8);
        return result;
    }

    private static void sameCell(String message,MapTransform.Grid expected,int row,int col,
                                 MapTransform.Grid actual,int r,int c) {
        int index=r*80+c;
        if(row<0 || row>=80 || col<0 || col>=80) {
            assertEquals(message+" clipped planner is source unknown",50,actual.cost[index]);
            assertEquals(message+" clipped display remains unknown",-1,actual.displayCost[index]);
            assertEquals(message+" clipped height remains unknown",-1,(int)actual.height[index]);
        } else {
            int source=row*80+col;
            assertEquals(message+" planner",expected.cost[source],actual.cost[index]);
            assertEquals(message+" display",expected.displayCost[source],actual.displayCost[index]);
            assertEquals(message+" height",(int)expected.height[source],(int)actual.height[index]);
        }
    }

    @Test public void asymmetricPatchesFollowOfficialPositiveYawRightForwardConvention() {
        float[] leaves=patches();Map<Integer,Float> config=config();
        MapTransform.Grid world=MapTransform.sourcePlanningGrid(leaves,0,0,0,0,config);
        // Official x_ego=cos(yaw)*dx+sin(yaw)*dy and y_ego=-sin(yaw)*dx+cos(yaw)*dy.
        // Source center samples have x=.2*(col-39), y=.2*(row-39).
        // Thus a +90 degree turn maps [row,col] -> [78-col,row].
        assertEquals(100,world.displayCost[50*80+45]);
        assertEquals(25,world.displayCost[46*80+23]);
        assertEquals(75,world.displayCost[26*80+30]);
        for(int degrees:new int[]{90,180,-90}) {
            MapTransform.Grid current=MapTransform.sourcePlanningGrid(leaves,0,0,0,0,0,
                    (float)Math.toRadians(degrees),config);
            for(int r=0;r<80;r++)for(int c=0;c<80;c++) {
                int row,col;
                if(degrees==90){row=c;col=78-r;}
                else if(degrees==180){row=78-r;col=78-c;}
                else {row=78-c;col=r;}
                sameCell("yaw="+degrees+" row="+r+" col="+c,world,row,col,current,r,c);
            }
            assertEquals(world.known,current.known);
            if(degrees==90) {
                assertEquals(100,current.displayCost[33*80+50]);
                assertEquals(25,current.displayCost[55*80+46]);
                assertEquals(75,current.displayCost[48*80+26]);
            } else if(degrees==180) {
                assertEquals(100,current.displayCost[28*80+33]);
                assertEquals(25,current.displayCost[32*80+55]);
                assertEquals(75,current.displayCost[52*80+48]);
            }
        }
    }

    @Test public void twentyCentimeterProjectionTranslationShiftsExactlyOneCellWithoutRebuildingCaptureGrid() {
        float[] leaves=patches();Map<Integer,Float> config=config();
        MapTransform.Grid capture=MapTransform.sourcePlanningGrid(leaves,0,0,0,0,config);
        for(int axis=0;axis<2;axis++)for(int direction:new int[]{-1,1}) {
            float x=axis==0?direction*.2f:0,y=axis==1?direction*.2f:0;
            MapTransform.Grid moved=MapTransform.sourcePlanningGrid(leaves,0,0,0,x,y,0,config);
            for(int r=0;r<80;r++)for(int c=0;c<80;c++) {
                // Moving the observer right makes a fixed obstacle move left;
                // moving forward makes it move one row backwards in ego space.
                int sourceRow=r+(axis==1?direction:0),sourceCol=c+(axis==0?direction:0);
                sameCell("axis="+axis+" direction="+direction+" row="+r+" col="+c,
                        capture,sourceRow,sourceCol,moved,r,c);
            }
            assertEquals(capture.known,moved.known);
            int expectedRow=50-(axis==1?direction:0),expectedCol=45-(axis==0?direction:0);
            assertEquals(100,moved.displayCost[expectedRow*80+expectedCol]);
        }
    }

    private static VinsMono.Pose pose(double timestamp,double x,double y,double yaw) {
        double half=Math.toRadians(yaw)/2;
        return new VinsMono.Pose(new double[]{x,y,0,0,0,Math.sin(half),Math.cos(half),0,0,0,1,timestamp},
                new double[]{1,0,0,0,1,0,0,0,1},new double[3]);
    }

    private static SemanticSegmenter.Result result(FrameEvidence evidence,VinsMono.Pose pose) {
        return new SemanticSegmenter.Result(false,"projection contract",0,3210,"CONTRACT_NO_MODEL",
                0,0,0,Float.NaN,75,75,63,Float.NaN,0,new int[6400],pose).withEvidence(evidence);
    }

    @Test public void withMapPosePreservesAcquisitionEvidenceAndOriginalInferenceTiming() {
        FrameEvidence evidence=new FrameEvidence(1793843604.5902698,7,10000);
        VinsMono.Pose captured=pose(evidence.cameraSeconds,0,0,0);
        SemanticSegmenter.Result original=result(evidence,captured);
        InferenceTiming timing=new InferenceTiming(evidence,11000000,3210000000L,23000000,45000000);
        SemanticSegmenter.Result reprojected=original.withMapPose(pose(evidence.cameraSeconds+2,.2,0,90));
        assertSame(evidence,reprojected.evidence);assertSame(evidence,timing.evidence);
        assertSame(original.localCostGrid,reprojected.localCostGrid);
        assertSame(original.displayCostGrid,reprojected.displayCostGrid);
        assertEquals(original.inferenceMillis,reprojected.inferenceMillis);
        assertEquals(original.backend,reprojected.backend);
        assertEquals(3210,timing.modelMillis(),0);assertEquals(3289,timing.pipelineMillis(),0);
        assertEquals(11000000,timing.prepNanos);assertEquals(3210000000L,timing.modelNanos);
        assertEquals(23000000,timing.decodeNanos);assertEquals(45000000,timing.mapNanos);
        assertEquals(evidence.cameraSeconds,reprojected.evidence.cameraSeconds,0);
        assertEquals(7,reprojected.evidence.generation);assertEquals(10000,reprojected.evidence.captureElapsedMillis);
        assertFalse(reprojected.evidence.fresh(12000));assertFalse(timing.evidence.fresh(12000));
        assertEquals(2000,reprojected.evidence.ageMillis(12000));
    }

    @Test public void poseOnlyReprojectionCannotCountAsANewSemanticObservation() {
        ObservationRefreshTracker tracker=new ObservationRefreshTracker();
        FrameEvidence evidence=new FrameEvidence(100,7,10000);
        SemanticSegmenter.Result original=result(evidence,pose(100,0,0,0));
        assertTrue(tracker.observeDrawn(original.evidence,11000));
        for(int i=1;i<=20;i++) {
            SemanticSegmenter.Result reprojected=original.withMapPose(pose(100+i*.1,i*.01,0,i*5));
            assertFalse("pose reprojection "+i,tracker.observeDrawn(reprojected.evidence,11000+i*100));
        }
        assertEquals(1,tracker.count);assertEquals(-1,tracker.intervalMillis);
        // Even copying the same camera identity into a fresh Java object must
        // not increase observation count or pretend an old frame arrived anew.
        assertFalse(tracker.observeDrawn(new FrameEvidence(100,7,10000),14000));
        assertEquals(1,tracker.count);assertEquals(3000,tracker.maximumGapMillis(14000));
        assertTrue(tracker.observeDrawn(new FrameEvidence(101,7,13000),15000));
        assertEquals(2,tracker.count);assertEquals(4000,tracker.intervalMillis);
    }
}
