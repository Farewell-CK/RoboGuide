package com.elabrador.mobilenavigation;

import java.util.Arrays;
import java.util.List;
import org.junit.Test;
import static org.junit.Assert.*;

public class PathStabilityTest {
    @Test public void shortRejoinIsDrawnFromPersonAndLongRejoinIsRejected() {
        int[][] map=new int[30][30];
        List<float[]> path=Arrays.asList(new float[]{.4f,0},new float[]{.4f,.2f},new float[]{.4f,1});
        List<float[]> joined=LocalPlanner.connectStart(path,map,.2f,-2,-2,0,0,.6f);
        assertFalse(joined.isEmpty());
        assertArrayEquals(new float[]{0,0},joined.get(0),.0001f);
        assertArrayEquals(new float[]{.2f,0},joined.get(1),.0001f);
        assertEquals(joined.size(),LocalPlanner.safePrefix(joined,map,.2f,-2,-2,0,0).size());
        assertTrue(LocalPlanner.connectStart(path,map,.2f,-2,-2,-.4f,0,.6f).isEmpty());
        map[10][11]=100;
        assertTrue(LocalPlanner.connectStart(path,map,.2f,-2,-2,0,0,.6f).isEmpty());
    }

    @Test public void rejoinCannotCutAcrossDiagonalObstacleCorner() {
        int[][] map=new int[20][20];map[10][11]=100;
        assertTrue(LocalPlanner.connectStart(Arrays.asList(new float[]{.4f,.4f}),
                map,.2f,-2,-2,0,0,.6f).isEmpty());
    }

    @Test public void movementKeepsDisplayedPathConnectedAndLargeDeviationReplans() {
        int[][] grid=new int[80][80];for(int[] row:grid)Arrays.fill(row,20);
        LocalPlanner planner=new LocalPlanner();
        assertTrue(planner.plan(grid,.2f,-7.9f,-7.9f,0,0,0,1,pose(0,0,0)).success);
        LocalPlanner.PathResult small=planner.plan(grid,.2f,-7.9f,-7.9f,0,0,0,1,pose(.4,0,0));
        assertTrue(small.success);assertArrayEquals(new float[]{0,0},small.worldPath.get(0),.0001f);
        LocalPlanner.PathResult far=planner.plan(grid,.2f,-7.9f,-7.9f,0,0,0,1,pose(1.4,0,0));
        assertTrue(far.success);assertArrayEquals(new float[]{0,0},far.worldPath.get(0),.0001f);
        for(float[] point:far.worldPath)assertTrue("must replan near current forward axis",Math.abs(point[0])<.25f);
    }

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

    @Test public void hardObstacleBeyondThreeMetersDoesNotReplaceCommittedRoute() {
        int[][] grid=new int[80][80];
        LocalPlanner planner=new LocalPlanner();
        LocalPlanner.PathResult initial=planner.plan(grid,.2f,-7.9f,-7.9f,0,0,0,1);
        grid[60][40]=100; // About four metres along the committed route.
        LocalPlanner.PathResult held=planner.plan(grid,.2f,-7.9f,-7.9f,0,0,0,1);
        assertTrue(held.success);
        assertTrue(held.worldPath.size()<initial.worldPath.size());
        for(int i=0;i<held.worldPath.size();i++)
            assertArrayEquals(initial.worldPath.get(i),held.worldPath.get(i),.0001f);
    }

    @Test public void hardObstacleInsideThreeMetersImmediatelyReplacesRoute() {
        int[][] grid=new int[80][80];
        LocalPlanner planner=new LocalPlanner();
        LocalPlanner.PathResult initial=planner.plan(grid,.2f,-7.9f,-7.9f,0,0,0,1);
        grid[52][40]=100; // About 2.4 metres ahead.
        LocalPlanner.PathResult changed=planner.plan(grid,.2f,-7.9f,-7.9f,0,0,0,1);
        assertTrue(changed.success);
        boolean differs=initial.worldPath.size()!=changed.worldPath.size();
        for(int i=0;!differs && i<initial.worldPath.size();i++)
            differs=!Arrays.equals(initial.worldPath.get(i),changed.worldPath.get(i));
        assertTrue(differs);
        for(float[] point:changed.worldPath) {
            int row=LocalPlannerGrid.pythonRound((point[1]+7.9f)/.2f);
            int col=LocalPlannerGrid.pythonRound((point[0]+7.9f)/.2f);
            assertFalse(Env.isObstacleCost(grid[row][col]));
        }
    }

    @Test public void realTargetTurnOverridesRouteHold() {
        int[][] grid=new int[80][80];
        LocalPlanner planner=new LocalPlanner();
        assertTrue(planner.plan(grid,.2f,-7.9f,-7.9f,0,0,0,1).success);
        LocalPlanner.PathResult right=planner.plan(grid,.2f,-7.9f,-7.9f,0,0,1,0);
        assertTrue(right.success);
        assertTrue(right.steeringDegrees < -70f);
    }

    @Test public void pathSimplificationRemovesAvoidableBendsWithoutCrossingHardCells() {
        int[][] map=new int[12][12];
        List<int[]> zigzag=Arrays.asList(new int[]{1,1},new int[]{2,1},new int[]{3,2},
                new int[]{4,2},new int[]{5,3},new int[]{6,3},new int[]{7,4});
        List<int[]> smooth=LocalPlanner.simplifyPath(zigzag,map,map);
        assertArrayEquals(zigzag.get(0),smooth.get(0));
        assertArrayEquals(zigzag.get(zigzag.size()-1),smooth.get(smooth.size()-1));
        for(int[] cell:smooth) assertFalse(Env.isObstacleCost(map[cell[0]][cell[1]]));
        // Pixel stair-steps are rasterization, not executable turns. The smoothed
        // route must stay on one geometric line from its first to last point.
        for(int[] cell:smooth) {
            double cross=Math.abs(3.0*(cell[0]-1)-6.0*(cell[1]-1))/Math.hypot(6,3);
            assertTrue("cross-track cells="+cross,cross<=.45);
        }
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
