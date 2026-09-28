package com.elabrador.mobilenavigation;

import java.util.*;
import org.junit.Test;
import static org.junit.Assert.*;

public class PathCompletionTest {
    @Test public void sparseWaypointsDrawEveryIntermediateCellWithoutChangingInput(){
        int[][] map=new int[12][12];
        int[][] shown=LocalPlanner.visualize(map,Arrays.asList(new float[]{2,2},new float[]{2,8}),1,0,0);
        for(int row=2;row<=8;row++){assertEquals(127,shown[row][2]);assertEquals(0,map[row][2]);}
    }
    @Test public void rendererStopsAtUnsafeSegmentAndNeverRestartsBeyondObstacle(){
        int[][] map=new int[12][12];map[5][2]=100;
        int[][] shown=LocalPlanner.visualize(map,Arrays.asList(new float[]{2,2},new float[]{2,8},new float[]{2,9}),1,0,0);
        assertEquals(127,shown[2][2]);assertEquals(100,shown[5][2]);
        assertNotEquals(127,shown[8][2]);assertNotEquals(127,shown[9][2]);
        map[5][2]=0;map[2][3]=100;
        shown=LocalPlanner.visualize(map,Arrays.asList(new float[]{2,2},new float[]{4,4}),1,0,0);
        assertNotEquals(127,shown[4][4]);assertEquals(100,shown[2][3]);
    }
    private List<float[]> prefix(){
        List<float[]> path=new ArrayList<>();
        for(int y=2;y<=8;y++)path.add(new float[]{8,y});
        return path;
    }
    @Test public void extensionKeepsNearPathAndUsesAStarToGoAroundTailObstacle(){
        int[][] map=new int[24][24];for(int[] row:map)Arrays.fill(row,20);
        map[12][8]=100;
        List<float[]> prefix=prefix();
        List<float[]> extended=LocalPlanner.extendSafeTail(prefix,map,1,0,0,20,8);
        assertTrue(extended.size()>prefix.size());
        for(int i=0;i<prefix.size();i++)assertArrayEquals(prefix.get(i),extended.get(i),0);
        assertArrayEquals(new float[]{8,20},extended.get(extended.size()-1),0);
        assertEquals(extended.size(),LocalPlanner.safePrefix(extended,map,1,0,0,8,2).size());
        assertEquals(100,map[12][8]);assertEquals(20,map[2][8]);
    }
    @Test public void impossibleTailOrUnsafePrefixDoesNotProduceFakeContinuation(){
        int[][] map=new int[24][24];Arrays.fill(map[12],100);
        assertTrue(LocalPlanner.extendSafeTail(prefix(),map,1,0,0,20,8).isEmpty());
        map=new int[24][24];map[5][8]=100;
        assertTrue(LocalPlanner.extendSafeTail(prefix(),map,1,0,0,20,8).isEmpty());
    }
    private VinsMono.Pose pose(double y){
        return new VinsMono.Pose(new double[]{0,y,0,0,0,0,1,0,0,0,1,1},
                new double[]{1,0,0,0,1,0,0,0,1},new double[]{0,0,0});
    }
    @Test public void walkingForwardExtendsTailInsteadOfShowingEverShorterLine(){
        int[][] map=new int[80][80];for(int[] row:map)Arrays.fill(row,20);
        LocalPlanner p=new LocalPlanner();
        LocalPlanner.PathResult original=p.plan(map,.2f,-7.9f,-7.9f,0,0,0,1,pose(0));
        LocalPlanner.PathResult moved=p.plan(map,.2f,-7.9f,-7.9f,0,0,0,1,pose(2));
        assertTrue(original.success);assertTrue(moved.success);
        assertArrayEquals(new float[]{0,0},moved.worldPath.get(0),.0001f);
        assertTrue(moved.worldPath.get(moved.worldPath.size()-1)[1]>7);
        assertEquals(moved.worldPath.size(),LocalPlanner.safePrefix(moved.worldPath,
                LocalPlannerGrid.preprocess(map),.2f,-7.9f,-7.9f,0,0).size());
    }
    @Test public void hardObstacleCutIsNotFilledInByTailExtension(){
        int[][] map=new int[80][80];for(int[] row:map)Arrays.fill(row,20);
        LocalPlanner p=new LocalPlanner();
        p.plan(map,.2f,-7.9f,-7.9f,0,0,0,1,pose(0));
        map[65][40]=100;
        LocalPlanner.PathResult cut=p.plan(map,.2f,-7.9f,-7.9f,0,0,0,1,pose(0));
        assertTrue(cut.success);assertTrue(cut.worldPath.get(cut.worldPath.size()-1)[1]<5.1);
        assertEquals(100,cut.visualizationGrid[65][40]);
    }
}
