package com.elabrador.mobilenavigation;

import java.util.*;
import java.util.concurrent.atomic.AtomicLong;
import org.junit.Test;
import static org.junit.Assert.*;

public class SingleConnectorTest {
    private VinsMono.Pose pose(double x,double y){
        return new VinsMono.Pose(new double[]{x,y,0,0,0,0,1,0,0,0,1,1},
                new double[]{1,0,0,0,1,0,0,0,1},new double[]{0,0,0});
    }
    private int[][] map(){int[][] m=new int[80][80];for(int[] row:m)Arrays.fill(row,20);return m;}
    @Test public void freshSearchSavesMainRouteWithoutCameraConnector(){
        LocalPlanner planner=new LocalPlanner();
        LocalPlanner.PathResult output=planner.plan(map(),.2f,-7.9f,-7.9f,0,0,0,1,pose(0,0));
        assertTrue(output.success);assertArrayEquals(new float[]{0,0},output.worldPath.get(0),1e-5f);
        float[] main=planner.previousPath().get(0);
        assertTrue("camera origin must not be saved as a main waypoint",Math.hypot(main[0],main[1])>.1);
    }
    @Test public void repeatedSmallOffsetsAndTailSuccessNeverSaveConnectors(){
        AtomicLong clock=new AtomicLong();LocalPlanner planner=new LocalPlanner(clock::get);
        LocalPlanner.PathResult first=planner.plan(map(),.2f,-7.9f,-7.9f,0,0,0,1,pose(0,0));
        assertTrue(first.success);float worldX=planner.previousPath().get(0)[0];
        for(int step=1;step<=3;step++){
            double x=step%2==0?-.2:.35,y=step*1.2;
            clock.addAndGet(2_000_000_000L);
            int[][] map=map();
            LocalPlanner.PathResult output=planner.plan(map,.2f,-7.9f,-7.9f,0,0,0,1,pose(x,y));
            assertTrue(output.success);assertArrayEquals(new float[]{0,0},output.worldPath.get(0),1e-5f);
            assertTrue("tail really extended",output.worldPath.get(output.worldPath.size()-1)[1]>7);
            List<float[]> main=planner.previousPath();
            assertTrue("main route must remain separate from camera",Math.abs(main.get(0)[0])>.15);
            for(float[] p:main)if(p[1]<2)
                assertEquals("near main line must retain its world x at step "+step,worldX,p[0]+x,1e-4);
            assertEquals(output.worldPath.size(),LocalPlanner.safePrefix(output.worldPath,
                    LocalPlannerGrid.preprocess(map),.2f,-7.9f,-7.9f,0,0).size());
        }
    }
    @Test public void ordinaryHoldsDoNotAccumulateAlternatingSideConnections(){
        LocalPlanner planner=new LocalPlanner();planner.plan(map(),.2f,-7.9f,-7.9f,0,0,0,1,pose(0,0));
        float worldX=planner.previousPath().get(0)[0];
        for(int step=1;step<=25;step++){
            double x=step%2==0?-.2:.35,y=step*.02;
            LocalPlanner.PathResult output=planner.plan(map(),.2f,-7.9f,-7.9f,0,0,0,1,pose(x,y));
            assertTrue(output.success);
            for(float[] p:planner.previousPath())assertEquals(worldX,p[0]+x,1e-4);
        }
    }
    @Test public void segmentProjectionAvoidsWalkingBackToNearestSample(){
        List<float[]> main=Arrays.asList(new float[]{.3f,-.2f},new float[]{.3f,.6f},new float[]{.3f,2});
        List<float[]> trimmed=LocalPlanner.trimBeforeNearest(main,0,0);
        assertArrayEquals(new float[]{.3f,0},trimmed.get(0),1e-5f);
        assertArrayEquals(new float[]{.3f,-.2f},main.get(0),1e-5f);
        assertFalse(LocalPlanner.connectRetainedStart(trimmed,map(),.2f,-7.9f,-7.9f,0,0,.6f).isEmpty());
    }
    @Test public void backwardsHookAndHardConnectorAreRejected(){
        int[][] map=map();
        assertTrue(LocalPlanner.connectRetainedStart(Arrays.asList(new float[]{0,-.4f},new float[]{0,.4f}),
                map,.2f,-7.9f,-7.9f,0,0,.6f).isEmpty());
        List<float[]> main=Arrays.asList(new float[]{.5f,0},new float[]{.5f,2});
        map[40][41]=100;
        assertTrue(LocalPlanner.connectRetainedStart(main,map,.2f,-7.9f,-7.9f,0,0,.6f).isEmpty());
    }
}
