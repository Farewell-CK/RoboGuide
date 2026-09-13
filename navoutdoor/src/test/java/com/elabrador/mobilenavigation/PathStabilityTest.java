package com.elabrador.mobilenavigation;

import java.util.Arrays;
import java.util.List;
import org.junit.Test;
import static org.junit.Assert.*;

public class PathStabilityTest {
    private static VinsMono.Pose pose(double x, double y, double yaw) {
        double h=Math.toRadians(yaw)/2;
        return new VinsMono.Pose(new double[]{x,y,0,0,0,Math.sin(h),Math.cos(h),0,0,0,1,1},
                new double[]{1,0,0,0,1,0,0,0,1},new double[]{0,0,0});
    }

    @Test public void retainedPathFollowsWorldWhenCameraMovesAndRotates() {
        List<float[]> transformed=LocalPlanner.reprojectPath(
                Arrays.asList(new float[]{0,2},new float[]{0,4}),pose(0,0,0),pose(0,1,90));
        assertArrayEquals(new float[]{1,0},transformed.get(0),0.0001f);
        assertArrayEquals(new float[]{3,0},transformed.get(1),0.0001f);
        List<float[]> roundTrip=LocalPlanner.reprojectPath(transformed,pose(0,1,90),pose(0,0,0));
        assertArrayEquals(new float[]{0,2},roundTrip.get(0),0.0001f);
        assertTrue(LocalPlanner.reprojectPath(transformed,null,pose(0,0,0)).isEmpty());
    }

    @Test public void softHistoryPenaltyCannotInventHardObstacles() {
        int[][] grid=new int[80][80];
        for(int[] row:grid) Arrays.fill(row,85);
        LocalPlanner planner=new LocalPlanner();
        assertTrue(planner.plan(grid,.2f,-7.9f,-7.9f,0,0,0,1).success);
        LocalPlanner.PathResult turned=planner.plan(grid,.2f,-7.9f,-7.9f,0,0,1,0);
        assertTrue(turned.success);
        assertFalse(turned.blocked);
        assertEquals(0,turned.obstacleCount);
        assertTrue(turned.steeringDegrees < -70);
    }

    @Test public void repeatedStraightCorridorDoesNotSwitchSide() {
        LocalPlanner planner=new LocalPlanner();
        for(int frame=0;frame<30;frame++) {
            int[][] grid=new int[80][80];
            for(int row=0;row<80;row++) {
                grid[row][30]=100; grid[row][49]=100;
                for(int col=31;col<49;col++) grid[row][col]=(row+col+frame)%3;
            }
            LocalPlanner.PathResult result=planner.plan(grid,.2f,-7.9f,-7.9f,0,0,0,1,pose(0,frame*.02,0));
            assertTrue("frame "+frame,result.success);
            assertFalse("frame "+frame,result.blocked);
            assertTrue("steering="+result.steeringDegrees,Math.abs(result.steeringDegrees)<10);
            for(float[] point:result.worldPath) assertTrue(Math.abs(point[0])<.25f);
            assertTrue(result.worldPath.get(0)[1]<.2f);
            assertTrue(result.worldPath.get(result.worldPath.size()-1)[1]>7);
        }
    }

    @Test public void newHardWallImmediatelyInvalidatesOldRoute() {
        int[][] grid=new int[80][80];
        LocalPlanner planner=new LocalPlanner();
        assertTrue(planner.plan(grid,.2f,-7.9f,-7.9f,0,0,0,1).success);
        Arrays.fill(grid[42],100);
        LocalPlanner.PathResult result=planner.plan(grid,.2f,-7.9f,-7.9f,0,0,0,1);
        assertFalse(result.success);
        assertEquals("停止",NavigationCue.fromPlan(result.planned,result.success,result.blocked,result.steeringDegrees));
    }

    @Test public void detourStaysOutOfHardCellsAndDoesNotReturnFalseStop() {
        int[][] grid=new int[80][80];
        for(int row=45;row<60;row++) for(int col=39;col<55;col++) grid[row][col]=100;
        LocalPlanner.PathResult result=new LocalPlanner().plan(grid,.2f,-7.9f,-7.9f,0,0,0,1);
        assertTrue(result.success);
        assertFalse(result.blocked);
        assertTrue(result.steeringDegrees>10);
        for(float[] point:result.worldPath) {
            int row=LocalPlannerGrid.pythonRound((point[1]+7.9f)/.2f);
            int col=LocalPlannerGrid.pythonRound((point[0]+7.9f)/.2f);
            assertFalse(Env.isObstacleCost(grid[row][col]));
        }
    }

    @Test public void semanticResultKeepsProjectionPoseIdentity() {
        VinsMono.Pose mapPose=pose(2,3,40);
        SemanticSegmenter.Result result=SemanticSegmenter.Result.waiting().withMapPose(mapPose);
        assertSame(mapPose,result.mapPose);
        assertNull(SemanticSegmenter.Result.waiting().mapPose);
    }
}
