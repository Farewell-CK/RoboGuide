package com.elabrador.mobilenavigation;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import org.junit.Test;
import static org.junit.Assert.*;

/** Explicit upstream landmarks plus the Android world-filter-to-ego adapter. */
public class SourceCoordinateConventionTest {
    private static final int BUILDING=0x112233, LOW_COST=0x445566, MEDIUM_COST=0x778899;

    private static Map<Integer,Float> config() {
        Map<Integer,Float> config=new HashMap<>();
        config.put(BUILDING,1f);config.put(LOW_COST,.2f);config.put(MEDIUM_COST,.7f);
        return config;
    }

    private static float[] leaf(float x,float y,float z,int color) {
        // The native node is BGR relative to the semantic configuration.
        return new float[]{x,y,z,.9f,color&255,(color>>8)&255,(color>>16)&255,1};
    }

    @Test public void sixHeadingLandmarksKeepSourceRightForwardAndRowColumnConventions() {
        // Directly obtained from the unmodified Python function at upstream
        // c758acdca0e9d05bb6f52337daefdce9b2b9983e, with float32 inputs:
        // world loc=(10,-3,5), node=(11.13,-.83,3.87), semantic degree=1.
        // The world map has origin=(2.1,-10.9), res=.2 and the same yaw=0 cell.
        int[] degrees={0,90,-90,180,45,-45};
        int[][] expected={{50,45},{33,50},{45,28},{28,33},{43,51},{51,35}};
        for(int i=0;i<degrees.length;i++) {
            float yaw=(float)Math.toRadians(degrees[i]);
            MapTransform.Grid grid=MapTransform.octree2localprmapSource(
                    leaf(11.13f,-.83f,3.87f,BUILDING),10,-3,5,yaw,config());
            int cell=expected[i][0]*80+expected[i][1];
            assertEquals("one known landmark yaw="+degrees[i],1,grid.known);
            assertEquals("source row/column yaw="+degrees[i],100,grid.cost[cell]);
            assertEquals("fixed source height encoding",71,(int)grid.height[cell]);
            for(int j=0;j<6400;j++)if(j!=cell) {
                assertEquals("unexpected landmark yaw="+degrees[i]+" cell="+j,-1,grid.cost[j]);
                assertEquals("unknown height remains -1",-1,(int)grid.height[j]);
            }
        }
    }

    private static void patch(List<float[]> records,float cx,float cy,int color) {
        for(int row=-2;row<=2;row++)for(int col=-2;col<=2;col++) {
            records.add(leaf(10+cx+col*.2f,-3+cy+row*.2f,3.87f,color));
        }
    }

    private static float[] asymmetricPatches() {
        List<float[]> records=new ArrayList<>();
        patch(records,1.2f,2.2f,BUILDING);
        patch(records,-3.2f,1.4f,LOW_COST);
        patch(records,-1.8f,-2.6f,MEDIUM_COST);
        float[] leaves=new float[records.size()*8];
        for(int i=0;i<records.size();i++)System.arraycopy(records.get(i),0,leaves,i*8,8);
        return leaves;
    }

    @Test public void cardinalWorldFilteredAdapterRotatesTheSameAsSourceEgoProjection() throws Exception {
        // At cardinal rotations, filtering/dilation in the world grid commutes
        // with direct ego projection followed by the same source preprocessing
        // for these interior patches. Unknown padding has source planner cost 50.
        // Asymmetric positions and three costs expose a sign error or transpose.
        float[] leaves=asymmetricPatches();
        for(int degrees:new int[]{0,90,-90,180}) {
            float yaw=(float)Math.toRadians(degrees);
            MapTransform.Grid direct=MapTransform.octree2localprmapSource(leaves,10,-3,5,yaw,config());
            int[] median=MapTransform.sourceMedian(direct.cost);
            // Constant-height patches have gradients above 120 at their
            // unknown boundaries, so the source height mask is empty.
            for(int edge:MapTransform.sourceHeightEdges(direct.height))assertEquals(0,edge);
            int[][] filtered=new int[80][80];
            for(int row=0;row<80;row++)System.arraycopy(median,row*80,filtered[row],0,80);
            int[][] planned=LocalPlannerGrid.preprocessSource(filtered);
            int[] expected=new int[6400];
            for(int row=0;row<80;row++)System.arraycopy(planned[row],0,expected,row*80,80);
            MapTransform.Grid adapted=MapTransform.sourcePlanningGrid(leaves,10,-3,5,yaw,config());
            assertArrayEquals("world-to-ego cost yaw="+degrees,expected,adapted.cost);
            assertArrayEquals("world-to-ego height yaw="+degrees,direct.height,adapted.height);
            int known=0;for(int value:median)if(value>=0)known++;
            assertEquals("filtered known cells yaw="+degrees,known,adapted.known);
        }
    }

    @Test public void positiveForwardRowsAndPositiveRightColumnsReachTheCorrectPlannerBoundary() {
        // Camera coordinate (0,0), origin=(-7.9,-7.9), res=.2 -> (39.5,39.5).
        // Source planning uses Python round/ties-to-even, hence the other axis 40.
        LocalPlannerGrid.Target forward=LocalPlannerGrid.boundaryTarget(80,80,39.5f,39.5f,1,0);
        LocalPlannerGrid.Target right=LocalPlannerGrid.boundaryTarget(80,80,39.5f,39.5f,0,1);
        LocalPlannerGrid.Target backward=LocalPlannerGrid.boundaryTarget(80,80,39.5f,39.5f,-1,0);
        LocalPlannerGrid.Target left=LocalPlannerGrid.boundaryTarget(80,80,39.5f,39.5f,0,-1);
        assertEquals(79,forward.row);assertEquals(40,forward.col);
        assertEquals(40,right.row);assertEquals(79,right.col);
        assertEquals(0,backward.row);assertEquals(40,backward.col);
        assertEquals(40,left.row);assertEquals(0,left.col);
    }
}
